# -*- coding: utf-8 -*-
"""一次性核对：代码读的列、stack.xlsx 表头、README 表格三者是否一致。"""
import os
import re
import sys

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
# 便携 Python 的 sys.path 不含脚本目录, 必须自己加
sys.path.insert(0, HERE)
from plot_raman import STACK_HEADERS, STACK_KEYS, norm_header  # noqa: E402

code = open(os.path.join(HERE, "plot_raman.py"), encoding="utf-8").read()
used = set(re.findall(r'get\("(\w+)"\)', code)) | {"xmin", "xmax"}

wb = openpyxl.load_workbook(os.path.join(HERE, "stack.xlsx"), data_only=True)
hdr = [c.value for c in wb.worksheets[0][1]]

readme = open(os.path.join(HERE, "README.md"), encoding="utf-8").read()
row = [ln for ln in readme.splitlines() if ln.startswith("| 文件 |")][0]
doc_hdr = [c.strip() for c in row.strip("|").split("|")]

print("code reads :", sorted(used))
print("expect hdr :", STACK_HEADERS)
print("xlsx header:", hdr)
print("xlsx as key:", [norm_header(h) for h in hdr])
print("readme hdr :", doc_hdr)
print("readme key :", [norm_header(h) for h in doc_hdr])
print("sheets     :", wb.sheetnames)
ok = (sorted(used) == sorted(norm_header(h) for h in hdr)
      == sorted(norm_header(h) for h in doc_hdr) == sorted(STACK_KEYS)
      == sorted(norm_header(h) for h in STACK_HEADERS))
print("MATCH:", ok)
raise SystemExit(0 if ok else 1)
