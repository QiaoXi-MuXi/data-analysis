# -*- coding: utf-8 -*-
"""纳米压痕的 Excel 配置表：先导入 data 的文件清单，再由用户设置。

参照 Raman/stack.xlsx 的做法：
  - 第一次运行或双击 fill_config.bat 时，按 data/ 中实际的 xls 生成 config.xlsx；
  - 用户在「配置」表里改图例名称、样品编号、测点、是否参与、分组、备注；
  - 「全局设置」表里改载荷容差、漂移上限、离群阈值、双峰间隙比、配对样品对；
  - run.bat 每次读这张表，表头按名字识别，列顺序可调，中英文都认。
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

from nano_core import discover_xls, load_config as load_json_config, parse_filename

CONFIG_NAME = "config.xlsx"
SHEET_NAME = "配置"
STATE_DIR = "_state"
SNAPSHOT_NAME = "config_snapshot.json"

HEADERS = ["文件", "图例名称", "样品编号", "测点", "参与(0/1)", "分组", "备注"]
KEYS = ["file", "label", "sample", "point", "include", "group", "note"]
ALIASES = {
    "文件": "file", "文件名": "file", "file": "file",
    "图例名称": "label", "图例": "label", "label": "label", "名称": "label",
    "样品编号": "sample", "样品": "sample", "条件编号": "sample", "sample": "sample",
    "测点": "point", "测点编号": "point", "point": "point",
    "参与": "include", "参与分析": "include", "绘图": "include", "include": "include",
    "分组": "group", "group": "group",
    "备注": "note", "说明": "note", "note": "note",
}
GLOBAL_HEADERS = ["参数", "值", "说明"]
GLOBAL_KEYS = ("参考载荷_mN", "载荷容差_pct", "漂移上限_nm_s", "离群z阈值",
               "双峰最小间隙比", "配对样品对", "绘图dpi",
               "压痕合并_启用", "压痕合并_E偏差上限_pct",
               "压痕合并_H偏差上限_pct", "压痕合并_最小压痕数")
GLOBAL_DOC = {
    "参考载荷_mN": "标称最大载荷，用于偏差质控（默认 30）",
    "载荷容差_pct": "相对标称载荷的允许偏差 %（默认 8）",
    "漂移上限_nm_s": "仪器漂移修正绝对值的上限 nm/s（默认 0.15）",
    "离群z阈值": "稳健 z 分数（中位数/MAD）离群阈值（默认 3.5）",
    "双峰最小间隙比": "最大间隙占全距的比例，达到才认为有两簇（默认 0.25）",
    "配对样品对": "同一测点下要比的样品编号，如 1100-1210；多对用分号隔开",
    "绘图dpi": "输出图片分辨率（默认 180）",
    "压痕合并_启用": "同一 xls 内 Test 1..N 偏差小则合并为一条代表曲线（1开/0关，默认1）",
    "压痕合并_E偏差上限_pct": "文件内各次压痕 E 变异系数上限 %（默认 5），低于它才合并",
    "压痕合并_H偏差上限_pct": "文件内各次压痕 H 变异系数上限 %（默认 5），低于它才合并",
    "压痕合并_最小压痕数": "一个文件至少几次有效压痕才考虑合并（默认 3）",
}


def norm_header(name) -> str:
    """表头 -> 内部键；去掉括号提示和空白，查中英文别名。"""
    s = "" if name is None else str(name)
    s = re.sub(r"[(（][^)）]*[)）]", "", s)
    s = s.replace(" ", "").replace("\u3000", "").strip().lower()
    return ALIASES.get(s, s)


def file_key(name) -> str:
    n = Path(str(name or "").strip()).name.lower()
    return n if n.endswith(".xls") else n + ".xls"


def sample_value(v):
    """数值样式的编号转成整数，其余保留文字；空值返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (not math.isfinite(v) or not v.is_integer()):
            return str(v)
        return int(v)
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(?:\.0+)?", s):
        return int(float(s))
    return s


def strip_sample_from_label(label, sample=None):
    """图例名称不再手写样品编号：去掉末尾的 (样品编号)。"""
    s = "" if label is None else str(label).strip()
    if not s or sample is None:
        return s
    pattern = r"^(.*?)\s*[（(]\s*" + re.escape(str(sample).strip()) + r"\s*[)）]\s*$"
    m = re.match(pattern, s)
    return m.group(1).strip() if m else s


def infer_group(label, sample=None):
    """从图例名称推断分组：图例名称本身已是能量/条件，无需再拆样品编号。"""
    s = "" if label is None else str(label).strip()
    if not s:
        return ""
    m = re.match(r"^(.*?)\s*[（(]([^（）()]*)[)）]\s*$", s)
    if m and sample is not None and m.group(2).strip() == str(sample).strip():
        return m.group(1).strip()
    return s


def int_value(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        f = float(v)
        return int(f) if math.isfinite(f) and f.is_integer() else None
    except (TypeError, ValueError):
        return None


def flag01(v, default=True) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "0.0", "n", "no", "false", "否", "跳过", "不开")


def _as_cell(v):
    return "" if v is None else v


def build_rows(data_dir, existing=None):
    """按 data/ 现状生成配置行；同名行保留用户设置，只补缺失的自动列。"""
    files = discover_xls(data_dir)
    old = {}
    for name, row in (existing or {}).items():
        old[file_key(name)] = row
    rows, added = [], []
    for path in files:
        key = file_key(path.name)
        meta = parse_filename(path.name)
        raw = old.pop(key, None)
        if isinstance(raw, dict):          # 兼容 read_workbook 的 settings dict
            row = [raw.get(k) for k in KEYS]
        else:
            row = list(raw) if raw else [None] * len(KEYS)
        row += [None] * (len(KEYS) - len(row))
        label_val = row[KEYS.index("label")] or Path(path.name).stem
        sample_val = (sample_value(row[KEYS.index("sample")])
                      if row[KEYS.index("sample")] not in (None, "")
                      else meta["sample"])
        label_val = strip_sample_from_label(label_val, sample_val)
        auto = {
            "file": path.name,
            "label": label_val,
            "sample": sample_val,
            "point": int_value(row[KEYS.index("point")])
                     if row[KEYS.index("point")] not in (None, "") else meta["point"],
            "include": flag01(row[KEYS.index("include")]),
            "group": row[KEYS.index("group")] or infer_group(label_val, sample_val),
            "note": row[KEYS.index("note")] or "",
        }
        rows.append([_as_cell(auto[k]) for k in KEYS])
        if key not in (file_key(n) for n, _ in (existing or {}).items()):
            added.append(path.name)
    # leftovers in old = rows in config.xlsx but no longer in data/
    return rows, added, sorted(old)


def write_workbook(path, rows, globals_rows=None):
    """写出配置表；「配置」第一行表头，后面是数据行。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append(list(r))
    for col, width in zip("ABCDEFG", (30, 24, 12, 9, 12, 14, 30)):
        ws.column_dimensions[col].width = width

    gs = wb.create_sheet("全局设置")
    gs.append(GLOBAL_HEADERS)
    for c in gs[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
    base = globals_rows if globals_rows is not None else [
        [k, "", GLOBAL_DOC[k]] for k in GLOBAL_KEYS]
    for r in base:
        gs.append(list(r))
    gs.column_dimensions["A"].width = 22
    gs.column_dimensions["B"].width = 18
    gs.column_dimensions["C"].width = 70

    doc = wb.create_sheet("说明")
    lines = [
        ("纳米压痕出图配置", True),
        ("", False),
        ("1. data\\ 里放 MTS 导出的 .xls（可直接用原文件名）。", False),
        ("2. 双击 fill_config.bat 把 data 里的文件导入本表；已有行的设置会保留。", False),
        ("3. 打开 config.xlsx，在「配置」表里设置，存盘后双击 run.bat。", False),
        ("4. 全局参数在「全局设置」表；留空用默认值，不用改 config.json。", False),
        ("", False),
        ("配置列说明", True),
        ("文件        data 文件夹里的 xls 文件名，必填", False),
        ("图例名称    图上显示的名字（不用写样品编号，程序会自动附在后面）；留空用文件名", False),
        ("样品编号    分组标签，默认从文件名第二段读取；可改成任意编号（如 1100A）", False),
        ("测点        配对键；同一测点下不同样品会被配对比较，默认从文件名末段读取", False),
        ("参与(0/1)   0 = 本文件不参与分析和绘图；留空或 1 = 参与", False),
        ("分组        柱状图分组名（如 0.39J/cm²），同一组的不同样品柱子紧挨着；"
         "留空时自动从图例名称去掉末尾样品编号得到", False),
        ("备注        只写给自己看，不影响计算", False),
        ("", False),
        ("表头中英文都认，列顺序可以调整；括号里的 (0/1) 只是提示。", False),
        ("产物：out\\report.md、nano_results.xlsx、summary.json、indent_detail.csv。", False),
        ("图片：out\\figures\\overview_all.png（全部数据总图），", False),
        ("      out\\figures\\grouped_bar.png（按分组+样品编号的柱状图，同组紧邻、"
         "不同组留间隔、同一样品颜色一致），", False),
        ("      out\\figures\\per_sample_*.png（每个样品编号一张），", False),
        ("      out\\figures\\per_file\\*.png（每个文件一张）。", False),
    ]
    for text, bold in lines:
        doc.append([text])
        if bold:
            doc.cell(row=doc.max_row, column=1).font = Font(bold=True, size=12)
    doc.column_dimensions["A"].width = 95
    wb.save(path)


def read_workbook(path):
    """返回 (文件设置 dict, 全局设置 dict)。只读第一张「配置」表 + 全局设置表。"""
    from openpyxl import load_workbook
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        settings, global_rows = {}, {}
        ws = wb.worksheets[0]
        pos = None
        for raw in ws.iter_rows(values_only=True):
            cells = [_as_cell(c).strip() if isinstance(c, str) else c for c in raw]
            if not any(c not in (None, "") for c in cells):
                continue
            header = [norm_header(c) for c in cells]
            if pos is None:
                pos = {k: i for i, k in enumerate(header) if k}
                continue
            get = lambda k: cells[pos[k]] if k in pos and pos[k] < len(cells) else None
            name = str(get("file") or "").strip()
            if not name or name.startswith("#"):
                continue
            settings[file_key(name)] = {
                "file": Path(name).name,
                "label": str(get("label") or "").strip(),
                "sample": sample_value(get("sample")),
                "point": int_value(get("point")),
                "include": flag01(get("include")),
                "group": str(get("group") or "").strip(),
                "note": str(get("note") or "").strip(),
            }
        if len(wb.worksheets) > 1:
            for raw in wb.worksheets[1].iter_rows(values_only=True):
                if not raw or raw[0] is None:
                    continue
                key = str(raw[0]).strip()
                if key in GLOBAL_KEYS and len(raw) > 1 and raw[1] not in (None, ""):
                    global_rows[key] = raw[1]
        return settings, global_rows
    finally:
        wb.close()


def _merge_globals(base, overrides):
    cfg = dict(base)
    cfg["组间比较"] = dict(base["组间比较"])
    cfg["绘图"] = dict(base["绘图"])
    numeric = ("参考载荷_mN", "载荷容差_pct", "漂移上限_nm_s",
               "离群z阈值", "双峰最小间隙比", "绘图dpi")
    for k in numeric:
        if k in overrides:
            try:
                cfg[k] = float(overrides[k])
            except (TypeError, ValueError):
                pass
    # 压痕合并：数值项 + 开关
    merge = dict(cfg.get("压痕合并", {}))
    for key, opt in (("压痕合并_E偏差上限_pct", "E偏差上限_pct"),
                     ("压痕合并_H偏差上限_pct", "H偏差上限_pct"),
                     ("压痕合并_最小压痕数", "最小压痕数")):
        if key in overrides:
            try:
                merge[opt] = float(overrides[key])
            except (TypeError, ValueError):
                pass
    if "压痕合并_启用" in overrides:
        v = str(overrides["压痕合并_启用"]).strip().lower()
        merge["启用"] = v not in ("0", "0.0", "n", "no", "false", "否", "关闭", "关")
    cfg["压痕合并"] = merge
    if "配对样品对" in overrides:
        pairs = []
        for chunk in str(overrides["配对样品对"]).replace("，", ";").split(";"):
            bits = re.split(r"[-—~到vsVS]", chunk.strip(), maxsplit=1)
            if len(bits) == 2 and bits[0] and bits[1]:
                pairs.append([sample_value(bits[0]), sample_value(bits[1])])
        if pairs:
            cfg["组间比较"]["关注样品对"] = pairs
    return cfg


def excel_lock_path(path):
    """Excel 打开文件时会生成 ~$xxx.xlsx 锁文件。"""
    p = Path(path)
    return p.with_name("~$" + p.name)


def _snapshot_path(config_path):
    return Path(config_path).resolve().parent / STATE_DIR / SNAPSHOT_NAME


def _save_snapshot(config_path, settings, global_rows):
    """配置读取成功后留一份快照，供文件被占用/损坏时兜底。"""
    try:
        path = _snapshot_path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": str(Path(config_path).resolve()),
            "saved": datetime.now().isoformat(timespec="seconds"),
            "settings": settings,
            "globals": global_rows,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _load_snapshot(config_path):
    path = _snapshot_path(config_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data.get("settings"), dict) else None


def _rows_to_settings(rows):
    """把 build_rows 生成的行转成 read_workbook 风格的 settings dict。"""
    out = {}
    for row in rows:
        rec = dict(zip(KEYS, row))
        name = str(rec.get("file") or "").strip()
        if not name:
            continue
        out[file_key(name)] = {
            "file": Path(name).name,
            "label": str(rec.get("label") or "").strip(),
            "sample": sample_value(rec.get("sample")),
            "point": int_value(rec.get("point")),
            "include": flag01(rec.get("include")),
            "group": str(rec.get("group") or "").strip(),
            "note": str(rec.get("note") or "").strip(),
        }
    return out


def load_settings(config_path=None, data_dir=None, create=True):
    """读取配置表并与 config.json 合并，返回 (cfg, 文件设置 dict)。

    config.xlsx 不存在时按 data/ 自动生成一次（等价于先导入 Excel）。
    """
    root = Path(__file__).resolve().parent
    src = Path(data_dir) if data_dir else root / "data"
    if config_path is None:
        json_path, xlsx = root / "config.json", root / CONFIG_NAME
    else:
        cp = Path(config_path)
        if cp.suffix.lower() == ".json":
            json_path, xlsx = cp, cp.with_suffix(".xlsx")
        else:
            json_path, xlsx = root / "config.json", cp
    cfg = load_json_config(json_path)
    if not xlsx.is_file():
        if not create:
            return cfg, {}
        rows, _, _ = build_rows(src)
        write_workbook(xlsx, rows)
        print(f"已按 data/ 生成配置表：{xlsx}（可在表里改设置后重跑）")
    elif excel_lock_path(xlsx).exists():
        print(f"提示：{xlsx.name} 正在 Excel 中打开；本次读取磁盘上已保存的版本，"
              "Excel 里未保存的修改不会生效。建议保存并关闭 Excel 后重跑。")

    # 读取失败时依次回退：上次成功快照 -> 按 data/ 自动生成默认配置
    try:
        settings, global_rows = read_workbook(xlsx)
        _save_snapshot(xlsx, settings, global_rows)
    except Exception as exc:
        print(f"WARNING: 读取 {xlsx.name} 失败（{type(exc).__name__}: {exc}）")
        snap = _load_snapshot(xlsx)
        if snap:
            settings, global_rows = snap["settings"], snap.get("globals") or {}
            print(f"已回退到上次成功读取的配置快照：{_snapshot_path(xlsx)}"
                  f"（保存于 {snap.get('saved', '未知时间')}）")
        else:
            rows, _, _ = build_rows(src)
            settings, global_rows = _rows_to_settings(rows), {}
            print("已回退到按 data/ 自动生成的默认配置"
                  "（图例名称=文件名，样品编号/测点从文件名解析）。")

    for row in settings.values():
        row["label"] = strip_sample_from_label(row.get("label"), row.get("sample"))
        if not row.get("group"):
            row["group"] = infer_group(row.get("label"), row.get("sample"))
    cfg = _merge_globals(cfg, global_rows)
    cfg["配置文件"] = str(xlsx)
    cfg["组间比较"]["关注样品对"] = [
        [sample_value(a), sample_value(b)]
        for a, b in cfg["组间比较"].get("关注样品对", [])
    ]
    return cfg, settings
