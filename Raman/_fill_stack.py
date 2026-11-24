# -*- coding: utf-8 -*-
"""按 data 里实际有的 txt 重建 stack.xlsx。

行顺序跟 data 一致；data 里已删掉的文件对应行会被丢掉；
同名文件原有的 label / normalize / peaks 等改动保留。
stack.xlsx 不存在时直接按 data 里的全部文件新建。

峰高阈值(height)默认按每条谱自动重算: max(2.5σ, 1.1%×最强峰), 兼顾弱峰
与杂峰抑制。带 --keep-height 时保留原表手填值不动。
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _make_stack_xlsx import HEADERS, KEYS, norm_header, write_workbook  # noqa: E402
from plot_raman import auto_threshold, load_txt  # noqa: E402


def read_existing(path):
    """返回 [(file, 8 格行)]，按文件原顺序。

    按表头名字取值，所以旧表的 file 表头、新表的中文表头、以及列顺序被
    调过，都能正确搬过来。
    """
    if not os.path.exists(path):
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        rows = []
        pos = None
        for raw in wb.worksheets[0].iter_rows(values_only=True):
            cells = [None if c is None else c for c in raw]
            if not any(str(c or "").strip() for c in cells):
                continue
            if pos is None:
                header = [norm_header(c) for c in cells]
                pos = {k: header.index(k) for k in KEYS if k in header}
                continue
            if "file" not in pos or pos["file"] >= len(cells):
                continue
            name = str(cells[pos["file"]] or "").strip()
            if not name:
                continue
            out = [cells[pos[k]] if k in pos and pos[k] < len(cells) else None
                   for k in KEYS]
            rows.append((name, out))
        return rows
    finally:
        wb.close()


def file_key(name):
    """文件名归一成小写 .txt 结尾，用来对表里的行和 data 里的文件。"""
    name = os.path.basename(str(name or "").strip())
    return name.lower() if name.lower().endswith(".txt") else (name + ".txt").lower()


def auto_height(name, data_dir):
    """读一条谱算自适应阈值; 读失败返回 None。"""
    path = os.path.join(data_dir, file_key(name))
    try:
        x, y = load_txt(path)
        thr, _sig, _hmax = auto_threshold(x, y)
        return round(float(thr), 2)
    except Exception as e:
        print("  警告: %s 读数据失败, 保留原阈值 (%s)" % (name, e), file=sys.stderr)
        return None


def main(argv=None):
    keep_height = False
    if argv is None:
        argv = sys.argv[1:]
    for a in argv:
        if a == "--keep-height":
            keep_height = True
        else:
            print("未知参数: %s" % a, file=sys.stderr)
            return 1
    stack = os.path.join(HERE, "stack.xlsx")
    data_dir = os.path.join(HERE, "data")

    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(data_dir, "*.txt")))
    if not files:
        print("ERROR: data 文件夹里没有 txt 文件", file=sys.stderr)
        return 1

    try:
        existing = read_existing(stack)
    except PermissionError:
        print("ERROR: stack.xlsx 正被 Excel 打开，先关掉再运行", file=sys.stderr)
        return 1

    old = {file_key(n): (n, row) for n, row in existing}
    # 新行的 normalize 跟着已有行走，避免混填报错；没有已有行时用 1
    norm = 1
    for _, row in existing:
        v = str(row[KEYS.index("normalize")] or "").strip().lower()
        if v:
            norm = 0 if v in ("0", "0.0", "n", "no", "false", "否") else 1
            break

    rows = []
    added = []
    for f in files:
        hit = old.pop(file_key(f), None)
        if hit:
            rows.append(hit[1])
            continue
        row = [None] * len(KEYS)
        row[KEYS.index("file")] = f
        row[KEYS.index("normalize")] = norm
        row[KEYS.index("peaks")] = 1
        rows.append(row)
        added.append(row)

    dropped = [n for n, _ in old.values()]

    # 自适应峰高阈值: 默认每条谱重算, --keep-height 保留原表手填值
    hi = KEYS.index("height")
    fi = KEYS.index("file")
    if not keep_height:
        for row in rows:
            thr = auto_height(row[fi], data_dir)
            if thr is not None:
                row[hi] = thr

    try:
        write_workbook(stack, rows)
    except PermissionError:
        print("ERROR: stack.xlsx 正被 Excel 打开，先关掉再运行", file=sys.stderr)
        return 1

    print("data 里有 %d 个 txt，stack.xlsx 现在 %d 行" % (len(files), len(rows)))
    if keep_height:
        print("峰高阈值: 保留原表手填值 (--keep-height)")
    else:
        print("峰高阈值: 已按每条谱自适应重算 (max(2.5σ, 1.1%×最强峰))")
    if added:
        print("新加 %d 行:" % len(added))
        for row in added:
            print("  " + row[KEYS.index("file")])
    if dropped:
        print("data 里已不存在，删掉 %d 行:" % len(dropped))
        for n in dropped:
            print("  " + n)
    if not added and not dropped:
        print("表格本来就和 data 一致（已按原样重写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
