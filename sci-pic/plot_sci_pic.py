# -*- coding: utf-8 -*-
"""读取 Excel，绘制学术样式柱状折线图或双组对比柱状图。"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
from uuid import uuid4
import warnings

from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Patch, Rectangle

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
VERSION = "1.2.0"
SETTING_SPECS = [
    ("图名", "roughness", "输出文件名，不带扩展名；不能含路径或 Windows 特殊字符"),
    ("图标题", "", "可留空；留空时不在图中显示标题"),
    ("横轴标题", "抛光阶段", "根据自己的数据填写"),
    ("纵轴标题", "粗糙度", "填写测量指标的名称"),
    ("纵轴单位", "", "填写真实单位，如 μm、nm、%；未知时留空，不自动换算"),
    ("配色", "academic", "academic：青蓝柱形与深蓝折线；npg：蓝色柱形与珊瑚红折线；paired：浅蓝与深蓝；energy：冷蓝到暖红有序色阶"),
    ("图片宽度_cm", 17.5, "8～50 cm；默认适合论文双栏图"),
    ("图片高度_cm", 11.4, "6～40 cm"),
    ("分辨率_dpi", 600, "150～1200；只影响 PNG，PDF/SVG 保持矢量"),
    ("小数位数", 3, "0～10；只控制图上数值标签，不修改数据"),
    ("显示数值", "是", "是 / 否"),
    ("标签旋转_度", 0, "0～90；类别文字较长时可填 30 或 45"),
]
OPTIONAL_SETTING_SPECS = [
    ("图类型", "柱状折线", "柱状折线 / 双组柱状；双组柱状按前两行分别作为基准和实验组"),
    ("显示倍数", "否", "是 / 否；仅双组柱状支持，倍数为第二组数值除以第一组"),
    ("倍数小数位数", 2, "0～6；仅控制倍数标注的显示精度"),
]
# Cold-to-warm ordered ramp ported from the Nanoindentation Analysis project
# (Fig4 E-H map / grouped bars / merged load-displacement curves).  Anchors
# run deep blue -> light blue -> sand -> orange -> deep red; any number of
# ordered categories are sampled at evenly spaced positions with a small
# margin off the darkest ends.
ENERGY_RAMP = [
    "#08306B", "#1F4E9C", "#2F6DB5", "#5A9BD4",
    "#D8C69A", "#E3A857", "#D9772B", "#B93A25", "#8C1A1A",
]
ENERGY_EDGE = 0.06  # skip the darkest 6% at both ends of the ramp
ENERGY_CMAP = LinearSegmentedColormap.from_list("energy_cold_warm", ENERGY_RAMP)

PALETTES = {
    "academic": {"bar": "#33BBEE", "line": "#0077BB", "edge": "#333333"},
    "npg": {"bar": "#4DBBD5", "line": "#E64B35", "edge": "#3C5488"},
    "paired": {"bar": "#A8C8E8", "line": "#1B3D6E", "edge": "#333333"},
    # energy resolves its per-category colours through _energy_colours(); the
    # bar/line/edge slots below only fill in the non-ramp fallbacks.
    "energy": {"bar": "#5A9BD4", "line": "#1F4E9C", "edge": "#222222"},
}


def _energy_colours(n):
    """Sample ``n`` evenly spaced colours off the cold-to-warm ramp."""
    if n <= 0:
        return []
    if n == 1:
        return [ENERGY_CMAP(0.5)]
    span = 1.0 - 2.0 * ENERGY_EDGE
    return [ENERGY_CMAP(ENERGY_EDGE + span * i / (n - 1)) for i in range(n)]


def _hatch_colour(face_rgba):
    """Pick a hatch stroke that stays visible on both dark and light fills."""
    r, g, b, _ = face_rgba
    luminance = (0.2126 * (0.92 * r + 0.08)
                 + 0.7152 * (0.92 * g + 0.08)
                 + 0.0722 * (0.92 * b + 0.08))
    return "#F4F4F4" if luminance < 0.55 else "#333333"


class InputError(ValueError):
    """可由用户直接修正的工作簿问题。"""


def blank(value):
    return value is None or isinstance(value, str) and not value.strip()


def number(value, location):
    if isinstance(value, bool):
        raise InputError(f"{location}：需要数值，不能填写是/否或布尔值。")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise InputError(f"{location}：{value!r} 不是有效数值。") from exc
    if not math.isfinite(result):
        raise InputError(f"{location}：不接受 NaN、无穷大或溢出数值。")
    return result


def parse_data(rows):
    """保留输入顺序和零值，忽略整行空白，拒绝半填行和非数值。"""
    points = []
    for row_number, row in enumerate(rows, start=5):
        label, value = row[0], row[1]
        if blank(label) and blank(value):
            continue
        if blank(label) or blank(value):
            raise InputError(f"数据表第 {row_number} 行：类别和数值必须同时填写；不用的行请清空 A、B 两格。")
        points.append({
            "excel_row": row_number,
            "label": str(label).strip(),
            "value": number(value, f"数据!B{row_number}"),
        })
    if not points:
        raise InputError("数据表没有可绘制的数据，请从第 5 行开始填写类别和数值。")
    return points


def parse_settings(rows):
    required = {key for key, _, _ in SETTING_SPECS}
    expected = required | {key for key, _, _ in OPTIONAL_SETTING_SPECS}
    result = {}
    for row_number, row in enumerate(rows, start=5):
        key, value = row[0], row[1]
        if blank(key) and blank(value):
            continue
        key = str(key).strip() if not blank(key) else ""
        if key not in expected:
            raise InputError(f"设置表第 {row_number} 行：未知参数 {key!r}，请保留 A 列原有参数名。")
        if key in result:
            raise InputError(f"设置表参数 {key!r} 重复，请只保留一行。")
        result[key] = value
    missing = required - result.keys()
    if missing:
        raise InputError("设置表缺少参数：" + "、".join(sorted(missing)))
    for key, default, _ in OPTIONAL_SETTING_SPECS:
        result.setdefault(key, default)
    for key in ("图名", "图标题", "横轴标题", "纵轴标题", "纵轴单位"):
        result[key] = "" if blank(result[key]) else str(result[key]).strip()
    stem = result["图名"]
    if (not stem or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem)
            or stem.endswith((".", " ")) or len(stem) > 100
            or stem.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise InputError("设置表“图名”必须是有效的 Windows 文件名，不能包含路径。")
    if not result["横轴标题"] or not result["纵轴标题"]:
        raise InputError("设置表“横轴标题”和“纵轴标题”不能为空。")
    result["配色"] = str(result["配色"]).strip().lower()
    if result["配色"] not in PALETTES:
        raise InputError("设置表“配色”只能填写 academic、npg、paired 或 energy。")
    if result["图类型"] not in ("柱状折线", "双组柱状"):
        raise InputError("设置表“图类型”只能填写 柱状折线 或 双组柱状。")
    if result["显示倍数"] not in ("是", "否"):
        raise InputError("设置表“显示倍数”只能填写 是 或 否。")
    if result["显示倍数"] == "是" and result["图类型"] != "双组柱状":
        raise InputError("倍数标注仅适用于双组柱状图。")
    bounds = {
        "图片宽度_cm": (8, 50), "图片高度_cm": (6, 40),
        "分辨率_dpi": (150, 1200), "小数位数": (0, 10), "标签旋转_度": (0, 90),
        "倍数小数位数": (0, 6),
    }
    for key, (low, high) in bounds.items():
        result[key] = number(result[key], f"设置表“{key}”")
        if not low <= result[key] <= high:
            raise InputError(f"设置表“{key}”必须在 {low}～{high} 之间。")
    for key in ("分辨率_dpi", "小数位数", "倍数小数位数"):
        if not result[key].is_integer():
            raise InputError(f"设置表“{key}”必须为整数。")
        result[key] = int(result[key])
    if result["显示数值"] not in ("是", "否"):
        raise InputError("设置表“显示数值”只能填写 是 或 否。")
    return result


def load_input(path):
    from openpyxl import load_workbook

    if path.suffix.lower() != ".xlsx":
        raise InputError("输入必须是 .xlsx 工作簿。")
    raw = path.read_bytes()
    try:
        book = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise InputError(f"无法读取工作簿 {path.name}：{exc}") from exc
    try:
        if not {"数据", "设置"}.issubset(book.sheetnames):
            raise InputError("工作簿必须保留“数据”和“设置”两张表。")
        sheets = {name: book[name] for name in ("数据", "设置")}
        if [sheets["数据"].cell(4, i).value for i in (1, 2)] != ["类别", "数值"]:
            raise InputError("数据表第 4 行必须保留“类别”“数值”两列标题。")
        if [sheets["设置"].cell(4, i).value for i in (1, 2)] != ["参数", "填写值"]:
            raise InputError("设置表第 4 行必须保留“参数”“填写值”两列标题。")
        parsed = {}
        for name, sheet in sheets.items():
            if sheet.max_row is None:
                sheet.calculate_dimension(force=True)
            if sheet.max_row > 100_000:
                raise InputError(f"{name}表占用超过10万行，请删除无用尾部格式后保存。")
            rows = []
            last_row = sheet.max_row
            for cells in sheet.iter_rows(min_row=5, max_row=last_row, max_col=2):
                # 原模板在参数区后保留“使用说明”，该区域不属于配置。
                if name == "设置" and cells[0].value == "使用说明":
                    break
                for cell in cells:
                    if cell.data_type in ("f", "e"):
                        raise InputError(f"{name}!{cell.coordinate}：请填写实际值，公式或 Excel 错误值不支持；可复制后粘贴为数值。")
                rows.append([cell.value for cell in cells])
            parsed[name] = rows
        points, config = parse_data(parsed["数据"]), parse_settings(parsed["设置"])
        if config["图类型"] == "双组柱状":
            if len(points) != 2 or any(p["value"] < 0 for p in points):
                raise InputError("双组柱状图需要恰好两组非负数值；请按基准组、实验组顺序填写。")
            if config["显示倍数"] == "是":
                if points[0]["value"] == 0:
                    raise InputError("基准组为 0，无法计算倍数；请将“显示倍数”设为“否”。")
                if not math.isfinite(points[1]["value"] / points[0]["value"]):
                    raise InputError("两组数据的比值超出可显示范围，请检查数值与单位。")
        return raw, points, config
    finally:
        book.close()


def draw(points, config, run_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import MaxNLocator, ScalarFormatter
    import numpy as np

    # 中文显式选兼容字体；英文及数值统一采用 Arial。
    font_path = font_manager.findfont("Microsoft YaHei", fallback_to_default=False)
    chinese = font_manager.FontProperties(fname=font_path)
    palette = PALETTES[config["配色"]]
    energy = config["配色"] == "energy"
    values = np.array([p["value"] for p in points])
    x = np.arange(len(points))
    notes = []
    paired = config["图类型"] == "双组柱状"
    if paired:
        bar_colors = ([ENERGY_CMAP(ENERGY_EDGE), ENERGY_CMAP(1 - ENERGY_EDGE)]
                      if energy else [palette["bar"], palette["line"]])
        accent = ENERGY_CMAP(1 - ENERGY_EDGE) if energy else palette["line"]
    else:
        bar_colors = _energy_colours(len(points)) if energy else palette["bar"]
        accent = "#1F4E9C" if energy else palette["line"]
    # energy 有序色阶在单系列柱状折线图底部配一条分段色条，对应 Fig4 /
    # merged_curves 的横向色标；双组对比与其他配色不加色条。
    show_strip = energy and not paired
    with plt.rc_context({
        "font.family": "Arial", "font.size": 9, "axes.labelsize": 10,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.bottom": True, "axes.spines.left": True,
        "axes.axisbelow": True,
        "axes.unicode_minus": False, "text.usetex": False, "text.parse_math": False,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
        "hatch.linewidth": 0.45,
    }):
        fig = plt.figure(figsize=(config["图片宽度_cm"] / 2.54, config["图片高度_cm"] / 2.54),
                         facecolor="white", layout="constrained")
        if show_strip:
            gs = fig.add_gridspec(2, 1, height_ratios=[1.0, 0.075], hspace=0.12)
            ax = fig.add_subplot(gs[0, 0])
            cax = fig.add_subplot(gs[1, 0])
        else:
            ax = fig.add_subplot(111)
            cax = None
        try:
            bars = ax.bar(x, values, width=0.52,
                          color=bar_colors,
                          edgecolor=palette["edge"], linewidth=0.6, alpha=0.92, zorder=2)
            line = None
            if not paired:
                line, = ax.plot(x, values, color=accent, linewidth=1.8, marker="o", markersize=5,
                                markerfacecolor="white", markeredgecolor=accent,
                                markeredgewidth=1.3, zorder=3)
            if paired:
                # 第二组以斜纹区分身份；底色保持与第一组同条件一致，纹色按
                # 底色亮度自适应，深底用浅纹、浅底用深纹。
                second = bars[1]
                second.set_hatch("//")
                second.set_hatchcolor(_hatch_colour(second.get_facecolor()))
            spread = max(float(np.ptp(values)), float(np.max(np.abs(values))), 1e-300)
            annotations = []
            if config["显示数值"] == "是":
                for i, value in enumerate(values):
                    # 陡降段向右、陡升段向左避让，避免文字与线段相交。
                    dx = 7 if not paired and i > 0 and values[i - 1] - value > 0.1 * spread else 0
                    if not paired and not dx and i + 1 < len(values) and values[i + 1] - value > 0.1 * spread:
                        dx = -10
                    annotations.append(ax.annotate(
                        f"{value:.{config['小数位数']}f}", (i, value), xytext=(dx, 6 if value >= 0 else -9),
                        textcoords="offset points", ha="center", va="bottom" if value >= 0 else "top",
                        fontsize=9, color="#222222",
                    ))
            rotation = config["标签旋转_度"]
            ax.set_xticks(x, [p["label"] for p in points], rotation=rotation, ha="right" if rotation else "center")
            for tick, point in zip(ax.get_xticklabels(), points):
                if any(ord(c) > 127 for c in point["label"]):
                    tick.set_fontproperties(chinese)
                    tick.set_fontsize(9 if paired else 8)
                    tick.set_rotation(rotation)
            ylabel = config["纵轴标题"] + (f" ({config['纵轴单位']})" if config["纵轴单位"] else "")
            ax.set_xlabel(config["横轴标题"], fontproperties=chinese, fontsize=10, labelpad=10)
            ax.set_ylabel(ylabel, fontproperties=chinese, fontsize=10, labelpad=9)
            if config["图标题"]:
                ax.set_title(config["图标题"], fontproperties=chinese, fontsize=11, pad=10)
            ax.set_xlim(-0.6, len(points) - 0.4)
            low, high = min(0.0, float(values.min())), max(0.0, float(values.max()))
            if low == high:
                low, high = 0.0, 1.0
            else:
                padding = (high - low) * 0.17
                low = low - padding if low < 0 else low
                high = high + padding if high > 0 else high
            if paired and config["显示倍数"] == "是":
                # 箭头按两组真实高度绘制，反向变化时自然朝下。
                baseline_y, experimental_y = map(float, values)
                if baseline_y != experimental_y:
                    ax.annotate("", xy=(0.5, experimental_y), xytext=(0.5, baseline_y),
                                arrowprops={"arrowstyle": "-|>", "color": accent,
                                            "lw": 1.3, "mutation_scale": 12,
                                            "shrinkA": 0, "shrinkB": 0}, zorder=4)
                    ax.plot([0.26, 0.5], [baseline_y, baseline_y],
                            color=accent, linewidth=0.7, linestyle=(0, (3, 3)))
                    ax.plot([0.5, 0.74], [experimental_y, experimental_y],
                            color=accent, linewidth=0.7, linestyle=(0, (3, 3)))
                ratio = float(values[1] / values[0])
                ax.annotate(f"{ratio:.{config['倍数小数位数']}f}×", (0.5, (baseline_y + experimental_y) / 2),
                            xytext=(-10, 0), textcoords="offset points", ha="right", va="center",
                            color=accent, fontsize=11, fontweight="bold")
            ax.set_ylim(low, high)
            if low < 0 < high:
                ax.axhline(0, color="#777777", linewidth=0.5, zorder=1)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=3))
            formatter = ScalarFormatter(useOffset=False)
            formatter.set_powerlimits((-3, 4))
            ax.yaxis.set_major_formatter(formatter)
            # Nanoindentation publication style: outward ticks, light grey
            # horizontal grid, only left/bottom spines kept.
            ax.tick_params(axis="both", direction="out", length=2.8, width=0.6, pad=2.0)
            ax.grid(True, axis="y", color="#D9D9D9", linewidth=0.40, alpha=0.55)
            ax.grid(False, axis="x")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            for spine in ("left", "bottom"):
                ax.spines[spine].set_linewidth(0.7)
                ax.spines[spine].set_color("#222222")
            if cax is not None:
                strip_colors = _energy_colours(len(points))
                for i, colour in enumerate(strip_colors):
                    cax.add_patch(Rectangle((i, 0), 1, 1, facecolor=colour, edgecolor="none"))
                cax.set_xlim(0, len(points))
                cax.set_ylim(0, 1)
                cax.set_yticks([])
                cax.set_xticks([])
                cax.tick_params(length=0)
                for spine in cax.spines.values():
                    spine.set_visible(False)
            legend_handles = []
            if paired:
                legend_handles = [
                    Patch(facecolor=bars[0].get_facecolor(), edgecolor=palette["edge"],
                          linewidth=0.35, alpha=0.92, label=points[0]["label"].split("\n")[0]),
                    Patch(facecolor=bars[1].get_facecolor(), edgecolor=palette["edge"],
                          linewidth=0.35, alpha=0.92, hatch="//",
                          hatchcolor=_hatch_colour(bars[1].get_facecolor()),
                          label=points[1]["label"].split("\n")[0]),
                ]
                legend = fig.legend(handles=legend_handles, loc="outside upper center", ncols=2,
                                    frameon=False, handletextpad=0.5, columnspacing=1.6)
                for text in legend.get_texts():
                    text.set_fontproperties(chinese)
            # constrained layout 自动管理边距；outside 图例与底部色条均由其预留空间。
            # 核对实际绘图对象，而不是仅核对输入数组。
            assert not ax.spines["top"].get_visible() and not ax.spines["right"].get_visible(), \
                "上、右边框必须隐藏"
            assert ax.spines["left"].get_visible() and ax.spines["bottom"].get_visible(), \
                "左、下边框必须显示"
            np.testing.assert_array_equal([bar.get_height() for bar in bars], values)
            if line is not None:
                np.testing.assert_array_equal(line.get_ydata(), values)
            if annotations:
                assert [a.get_text() for a in annotations] == [f"{v:.{config['小数位数']}f}" for v in values]
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                for extension in ("png", "pdf", "svg"):
                    fig.savefig(run_dir / f"{config['图名']}.{extension}", dpi=config["分辨率_dpi"], facecolor="white")
                notes.extend(sorted({str(w.message) for w in caught}))
        finally:
            plt.close(fig)
    return {"matplotlib": matplotlib.__version__, "numpy": np.__version__}, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description="填写 Excel 后运行，生成学术样式柱状折线图或双组对比图。")
    parser.add_argument("--excel", type=Path, default=ROOT / "templates" / "sci_pic.xlsx", help="输入工作簿；默认使用 templates/sci_pic.xlsx")
    parser.add_argument("--out", type=Path, default=ROOT / "out", help="输出根目录；每次新建独立的运行子目录")
    parser.add_argument("--check", action="store_true", help="只读检查工作簿和依赖，不生成结果")
    args = parser.parse_args(argv)
    lock = args.excel.with_name("~$" + args.excel.name)
    if lock.exists():
        print(f"提示：{args.excel.name} 正在 Excel 中打开；本次读取磁盘上已保存的版本，"
              "未保存的修改不会生效。建议保存并关闭后重跑。", file=sys.stderr)
    run_dir = None
    try:
        import openpyxl
        raw, points, config = load_input(args.excel)
        import matplotlib
        import numpy
        from matplotlib import font_manager
        font_manager.findfont("Microsoft YaHei", fallback_to_default=False)
        print(f"解释器：{sys.executable}")
        print(f"工作簿：{args.excel.resolve()}")
        print(f"数据：{len(points)} 项；配色：{config['配色']}")
        if not config["纵轴单位"]:
            print("提示：纵轴单位留空；图中不标单位，请在论文使用前核对。")
        if args.check:
            print("检查通过。未改写工作簿，未生成结果。")
            return 0
        run_dir = args.out / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6])
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "input_snapshot.xlsx").write_bytes(raw)
        versions, render_notes = draw(points, config, run_dir)
        with (run_dir / "data_used.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Excel行号", "类别", "数值"])
            writer.writerows((p["excel_row"], p["label"], p["value"]) for p in points)
        report = {
            "version": VERSION, "status": "completed", "input_file": args.excel.name,
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "settings": config, "point_count": len(points), "data": points,
            "processing": "按Excel原有顺序绘图；忽略A、B两格均为空的行；不筛选、不排序、不拟合、不平滑、不换算",
            "style": "Nanoindentation 出版物风格：隐藏上右边框、浅灰水平网格、外向刻度；academic/npg/paired 为分类色板，energy 为冷蓝到暖红有序色阶（单系列底部带分段色条）",
            "verification": "仅左、下边框显示，上、右边框隐藏；实际柱高、数值标签及适用时的折线点均与输入数据核对一致",
            "render_warnings": render_notes,
            "limitations": ["未提供不确定度数据，不生成误差棒"] + ([] if config["纵轴单位"] else ["粗糙度或其他指标的单位尚未填写"]),
            "dependencies": {**versions, "openpyxl": openpyxl.__version__},
        }
        if config["图类型"] == "双组柱状":
            report["style"] = "plot-from-data/bar_paired_delta；第一组实色、第二组斜纹区分；两柱之间的竖向箭头从第一组高度指向第二组高度，旁边标注第二组/第一组倍数，顶部居中无框图例"
            report["comparison"] = {
                "baseline": points[0]["value"], "experimental": points[1]["value"],
                "ratio": points[1]["value"] / points[0]["value"] if points[0]["value"] else None,
                "definition": "第二组/第一组；描述性比值，不代表统计显著性",
            }
        (run_dir / "run_info.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成 PNG、PDF、SVG：{run_dir.resolve()}")
        if render_notes:
            print("存在绘图提示，详见 run_info.json 中 render_warnings。")
        return 0
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        if run_dir is not None:
            print(f"本次运行未完成，已生成的部分文件保留于：{run_dir.resolve()}", file=sys.stderr)
            try:
                (run_dir / "FAILED.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
