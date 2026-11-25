# -*- coding: utf-8 -*-
"""生成 montage.xlsx 模板；默认拒绝覆盖用户已有配置。

    python _make_montage_xlsx.py                      # 空模板（只有表头和说明）
    python _make_montage_xlsx.py --from-src ..\\1100   # 按该目录的实物图/光谱预填
"""
import argparse
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from raman_montage import MONTAGE_HEADERS, discover_groups  # noqa: E402

WIDTHS = [12, 20, 18, 16, 12, 13, 11, 10, 11, 11, 11]


def write_workbook(path, rows, overwrite=False):
    if os.path.exists(path) and not overwrite:
        raise ValueError("%s 已存在；确需重建请加 --force" % path)
    wb = Workbook()
    ws = wb.active
    ws.title = "拼图"
    ws.append(MONTAGE_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append(r)
    for col, w in zip("ABCDEFGHIJK", WIDTHS):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    doc = wb.create_sheet("说明")
    lines = [
        ("拼图配置（实物图 + 该样品各位置的拉曼谱）", True),
        ("", False),
        ("一行一条光谱；组名相同的行合成一张拼图，实物图在该组任意一行写一次即可。", False),
        ("产物：out\\montage\\<组名>.png（上方实物图，下方该组全部光谱）+ montage_peaks.csv。", False),
        ("", False),
        ("列说明", True),
        ("组名        这一组的名字，也是输出文件名；留空则按文件主名推（1-2.txt -> 1）", False),
        ("实物图      实物照片的路径，相对 --src；支持 png/jpg/tif/bmp", False),
        ("文件        --src 里的光谱 txt，.txt 可省略，必填", False),
        ("图例名称    图例里显示的位置名，用 | 换行；留空用文件名", False),
        ("归一化(0/1)  1 = 该条谱缩放到 0~1；0 = 保留原始 counts。同一组必须一致", False),
        ("标注峰位(0/1)  1 或留空 = 标峰；0 = 这条谱不标", False),
        ("峰高阈值    标峰阈值，原始 counts；留空按该谱自适应 max(5.5σ, 1.1%×H_max)", False),
        ("            想按每条谱自动定阈值：先跑 fill_stack.bat 的思路手动填，或直接调大只留强峰", False),
        ("颜色        谱线颜色，写 #RRGGBB 或 red/blue 这类名字；留空按红、绿、蓝顺序", False),
        ("纵向偏移    纵向错开量，留空自动排布（一般不用填）", False),
        ("区间下限/上限  横向区间，可只填一端，空的一端自动取数据边界", False),
        ("", False),
        ("不想写表格时，直接用自动扫描：实物图命名 <组名>-1-1.png，光谱命名 <组名>-<序号>.txt，", False),
        ("然后 run.bat --montage 即可（不带配置表）。", False),
        ("", False),
        ("说明：各位置的谱按基线纵向错开，纵刻度数字是错开后的位置，只适合看单条谱自身的强弱；", False),
        ("跨位置比较峰高请用 montage_peaks.csv 里的 H。", False),
    ]
    for text, bold in lines:
        doc.append([text])
        if bold:
            doc.cell(row=doc.max_row, column=1).font = Font(bold=True, size=12)
    doc.column_dimensions["A"].width = 96

    wb.save(path)
    return path


def rows_from_src(src):
    groups, _notes = discover_groups(src)
    rows = []
    for g in groups:
        for r in g["rows"]:
            rows.append([g["name"], os.path.basename(g["photo"]),
                         os.path.basename(r["path"]), r["label"],
                         0, 1, None, r["color"], None, None, None])
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 Raman 拼图配置模板")
    ap.add_argument("path", nargs="?", default=str(HERE / "montage.xlsx"))
    ap.add_argument("--from-src", default=None,
                    help="按该目录自动扫描的结果预填行（实物图 <组名>-1-1.png）")
    ap.add_argument("--force", action="store_true", help="覆盖已有目标文件")
    args = ap.parse_args(argv)
    try:
        rows = rows_from_src(args.from_src) if args.from_src else []
        target = write_workbook(args.path, rows, overwrite=args.force)
    except (OSError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    print("wrote %s (%d row(s))" % (target, len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
