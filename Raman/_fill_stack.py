# -*- coding: utf-8 -*-
"""把 data 里所有 txt 补进 stack.xlsx。

已有的行原样保留（label / normalize / peaks 等改动不会丢），只追加缺的文件。
stack.xlsx 不存在时直接按 data 里的全部文件新建。
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _make_stack_xlsx import HEADERS, KEYS, norm_header, write_workbook  # noqa: E402


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


def main():
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

    have = {os.path.basename(n if n.lower().endswith(".txt") else n + ".txt")
            for n, _ in existing}
    # 新行的 normalize 跟着已有行走，避免混填报错；没有已有行时用 1
    norm = 1
    for _, row in existing:
        v = str(row[KEYS.index("normalize")] or "").strip().lower()
        if v:
            norm = 0 if v in ("0", "0.0", "n", "no", "false", "否") else 1
            break

    added = []
    for f in files:
        if f in have:
            continue
        row = [None] * len(KEYS)
        row[KEYS.index("file")] = f
        row[KEYS.index("normalize")] = norm
        row[KEYS.index("peaks")] = 1
        added.append(row)

    rows = [row for _, row in existing] + added
    try:
        write_workbook(stack, rows)
    except PermissionError:
        print("ERROR: stack.xlsx 正被 Excel 打开，先关掉再运行", file=sys.stderr)
        return 1

    print("data 里有 %d 个 txt，stack.xlsx 现在 %d 行" % (len(files), len(rows)))
    if added:
        print("新加 %d 行:" % len(added))
        for row in added:
            print("  " + row[KEYS.index("file")])
    else:
        print("没有缺失的文件，表格未变（已按原样重写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
