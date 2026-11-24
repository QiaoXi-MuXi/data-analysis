# -*- coding: utf-8 -*-
"""只读检查已验证的 ZEN LSM 子格式；不替换槽宽分析输入，不推断缺失单位。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

import numpy as np


class LsmFormatError(ValueError):
    """文件损坏或超出本检查器支持的格式范围。"""


class ZenLsm:
    """支持小端经典 TIFF、无压缩条带、单通道单时刻 Z 堆栈和嵌入记录。"""

    _types = {1: 'u1', 2: 'u1', 3: '<u2', 4: '<u4', 5: '<u4',
              6: 'i1', 7: 'u1', 8: '<i2', 9: '<i4', 10: '<i4',
              11: '<f4', 12: '<f8'}

    def __init__(self, data: bytes):
        self.data = data

    def take(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise LsmFormatError(f'偏移越界：offset={offset}, size={size}')
        return self.data[offset:offset + size]

    def unpack(self, fmt: str, offset: int):
        return struct.unpack(fmt, self.take(offset, struct.calcsize(fmt)))

    def ifd(self, base: int, offset: int):
        pos = base + offset
        count, = self.unpack('<H', pos)
        self.take(pos + 2, count * 12 + 4)
        tags = {}
        for i in range(count):
            entry = pos + 2 + 12 * i
            tag, kind, n, ptr = self.unpack('<HHII', entry)
            if kind not in self._types or tag in tags:
                raise LsmFormatError('不支持的 TIFF 字段类型或重复标签')
            dtype = np.dtype(self._types[kind])
            # RATIONAL 占两个整数；本检查器只保留其原始分子、分母。
            size = n * dtype.itemsize * (2 if kind in (5, 10) else 1)
            raw = self.take(entry + 8 if size <= 4 else base + ptr, size)
            tags[tag] = np.frombuffer(raw, dtype=dtype)
        next_ifd, = self.unpack('<I', pos + 2 + 12 * count)
        return tags, next_ifd

    @staticmethod
    def number(tags, key, default=None):
        if key not in tags:
            if default is None:
                raise LsmFormatError(f'缺少 TIFF 标签 {key}')
            return default
        if tags[key].size != 1:
            raise LsmFormatError(f'TIFF 标签 {key} 不是标量')
        return int(tags[key][0])

    def metadata(self, tags, base):
        if 34412 not in tags:
            raise LsmFormatError('缺少 CZ_LSMINFO')
        raw = tags[34412].tobytes()
        if len(raw) < 236:
            raise LsmFormatError('CZ_LSMINFO 太短')
        magic, size = struct.unpack_from('<II', raw)
        if magic not in (50350412, 67127628) or size > len(raw):
            raise LsmFormatError('CZ_LSMINFO 标识或长度错误')
        nx, ny, nz, nc, nt, dtype = struct.unpack_from('<6i', raw, 8)
        scan_type, = struct.unpack_from('<H', raw, 88)
        if nc != 1 or nt != 1 or scan_type != 0 or min(nx, ny, nz) <= 0:
            raise LsmFormatError('目前只支持正常 XYZ 扫描、单通道和单时刻')
        voxel = np.array(struct.unpack_from('<3d', raw, 40)) * 1e6
        if not np.all(np.isfinite(voxel)) or np.any(voxel <= 0):
            raise LsmFormatError('空间标定缺失或无效')
        meta = dict(base_offset=base, shape_zyx=[nz, ny, nx], channels=nc,
                    times=nt, data_type=dtype, scan_type=scan_type,
                    type_of_data=struct.unpack_from('<I', raw, 92)[0],
                    voxel_size_xyz_um=voxel.tolist(),
                    next_recording_absolute=struct.unpack_from('<I', raw, 148)[0])
        factor_offset, = struct.unpack_from('<I', raw, 208)
        if factor_offset:
            block_size, = self.unpack('<I', base + factor_offset)
            if block_size < 24:
                raise LsmFormatError('ChannelFactors 长度无效')
            self.take(base + factor_offset, block_size)
            factor, offset, unit_code = self.unpack('<ddI', base + factor_offset + 4)
            meta['channel_factors_raw'] = dict(factor=factor, offset=offset,
                                              unit_code=unit_code, block_size=block_size)
        return meta

    def plane(self, tags, base, meta):
        number = self.number
        if number(tags, 259, 1) != 1:
            raise LsmFormatError('目前不支持压缩条带')
        if number(tags, 277, 1) != 1 or number(tags, 317, 1) != 1:
            raise LsmFormatError('目前只支持单分量、无预测器图像')
        if number(tags, 274, 1) != 1 or 322 in tags or 324 in tags:
            raise LsmFormatError('目前不支持旋转存储或 TIFF 瓦片')
        dtype = {1: np.dtype('u1'), 2: np.dtype('<u2'), 5: np.dtype('<f4')}.get(meta['data_type'])
        if dtype is None or number(tags, 258) != dtype.itemsize * 8:
            raise LsmFormatError('不支持的数据类型或位深与 LSM 元数据冲突')
        # ZEN 的浮点高度图不一定写 SampleFormat；依据 LSM DataType=5。
        sample_format = number(tags, 339, 3 if dtype.kind == 'f' else 1)
        if sample_format != (3 if dtype.kind == 'f' else 1):
            raise LsmFormatError('TIFF SampleFormat 与 LSM DataType 冲突')
        nx, ny = number(tags, 256), number(tags, 257)
        if [ny, nx] != meta['shape_zyx'][1:]:
            raise LsmFormatError('图像与记录尺寸不一致')
        offsets, counts = tags.get(273), tags.get(279)
        if offsets is None or counts is None or not len(offsets) or len(offsets) != len(counts):
            raise LsmFormatError('条带索引不完整')
        expected = nx * ny * dtype.itemsize
        if sum(int(n) for n in counts) != expected:
            raise LsmFormatError('条带长度与图像尺寸不一致')
        payload = b''.join(self.take(base + int(o), int(n)) for o, n in zip(offsets, counts))
        return np.frombuffer(payload, dtype=dtype).reshape(ny, nx).copy()

    def read(self):
        base = 0
        visited_records = set()
        records = []
        while True:
            if base in visited_records or len(visited_records) >= 64:
                raise LsmFormatError('记录链接循环或记录过多')
            visited_records.add(base)
            if self.take(base, 4) != b'II*\x00':
                raise LsmFormatError('目前只支持小端经典 TIFF 的嵌入记录')
            first, = self.unpack('<I', base + 4)
            tags, _ = self.ifd(base, first)
            meta = self.metadata(tags, base)
            pages, thumbs, visited = [], 0, set()
            offset = first
            while offset:
                if offset in visited or len(visited) >= 10000:
                    raise LsmFormatError('IFD 链接循环或页数过多')
                visited.add(offset)
                tags, offset = self.ifd(base, offset)
                subfile = self.number(tags, 254, 0)
                if subfile & 1:
                    thumbs += 1
                elif subfile == 0:
                    pages.append(self.plane(tags, base, meta))
                else:
                    raise LsmFormatError('不支持的子图类型')
            if len(pages) != meta['shape_zyx'][0]:
                raise LsmFormatError('实际图像层数与元数据不一致')
            array = np.stack(pages)
            meta.update(thumbnails_skipped=thumbs, dtype=str(array.dtype),
                        minimum=float(np.min(array)), maximum=float(np.max(array)),
                        zero_count=int(np.count_nonzero(array == 0)),
                        nonfinite_count=int(np.count_nonzero(~np.isfinite(array))))
            records.append((meta, array))
            base = meta['next_recording_absolute']
            if not base:
                return records


def read_export(path, shape):
    """只接受完整矩阵；显式处理本次 ZEN 文本末尾的 NUL，不筛除零高度。"""
    lines = path.read_text(encoding='utf-8-sig').rstrip('\x00\r\n').splitlines()
    x = np.array([float(v.removeprefix('X:').strip()) for v in lines[0].split('\t')[1:]])
    z = np.array([[float(v) for v in line.split('\t')[1:]] for line in lines[1:]])
    if z.shape != tuple(shape) or len(x) != shape[1]:
        raise LsmFormatError('TXT 尺寸与 LSM 不一致')
    if not np.all(np.isfinite(z)) or not np.all(np.isfinite(x)):
        raise LsmFormatError('TXT 包含非有限值')
    return x, z


def inspect(source, out, txt=None):
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    records = ZenLsm(data).read()
    report = dict(source=str(source.resolve()), source_sha256=digest,
                  records=[m for m, _ in records],
                  limitations=['仅验证本文件结构；不是通用 LSM 转换器',
                               '高度图按存储值导出，尚未确认 ChannelFactors 的单位代码和 Z 方向约定',
                               '没有重建、平滑、去倾斜、删点或更新槽宽槽深 Excel'])
    arrays = {f'record_{i}_stored': a for i, (_, a) in enumerate(records)}
    dx, dy, _ = records[0][0]['voxel_size_xyz_um']
    shape = records[0][0]['shape_zyx'][1:]
    arrays.update(x_um=np.arange(shape[1]) * dx, y_um=np.arange(shape[0]) * dy)
    raw = records[0][1]
    if raw.dtype == np.uint8:
        sat = raw == 255
        report['raw_stack_255_fraction'] = float(np.mean(sat))
        report['raw_stack_pixels_with_any_255_fraction'] = float(np.mean(np.any(sat, axis=0)))
    topo = [a[0] for m, a in records if m['type_of_data'] == 3]
    if len(topo) != 1:
        raise LsmFormatError('需要恰好一份明确标注 TypeOfData=3 的高度图')
    if txt:
        x, z = read_export(txt, shape)
        arrays.update(txt_x_um=x, txt_z_um=z)
        report['txt_comparison'] = dict(path=str(txt.resolve()), sha256=hashlib.sha256(txt.read_bytes()).hexdigest(),
                                       shape=list(z.shape), minimum_um=float(z.min()), maximum_um=float(z.max()),
                                       zero_count=int(np.count_nonzero(z == 0)),
                                       stored_height_values_equal_txt=bool(np.array_equal(topo[0], z)),
                                       maximum_x_label_rounding_error_um=float(np.max(np.abs(x-arrays['x_um']))),
                                       warning='尚未确认 TXT 与嵌入高度图之间的处理关系，不拟合或强行变换替代原输入')
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise RuntimeError('读取期间源文件发生变化')
    out.mkdir(parents=True, exist_ok=False)
    report['reader_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    stem = source.stem
    with (out / f'{stem}_decoded.npz').open('xb') as f:
        np.savez_compressed(f, **arrays)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3 if txt else 2, figsize=(15 if txt else 10, 4.5), layout='constrained')
    panels = [(raw.max(axis=0), 'Original stack: maximum intensity', 'Intensity [stored units]', 'gray'),
              (topo[0], 'Embedded height map: stored values', 'Stored float value (not converted to um)', 'viridis')]
    if txt:
        panels.append((z, 'Existing TXT export', 'Exported Z [um]', 'viridis'))
    extent = [-dx/2, (shape[1]-.5)*dx, (shape[0]-.5)*dy, -dy/2]
    for ax, (a, title, label, cmap) in zip(axes, panels):
        im = ax.imshow(a, extent=extent, cmap=cmap, interpolation='nearest')
        ax.set(title=title, xlabel='X [um]', ylabel='Y [um]')
        fig.colorbar(im, ax=ax, label=label, shrink=.8)
    fig.savefig(out / f'{stem}_source_check.png', dpi=180)
    plt.close(fig)
    for file in out.iterdir():
        report.setdefault('output_sha256', {})[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    with (out / f'{stem}_source_check.json').open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True, help='必须使用尚不存在的独立输出目录')
    parser.add_argument('--compare-txt', type=Path)
    args = parser.parse_args()
    report = inspect(args.source, args.out, args.compare_txt)
    print(json.dumps(dict(record_count=len(report['records']), records=report['records'],
                          txt_comparison=report.get('txt_comparison')), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
