# -*- coding: utf-8 -*-
"""生成 stack.xlsx 模板（可重复运行覆盖）。

也被 _fill_stack.py 复用: 它先算出要写哪些行, 再调 write_workbook 落盘。

表头定义放在 plot_raman.py 里（它才是读表的一方），这里只导入，
免得两份表头各改各的、对不上。
"""
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = os.path.dirname(os.path.abspath(__file__))
# 便携 Python 的 sys.path 不含脚本目录, 必须自己加
sys.path.insert(0, HERE)

from plot_raman import (  # noqa: E402
    STACK_HEADERS as HEADERS, STACK_KEYS as KEYS, norm_header)

PATH = os.path.join(HERE, "stack.xlsx")
WIDTHS = [16, 22, 16, 14, 12, 12, 12, 12]


def write_workbook(path, rows):
    """rows: list of 8-cell lists matching HEADERS."""
    wb = Workbook()
    ws = wb.active
    ws.title = "配置"

    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(horizontal="center")

    for r in rows:
        ws.append(r)

    for col, w in zip("ABCDEFGH", WIDTHS):
        ws.column_dimensions[col].width = w

    doc = wb.create_sheet("说明")
    lines = [
        ("堆叠对比图配置", True),
        ("", False),
        ("改完存盘，双击 run.bat 就出图，不用在窗口里填任何参数。", False),
        ("行顺序 = 图上从下往上的顺序，第一条在最下面；图例统一放在右上角。", False),
        ("要重新把 data 里所有 txt 列进来：双击 fill_stack.bat。", False),
        ("", False),
        ("列说明", True),
        ("文件        data 文件夹里的 txt 文件名，.txt 可以省略，必填", False),
        ("图例名称    图例里显示的名字；用 | 换行（例如 样品 A|三次平均）；留空则用文件名", False),
        ("归一化(0/1)  1 = 这条谱缩放到自己的 0~1 范围；0 = 保留原始强度", False),
        ("            整张表必须一致，不能有的填 1 有的填 0", False),
        ("标注峰位(0/1)  1 = 在这条谱上标注峰位；0 或留空 = 不标", False),
        ("            可以只标其中几条，例如只标对比样品", False),
        ("峰高阈值    标注阈值，单位 counts（按原始强度判断，不受归一化影响）", False),
        ("            留空则用默认 5；调大只留强峰，调小标出弱峰", False),
        ("纵向偏移    纵向偏移量，留空则自动排布，一般不用填", False),
        ("区间下限    横向显示区间左边界，留空 = 数据全范围；两列要么都填要么都空", False),
        ("区间上限    横向显示区间右边界，同上", False),
        ("", False),
        ("表头可以用中文，也可以用英文（file/label/normalize/peaks/height/offset/xmin/xmax），两者都认。", False),
        ("颜色按行的顺序自动分配（红、蓝、绿、紫、橙、青、灰、洋红），超过 8 条循环使用。", False),
        ("归一化全填 1 时纵轴不标数字（标了也没意义），轴标题显示 norm.。", False),
        ("峰位数字用高斯+线性基线拟合算出，和单谱出图用的是同一套算法。", False),
        ("", False),
        ("想回到逐个 txt 单谱出图（每个文件一张图）的模式：把 stack.xlsx 改名，例如 stack.xlsx.bak。", False),
        ("产物：out\\stack.png（合并图）+ 每行一张 out\\<文件名>.png（单谱图）。不写 peaks.csv。", False),
    ]
    for text, bold in lines:
        doc.append([text])
        if bold:
            doc.cell(row=doc.max_row, column=1).font = Font(bold=True, size=12)
    doc.column_dimensions["A"].width = 95

    wb.save(path)


if __name__ == "__main__":
    write_workbook(PATH, [
        ["0-1.txt", "样品 0-1", 1, 1, None, None, None, None],
        ["0-2.txt", "样品 0-2", 1, 1, None, None, None, None],
    ])
    print("wrote", PATH)
