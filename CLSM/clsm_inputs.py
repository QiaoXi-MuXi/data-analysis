# -*- coding: utf-8 -*-
"""TXT/LSM 输入发现、去重及有明确标定边界的 LSM 高度读取。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from inspect_zen_lsm import LsmFormatError, ZenLsm


def natural_key(name):
    """文件名自然排序：2-1 排在 10-1 前，数字段按整数比较。"""
    text = str(name)
    key = []
    for part in re.split(r"(\d+)", text):
        if part.isdigit():
            key.append((0, int(part), len(part)))
        else:
            key.append((1, part.casefold()))
    return (key, text.casefold())


def discover_inputs(directory, input_format="auto", samples=None):
    """沿用根目录优先、无输入时递归的规则；同目录同名 TXT 优先。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"输入目录不存在：{directory}")
    suffixes = {".txt", ".lsm"} if input_format == "auto" else {"." + input_format}
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.casefold() in suffixes]
    if not files:
        files = [p for p in directory.rglob("*") if p.is_file() and p.suffix.casefold() in suffixes]
    excluded = []
    if samples is not None:
        requested = {str(name).casefold() for name in samples}
        missing = requested - {p.stem.casefold() for p in files}
        if missing:
            raise ValueError("--samples 未找到样品名（不含扩展名）：" + ", ".join(sorted(missing, key=natural_key)))
        excluded = [(p, "本次 --samples 未选择该样品") for p in files if p.stem.casefold() not in requested]
        files = [p for p in files if p.stem.casefold() in requested]
    groups = {}
    for path in files:
        key = (str(path.parent.resolve()).casefold(), path.stem.casefold())
        groups.setdefault(key, []).append(path)
    selected, ignored = [], excluded
    for paths in groups.values():
        paths.sort(key=lambda p: (p.suffix.casefold() != ".txt", natural_key(p.name)))
        selected.append(paths[0])
        ignored.extend((p, f"同名输入优先使用 {paths[0].name}，避免重复统计") for p in paths[1:])
    # 输出按样品名命名；不同子目录的同名样品会覆盖图像，须在任何写入前拒绝。
    seen = {}
    for path in selected:
        key = path.stem.casefold()
        if key in seen:
            raise ValueError(f"不同目录存在同名样品，输出会冲突：{seen[key]} 与 {path}；请分目录运行")
        seen[key] = path
    return (sorted(selected, key=lambda p: natural_key(p.name)),
            sorted(ignored, key=lambda item: natural_key(item[0])))


def add_input_arguments(parser):
    parser.add_argument("--strict-inputs", action="store_true",
                        help="严格模式：任一LSM预检查失败即停止；默认记录原因并跳过不可处理的LSM")
    parser.add_argument("--samples", nargs="+", default=None,
                        help="仅分析指定样品名（不含扩展名），例如 --samples 3 4；仍优先同名TXT")
    parser.add_argument("--input-format", choices=("auto", "txt", "lsm"), default="auto",
                        help="默认识别TXT/LSM，同目录同名优先TXT；可指定只读某种格式")
    parser.add_argument("--lsm-height-mode", choices=("require-calibration", "metadata-pending"),
                        default="require-calibration",
                        help="LSM默认要求外部高度标定；metadata-pending按已识别元数据作待核验试算")
    parser.add_argument("--lsm-height-scale-um", type=float, default=None,
                        help="外部确认的有符号高度比例：Z(µm)=存储值*比例+偏移；负号表示反向")
    parser.add_argument("--lsm-height-offset-um", type=float, default=0.0,
                        help="与外部高度比例配套的Z偏移(µm)，默认0")


def lsm_options(args):
    return dict(lsm_height_mode=args.lsm_height_mode,
                lsm_height_scale_um=args.lsm_height_scale_um,
                lsm_height_offset_um=args.lsm_height_offset_um)


def _registered_calibration(path, data, *, allow_unregistered_name=False):
    """仅将本项目已记录的用户确认应用于校验值一致的文件。"""
    config_path = Path(__file__).resolve().parent / "lsm_height_calibration.json"
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if config.get("version") != 1 or not isinstance(config.get("files"), list):
            raise ValueError("配置版本或files列表无效")
        digest = hashlib.sha256(data).hexdigest()
        matches = [item for item in config["files"] if item["input_sha256"] == digest]
        if len(matches) > 1:
            raise ValueError("同一校验值存在重复换算记录")
        if not matches:
            if (not allow_unregistered_name and
                    any(item["input_name"].casefold() == Path(path).name.casefold() for item in config["files"])):
                raise ValueError("同名LSM的SHA256已变化，不能复用旧换算，请重新确认")
            return None
        item = matches[0]
        if item["unit"] != "um" or item["confirmation"] != "user-confirmed":
            raise ValueError("换算尚未由用户确认或目标单位不是um")
        if not isinstance(item["basis"], str) or not item["basis"].strip():
            raise ValueError("缺少换算确认依据")
        scale, offset = item["scale_um"], item["offset_um"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (scale, offset)):
            raise ValueError("比例与偏移必须是数值")
        if not np.isfinite(scale) or scale == 0 or not np.isfinite(offset):
            raise ValueError("比例必须有限且非零，偏移必须有限")
        return dict(item, config_file=config_path.name)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LsmFormatError(f"LSM高度换算记录无效：{exc}") from exc


def read_lsm_height(path, *, lsm_height_mode="require-calibration",
                    lsm_height_scale_um=None, lsm_height_offset_um=0.0):
    """只用唯一的浮点 topography 记录，不把强度、缩略图或未知掩码当高度。"""
    data = Path(path).read_bytes()
    records = ZenLsm(data).read()
    candidates = [(i, meta, array) for i, (meta, array) in enumerate(records)
                  if meta["type_of_data"] == 3 and meta["data_type"] == 5
                  and meta["shape_zyx"][0] == 1]
    if not candidates:
        layers = records[0][0]["shape_zyx"][0]
        raise LsmFormatError(
            f"已读取主记录{layers}层原始扫描，但当前读取器未找到可用的嵌入浮点高度图。"
            "程序尚未实现与ZEN核对的原始堆栈高度重建，此提示不代表原始扫描丢失或文件损坏；"
            "目前可用ZEN重建并保存Topography，或导出同名高度TXT放入data。")
    if len(candidates) > 1:
        raise LsmFormatError(f"发现{len(candidates)}份浮点高度图，无法唯一选择；请将目标高度矩阵导出为同名TXT")
    index, meta, array = candidates[0]
    stored = array[0].astype(float)
    factor = meta.get("channel_factors_raw")
    calibration = None
    if lsm_height_scale_um is not None:
        scale, offset = float(lsm_height_scale_um), float(lsm_height_offset_um)
        status = "外部指定换算（程序未独立核验）"
        basis = "用户参数指定 Z_um = stored * scale_um + offset_um"
    elif (calibration := _registered_calibration(
            path, data, allow_unregistered_name=(lsm_height_mode == "metadata-pending"))) is not None:
        scale, offset = calibration["scale_um"], calibration["offset_um"]
        status = "用户确认换算（非独立仪器标定）"
        basis = calibration["basis"] + "；Z_um = stored * scale_um + offset_um"
    elif lsm_height_mode == "metadata-pending":
        # 此规则仅匹配本批文件的结构，不宣称已解释所有 ZEN 单位代码。
        raw = records[0][0]
        extent = (raw["shape_zyx"][0] - 1) * raw["voxel_size_xyz_um"][2]
        if (not factor or factor["unit_code"] != 2 or factor["offset"] != 0
                or not np.isfinite(factor["factor"]) or factor["factor"] <= 0
                or not np.isclose(factor["factor"] * 1e6, extent, rtol=1e-6, atol=1e-6)
                or meta["shape_zyx"][1:] != raw["shape_zyx"][1:]
                or not np.allclose(meta["voxel_size_xyz_um"][:2], raw["voxel_size_xyz_um"][:2])
                or not np.isfinite(stored).any()
                or np.nanmin(stored) < 0 or np.nanmax(stored) > 1):
            raise LsmFormatError("LSM不符合已识别的归一化高度元数据，拒绝猜测换算")
        scale, offset = -factor["factor"] * 1e6, factor["factor"] * 1e6
        status = "待ZEN核验（元数据假设）"
        basis = "试算假设：factor按米解释，方向反转，Z_um=(1-stored)*factor*1e6；单位代码及方向尚未独立核验"
    else:
        raise LsmFormatError(
            "已识别LSM高度图，但高度单位/方向尚未确认。请用ZEN确认后指定 --lsm-height-scale-um（有符号比例），"
            "或显式使用 --lsm-height-mode metadata-pending 生成标为待核验的试算。该文件未通过高度换算检查。")
    if not np.isfinite(scale) or scale == 0 or not np.isfinite(offset):
        raise LsmFormatError("LSM高度比例必须有限且非零，偏移必须有限")
    z = stored * scale + offset
    missing = ~np.isfinite(z)
    dx, dy, _ = meta["voxel_size_xyz_um"]
    x = np.arange(z.shape[1], dtype=float) * dx
    source = dict(format="lsm", height_status=status, height_basis=basis,
                  record_index=index, record_base_offset=meta["base_offset"],
                  voxel_size_xyz_um=meta["voxel_size_xyz_um"], channel_factors_raw=factor,
                  height_scale_um=scale, height_offset_um=offset, dy_source="LSM元数据",
                  missing_policy="仅非有限值视为缺失；有限零高度保留；未知辅助记录不充当有效性掩码",
                  acquisition_validity="尚未核实ZEN失测/饱和像素掩码，几何质量筛查不能替代仪器有效性判断")
    if calibration is not None:
        source["height_calibration"] = calibration
    return x, z, dy, missing, source
