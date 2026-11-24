# -*- coding: utf-8 -*-
"""按 data 里实际有的 xls 重建 config.xlsx，保留同名行的用户设置。

参照 Raman/fill_stack.bat：文件增删后双击 fill_config.bat 让表格跟数据对齐。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nano_config import (CONFIG_NAME, GLOBAL_DOC, GLOBAL_KEYS, KEYS,
                         build_rows, excel_lock_path, read_workbook,
                         write_workbook)


def main(argv=None):
    root = Path(__file__).resolve().parent
    path = root / CONFIG_NAME
    if excel_lock_path(path).exists():
        print("ERROR: config.xlsx 正在 Excel 中打开，无法写回。"
              "请保存并关闭 Excel 后重新双击 fill_config.bat。", file=sys.stderr)
        return 1
    try:
        old_settings, old_globals = read_workbook(path) if path.is_file() else ({}, {})
        # read_workbook 返回按列名索引的字典；build_rows 需要按 KEYS 排列的行
        old = {k: [v.get(key) or "" for key in KEYS]
               for k, v in old_settings.items()}
    except (PermissionError, OSError) as exc:
        print(f"ERROR: 读取 config.xlsx 失败：{exc}", file=sys.stderr)
        return 1
    try:
        rows, added, dropped = build_rows(root / "data", old)
        grows = [[k, old_globals.get(k, ""), GLOBAL_DOC[k]] for k in GLOBAL_KEYS]
        write_workbook(path, rows, grows)
    except PermissionError:
        print("ERROR: config.xlsx 正在 Excel 中打开，无法写回。"
              "请保存并关闭 Excel 后重新双击 fill_config.bat。", file=sys.stderr)
        return 1
    print(f"data 里有 {len(rows)} 个 xls，config.xlsx 现在 {len(rows)} 行")
    if added:
        print("新加 %d 行:" % len(added))
        for n in added:
            print("  " + n)
    if dropped:
        print("data 里已不存在，删掉 %d 行:" % len(dropped))
        for n in dropped:
            print("  " + n)
    if not added and not dropped:
        print("表格本来就和 data 一致（已按原样重写）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
