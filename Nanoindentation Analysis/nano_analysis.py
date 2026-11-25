# -*- coding: utf-8 -*-
"""纳米压痕批量分析入口。

用法：
    fill_config.bat               # 先把 data/ 里的 xls 导入 config.xlsx
    打开 config.xlsx 设置图例/样品编号/测点/参与开关，存盘
    run.bat                       # 分析 data/ 下参与设置的 xls，结果写入 out/
    run.bat --src 目录 --out 目录  # 指定其它输入输出
    run.bat --no-curves            # 跳过原始曲线解析（只出仪器 Results 结论）

原始输入只读；输出目录每次运行前清理普通文件（不动子目录）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# 便携 Python 的 sys.path[0] 指向 python312.zip，需显式加入脚本目录
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nano_core import (
    discover_xls, read_one, mean_sd, mad_sigma, flag_outliers,
    paired_signflip_test, independent_permutation_test, split_by_largest_gap,
)
from nano_config import file_key, load_settings
import nano_plots as plots


class ExcelLockedError(RuntimeError):
    """结果工作簿被 Excel / WPS 占用，无法写入。"""

    def __init__(self, path):
        super().__init__(str(path))
        self.path = Path(path)


def analyze_tests(files, indents, cfg):
    """逐个文件判断其 Test 1..N 次压痕是否可合并为一条代表曲线。

    同一份 xls 里对同一点重复压了多次（Test 001/002/...），先做批内偏差分析：
      - 用该文件各次压痕的 E、H 算变异系数 COV 与相对均值的最大偏差；
      - E_COV、H_COV 都不超过上限时判为「一致」，压痕合并成一条代表曲线；
      - 代表（medoid）：E/H 联合最接近文件均值的那次压痕；
      - 偏差大时不合并，各次压痕曲线独立保留，并写明原因。

    注意：这里的合并只影响「曲线如何聚合与绘图」，不重算 E/H——仪器 Results
    表已是该点全部有效压痕的统计结果，绝对值得分仍以它为准。

    返回 dict：{启用, E上限, H上限, min_tests, files:[每文件判定], units:[绘图单元]}
    """
    mcfg = cfg.get("压痕合并", {})
    enabled = bool(mcfg.get("启用", True))
    e_lim = float(mcfg.get("E偏差上限_pct", 5.0))
    h_lim = float(mcfg.get("H偏差上限_pct", 5.0))
    min_n = int(mcfg.get("最小压痕数", 3))

    by_source = {}
    for i in indents:
        by_source.setdefault(i["source"], []).append(i)

    records, units = [], []
    for f in files:
        rows = [r for r in by_source.get(f["source"], [])
                if r.get("E") is not None and r.get("H") is not None]
        sources_curves = [r for r in by_source.get(f["source"], []) if r.get("curve_raw")]
        es = [r["E"] for r in rows]
        hs = [r["H"] for r in rows]
        em, esd, en = mean_sd(es)
        hm, hsd, hn = mean_sd(hs)
        e_cov = esd / em * 100 if em and en > 1 else 0.0
        h_cov = hsd / hm * 100 if hm and hn > 1 else 0.0
        e_max = max((abs(v - em) / em * 100 for v in es), default=0.0) if em else 0.0
        h_max = max((abs(v - hm) / hm * 100 for v in hs), default=0.0) if hm else 0.0

        ok = (enabled and en >= min_n and math.isfinite(e_cov) and math.isfinite(h_cov)
              and e_cov <= e_lim and h_cov <= h_lim)
        if en and em and hm:
            def dist(r):
                return ((r["E"] - em) / em) ** 2 + ((r["H"] - hm) / hm) ** 2
            rep = min(rows, key=dist)
        else:
            rep = rows[0] if rows else None

        if ok:
            reason = (f"E_COV={e_cov:.2f}%≤{e_lim}%、H_COV={h_cov:.2f}%≤{h_lim}%，"
                      f"{en} 次压痕合并为一条代表曲线")
            decision = "合并"
        else:
            why = []
            if not enabled:
                why.append("合并功能已关闭")
            if en < min_n:
                why.append(f"有效压痕{en}<{min_n}")
            if e_cov > e_lim:
                why.append(f"E_COV={e_cov:.2f}%>{e_lim}%")
            if h_cov > h_lim:
                why.append(f"H_COV={h_cov:.2f}%>{h_lim}%")
            reason = "；".join(why) or "偏差不满足合并条件"
            decision = "不合并"

        rec = {
            "source": f["source"], "label": f["label"], "sample": f["sample"],
            "point": f["point"], "n_tests": en, "n_curves": len(sources_curves),
            "E_mean": f["E_mean"], "H_mean": f["H_mean"],
            "E_cov": e_cov, "H_cov": h_cov,
            "E_maxdev": e_max, "H_maxdev": h_max,
            "E_lim": e_lim, "H_lim": h_lim,
            "decision": decision, "reason": reason,
            "representative_test": rep["test_no"] if rep else None,
        }
        records.append(rec)
        units.append({
            "source": f["source"], "label": f["label"], "sample": f["sample"],
            "point": f["point"], "merged": ok, "n_tests": en,
            "E_mean": f["E_mean"], "H_mean": f["H_mean"],
            "E_cov": e_cov, "H_cov": h_cov, "reason": reason,
            "curves": [{"test_no": r["test_no"], "curve": r["curve_raw"],
                        "E": r["E"], "H": r["H"]} for r in sources_curves],
            "rep_test": rep["test_no"] if rep else None,
        })

    return {
        "启用": enabled, "E上限": e_lim, "H上限": h_lim, "最小压痕数": min_n,
        "files": records, "units": units,
        "n_merged": sum(1 for r in records if r["decision"] == "合并"),
    }


def analyze(cfg, src, out, want_curves=True, want_plots=True, settings=None):
    files_in = discover_xls(src)
    settings = settings or {}
    if settings:
        files_in = [p for p in files_in
                    if settings.get(file_key(p.name), {}).get("include", True)]
    if not files_in:
        raise RuntimeError(f"输入目录没有参与分析的 xls 文件: {src}")

    per_indent, files, warnings = [], [], []
    nominal = cfg["参考载荷_mN"]
    tol_pct = cfg["载荷容差_pct"]
    drift_max = cfg["漂移上限_nm_s"]

    for fi, path in enumerate(files_in):
        rec = read_one(path, want_curves=want_curves)
        ov = settings.get(file_key(path.name), {})
        if ov.get("sample") is not None:
            rec["sample"] = ov["sample"]
        if ov.get("point") is not None:
            rec["point"] = ov["point"]
        rec["label"] = ov.get("label") or rec["source"]
        rec["group"] = ov.get("group") or None
        indents = rec["results"]["indents"]
        if not indents:
            warnings.append(f"{path.name}: Results 无压痕数据，跳过")
            continue
        # 批内稳健离群标记（E 与 H 分别判断）
        e_flags = flag_outliers([i.get("E") for i in indents], cfg["离群z阈值"])
        h_flags = flag_outliers([i.get("H") for i in indents], cfg["离群z阈值"])
        es = [i["E"] for i in indents if i.get("E") is not None]
        hs = [i["H"] for i in indents if i.get("H") is not None]
        em, esd, en = mean_sd(es)
        hm, hsd, hn = mean_sd(hs)
        drifts = [i.get("drift") for i in indents if i.get("drift") is not None]
        hmaxs = [i.get("hmax") for i in indents if i.get("hmax") is not None]
        pmaxes = [i.get("Pmax") for i in indents if i.get("Pmax") is not None]
        pmean, _, _ = mean_sd(pmaxes)
        deviation = abs(pmean - nominal) / nominal * 100 if nominal else float("nan")
        drive = mean_sd(drifts)[0]
        h_cov = hsd / hm * 100 if hm else float("nan")
        flags = []
        if deviation > tol_pct:
            flags.append(f"载荷偏差{deviation:.1f}%")
        if drive and abs(drive) > drift_max:
            flags.append(f"漂移{drive:.3f}nm/s")
        if en < 3:
            flags.append("有效压痕不足")
        if not rec["name_ok"]:
            flags.append("文件名无法解析")
        if math.isfinite(h_cov) and h_cov > 10:
            flags.append(f"硬度COV {h_cov:.1f}%")
        if flags:
            warnings.append(f"{path.name}: " + ", ".join(flags))

        fsum = {
            "file_index": fi, "source": rec["source"], "path": str(path),
            "seq": rec["seq"], "sample": rec["sample"], "point": rec["point"],
            "label": rec["label"], "group": rec["group"],
            "load_mn": rec["load_mn"], "rate_mn_s": rec["rate_mn_s"],
            "hold_s": rec["hold_s"], "n": en,
            "E_mean": em, "E_sd": esd, "E_cov": esd / em * 100 if em else float("nan"),
            "H_mean": hm, "H_sd": hsd, "H_cov": hsd / hm * 100 if hm else float("nan"),
            "drift_mean": drive, "hmax_mean": mean_sd(hmaxs)[0],
            "Pmax_mean": pmean, "Pmax_dev_pct": deviation,
            "method": rec["rtf"]["method"], "operator": rec["rtf"]["operator"],
            "flags": flags, "n_outliers": sum(e_flags) + sum(h_flags),
        }
        files.append(fsum)
        for i, (ind, ef, hf) in enumerate(zip(indents, e_flags, h_flags)):
            curve = rec["curves"].get(ind["test_no"])
            row = {
                "source": rec["source"], "label": rec["label"],
                "point": rec["point"], "sample": rec["sample"],
                "test_no": ind["test_no"], "E": ind.get("E"), "H": ind.get("H"),
                "drift": ind.get("drift"), "hmax": ind.get("hmax"),
                "Pmax": ind.get("Pmax"), "tip": ind.get("tip"),
                "E_outlier": ef, "H_outlier": hf,
            }
            if curve:
                for key in ("hf_nm", "recovery_ratio", "creep_hold_nm",
                            "creep_rate_nm_s", "drift_rate_nm_s",
                            "stiffness_fit_n_m", "unload_exponent_m",
                            "popin_count", "popin_max_jump_nm"):
                    row[key] = curve.get(key)
                row["curve_raw"] = curve.get("_raw")
            per_indent.append(row)

    # 按样品编号分组统计
    groups = {}
    for sample in sorted({f["sample"] for f in files if f["sample"] is not None}):
        sub = [f for f in files if f["sample"] == sample]
        es = [v for f in sub for i in per_indent
              if i["source"] == f["source"] for v in [i["E"]] if v is not None]
        hs = [v for f in sub for i in per_indent
              if i["source"] == f["source"] for v in [i["H"]] if v is not None]
        em, esd, en = mean_sd(es)
        hm, hsd, hn = mean_sd(hs)
        groups[sample] = {
            "n_files": len(sub), "n_indents": en,
            "E_mean": em, "E_sd": esd, "E_cov": esd / em * 100 if em else float("nan"),
            "H_mean": hm, "H_sd": hsd, "H_cov": hsd / hm * 100 if hm else float("nan"),
        }

    # 识别双峰总体（按文件 H 均值），避免把两类材料平均成一个数
    split = split_by_largest_gap([f["H_mean"] for f in files],
                                 cfg["双峰最小间隙比"])
    split_info = None
    if split:
        low = [files[i] for i in split["low_ids"]]
        high = [files[i] for i in split["high_ids"]]
        split_info = {
            **split,
            "low_files": [f["source"] for f in low],
            "high_files": [f["source"] for f in high],
            "low_E": mean_sd([f["E_mean"] for f in low])[0],
            "high_E": mean_sd([f["E_mean"] for f in high])[0],
            "low_H": split["low_mean"], "high_H": split["high_mean"],
        }

    # 配对比较：同一测点下两种样品编号的 E/H 差值
    paired = []
    by_point = {}
    for f in files:
        if f["point"] is not None:
            by_point.setdefault(f["point"], []).append(f)
    sample_pairs = [tuple(p) for p in cfg["组间比较"]["关注样品对"]]
    for point, sub in sorted(by_point.items()):
        for ta, tb in sample_pairs:
            fa = next((f for f in sub if f["sample"] == ta), None)
            fb = next((f for f in sub if f["sample"] == tb), None)
            if not fa or not fb:
                continue
            paired.append({
                "point": point, "sample_a": ta, "sample_b": tb,
                "E_a": fa["E_mean"], "E_b": fb["E_mean"],
                "H_a": fa["H_mean"], "H_b": fb["H_mean"],
                "dE": fb["E_mean"] - fa["E_mean"],
                "dH": fb["H_mean"] - fa["H_mean"],
            })
    # 样品编号只出现一次时提示核对，不自动改写用户设置
    sample_counts = {}
    for f in files:
        if f["sample"] is not None:
            sample_counts[f["sample"]] = sample_counts.get(f["sample"], 0) + 1
    for sample, count in sorted(sample_counts.items(), key=lambda kv: str(kv[0])):
        if count == 1:
            warnings.append(f"样品编号 {sample} 仅 1 个文件，建议核对配置表或文件名")

    paired_stats = {}
    if paired:
        paired_stats["E"] = paired_signflip_test([p["dE"] for p in paired])
        paired_stats["H"] = paired_signflip_test([p["dH"] for p in paired])

    # 仅在稳定（高硬度）总体内部做配对检验，排除软相混入造成的方差膨胀
    if split_info and paired:
        hard_sources = set(split_info["high_files"])
        stable_pairs = [p for p in paired
                        if any(f["point"] == p["point"] and f["source"] in hard_sources
                               for f in files)]
        if stable_pairs:
            paired_stats["stable_E"] = paired_signflip_test([p["dE"] for p in stable_pairs])
            paired_stats["stable_H"] = paired_signflip_test([p["dH"] for p in stable_pairs])
            paired_stats["stable_n"] = len(stable_pairs)

    # 组间比较（仅当数据未分成两簇时才做全体样品编号比较，避免混合分布误判）
    group_tests = {}
    pair_config = cfg["组间比较"].get("关注样品对", [])
    if pair_config and not split:
        ga, gb = pair_config[0]
        if ga in groups and gb in groups:
            def vals(sid, key):
                return [i[key] for i in per_indent
                        if i["sample"] == sid and i[key] is not None]
            for metric in ("E", "H"):
                gt = independent_permutation_test(vals(ga, metric), vals(gb, metric))
                gt["a"], gt["b"] = ga, gb
                group_tests[metric] = gt

    # ---- 文件内压痕合并：同一 xls 的 Test 1..N 偏差小则合并为一条代表曲线 ----
    merge_info = analyze_tests(files, per_indent, cfg)

    return {
        "cfg": cfg, "src": str(src), "out": str(out),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": files, "indents": per_indent, "groups": groups,
        "merge": merge_info,
        "split": split_info, "paired": paired, "paired_stats": paired_stats,
        "group_tests": group_tests, "warnings": warnings,
        "methods": {
            "E_H": "仪器 Results 表 'Modulus/Hardness At Max Load'",
            "离群": f"稳健 z 分数 > {cfg['离群z阈值']}（中位数/MAD）",
            "配对检验": "精确符号翻转置换检验（双尾）",
            "组间检验": "蒙特卡洛置换检验（20000 次，固定种子）" if group_tests else "数据双峰，跳过全体比较",
            "双峰": f"最大间隙占全距比例 ≥ {cfg['双峰最小间隙比']}",
            "压痕合并": (f"每个文件内 Test 1..N 的 E/H_COV ≤ "
                     f"{merge_info['E上限']}/{merge_info['H上限']}% 时合并为一条代表曲线；"
                     f"代表取离文件均值最近的那次压痕（medoid）；E/H 仍取仪器 Results"
                     if merge_info["启用"] else "已关闭"),
        },
    }


def write_outputs(analysis, out, want_plots=True):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # 清理旧 out 之前先确认结果表可写；被 Excel 打开时直接停止，避免删掉上一轮结果。
    target = out / "nano_results.xlsx"
    if target.exists():
        try:
            with target.open("r+b"):
                pass
        except PermissionError as exc:
            raise ExcelLockedError(target) from exc
    for p in list(out.glob("*")):
        if p.is_file():
            try:
                p.unlink()
            except OSError as exc:
                print(f"WARN: 无法清理 {p.name}: {exc}")
    fig_dir = out / "figures"
    fig_dir.mkdir(exist_ok=True)
    for p in list(fig_dir.rglob("*")):
        if p.is_file():
            try:
                p.unlink()
            except OSError as exc:
                print(f"WARN: 无法清理 {p.name}: {exc}")

    # CSV
    with open(out / "indent_detail.csv", "w", encoding="utf-8-sig", newline="") as fh:
        cols = ["source", "label", "point", "sample", "test_no", "E", "H", "drift", "hmax",
                "Pmax", "E_outlier", "H_outlier", "hf_nm", "recovery_ratio",
                "creep_hold_nm", "creep_rate_nm_s", "drift_rate_nm_s",
                "stiffness_fit_n_m", "unload_exponent_m", "popin_count",
                "popin_max_jump_nm"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in analysis["indents"]:
            w.writerow({k: (round(v, 5) if isinstance(v, float) and math.isfinite(v)
                            else ("" if v is None or (isinstance(v, float) and not math.isfinite(v)) else v))
                        for k, v in row.items() if k in cols})

    summary = {
        "generated": analysis["generated"], "src": analysis["src"],
        "methods": analysis["methods"],
        "groups": analysis["groups"], "split": analysis["split"],
        "merge": analysis["merge"],
        "paired": analysis["paired"], "paired_stats": analysis["paired_stats"],
        "group_tests": analysis["group_tests"], "warnings": analysis["warnings"],
        "files": [{k: (round(v, 5) if isinstance(v, float) and math.isfinite(v) else v)
                   for k, v in f.items()} for f in analysis["files"]],
    }
    with open(out / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)

    # 报告先写，Excel 的"方法与结论"页会读取它
    write_report_md(analysis, out / "report.md")
    write_excel(analysis, out / "nano_results.xlsx")

    figs = []
    if want_plots:
        cfg = analysis["cfg"]
        plots.init_style(cfg)
        figs += plots.generate_publication_figures(analysis, cfg, fig_dir)
    return figs


def write_excel(a, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="DCE6F1")
    bold = Font(bold=True)

    def sheet(title, headers, rows, widths=None):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            ws.cell(1, c).font = bold
            ws.cell(1, c).fill = head_fill
            ws.cell(1, c).alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append([round(v, 4) if isinstance(v, float) and math.isfinite(v)
                       else ("" if isinstance(v, float) else v) for v in r])
        for c in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(c)].width = (
                widths[c - 1] if widths and c - 1 < len(widths) else 15)
        ws.freeze_panes = "A2"
        return ws

    wb.remove(wb.active)
    sheet("文件汇总",
          ["文件", "图例名称", "分组", "测点", "样品编号", "载荷mN", "加载速率mN/s", "保载s", "有效压痕",
           "E均值GPa", "E标准差", "E_COV%", "H均值GPa", "H标准差", "H_COV%",
           "最大深度nm", "最大载荷mN", "载荷偏差%", "漂移nm/s", "离群数", "质控标记"],
          [[f["source"], f["label"], f.get("group") or "", f["point"], f["sample"],
            f["load_mn"], f["rate_mn_s"],
            f["hold_s"], f["n"], f["E_mean"], f["E_sd"], f["E_cov"], f["H_mean"],
            f["H_sd"], f["H_cov"], f["hmax_mean"], f["Pmax_mean"],
            f["Pmax_dev_pct"], f["drift_mean"], f["n_outliers"], "; ".join(f["flags"])]
           for f in a["files"]],
          widths=[30, 22, 14, 8, 10, 10, 13, 8, 9, 11, 10, 9, 11, 10, 9, 12, 12, 11, 11, 8, 24])

    sheet("逐次压痕",
          ["文件", "图例名称", "测点", "样品编号", "Test", "E(GPa)", "H(GPa)", "漂移nm/s",
           "最大深度nm", "最大载荷mN", "E离群", "H离群", "残余深度nm",
           "弹性恢复比", "保载蠕变nm", "蠕变速率nm/s", "拟合刚度N/m", "卸载指数m",
           "pop-in数", "最大突增nm"],
          [[i["source"], i["label"], i["point"], i["sample"], i["test_no"], i["E"], i["H"],
            i["drift"], i["hmax"], i["Pmax"], i["E_outlier"], i["H_outlier"],
            i.get("hf_nm"), i.get("recovery_ratio"), i.get("creep_hold_nm"),
            i.get("creep_rate_nm_s"), i.get("stiffness_fit_n_m"),
            i.get("unload_exponent_m"), i.get("popin_count")]
           for i in a["indents"]])

    group_rows = []
    for sample, g in sorted(a["groups"].items(), key=lambda kv: str(kv[0])):
        group_rows.append([str(sample), g["n_files"], g["n_indents"], g["E_mean"],
                           g["E_sd"], g["E_cov"], g["H_mean"], g["H_sd"], g["H_cov"]])
    sheet("样品分组", ["样品编号", "文件数", "压痕数", "E均值GPa", "E标准差", "E_COV%",
                   "H均值GPa", "H标准差", "H_COV%"], group_rows)

    # 文件内 Test 1..N 的偏差分析与合并判定
    mg = a["merge"]
    test_rows = [[r["source"], r["label"], r["sample"], r["point"], r["n_tests"],
                  r["n_curves"], r["E_mean"], r["H_mean"], r["E_cov"], r["H_cov"],
                  r["E_maxdev"], r["H_maxdev"], r["E_lim"], r["H_lim"],
                  r["decision"], r["representative_test"], r["reason"]]
                 for r in mg["files"]]
    sheet("压痕偏差与合并",
          ["文件", "图例名称", "样品编号", "测点", "有效压痕", "曲线数",
           "E均值GPa", "H均值GPa", "E_COV%", "H_COV%",
           "E最大相对偏差%", "H最大相对偏差%", "E阈值%", "H阈值%",
           "判定", "代表Test号", "依据"], test_rows,
          widths=[30, 22, 10, 8, 10, 9, 11, 11, 10, 10, 16, 16, 10, 10, 10, 12, 46])

    paired_rows = [[p["point"], str(p["sample_a"]), str(p["sample_b"]), p["E_a"],
                    p["E_b"], p["dE"], p["H_a"], p["H_b"], p["dH"]]
                   for p in a["paired"]]
    sheet("配对比较", ["测点", "样品A", "样品B", "E_A(GPa)", "E_B(GPa)", "ΔE(GPa)",
                   "H_A(GPa)", "H_B(GPa)", "ΔH(GPa)"], paired_rows)

    stat_rows = []
    for metric in ("E", "H"):
        st = a["paired_stats"].get(metric)
        if st:
            stat_rows.append([f"配对{metric}", st["n"], st["mean"], st["sd"],
                              st["p"], st["method"]])
        st = a["paired_stats"].get(f"stable_{metric}")
        if st:
            stat_rows.append([f"高硬度组配对{metric}", st["n"], st["mean"], st["sd"],
                              st["p"], "精确符号翻转置换（排除软相测点）"])
        gt = a["group_tests"].get(metric)
        if gt:
            stat_rows.append([f"组间{metric}", f"{gt['n1']}/{gt['n2']}", gt["diff"],
                              "", gt["p"], gt["method"]])
    sp = a["split"]
    if sp:
        stat_rows.append(["双峰识别-E", len(sp["low_ids"]), sp["low_E"], sp["high_E"],
                          sp["gap_ratio"], "最大间隙切分"])
        stat_rows.append(["双峰识别-H", len(sp["low_ids"]), sp["low_H"], sp["high_H"],
                          sp["gap_ratio"], "最大间隙切分"])
    sheet("统计检验", ["项目", "n", "数值1/均值差", "数值2/SD", "p值或比例", "方法"],
          stat_rows, widths=[16, 10, 16, 16, 14, 36])

    ws = wb.create_sheet("方法与结论")
    ws.column_dimensions["A"].width = 110
    for line in (path.parent / "report.md").read_text(encoding="utf-8").splitlines():
        ws.append([line])
    try:
        wb.save(path)
    except PermissionError as exc:
        raise ExcelLockedError(path) from exc


def _fmt(v, nd=2):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:.{nd}f}"


def write_report_md(a, path):
    cn_sec = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七",
              8: "八", 9: "九", 10: "十"}
    section = {"n": 0}

    def head(title):
        section["n"] += 1
        L.append(f"## {cn_sec.get(section['n'], section['n'])}、{title}")
        L.append("")

    L = ["# 纳米压痕数据分析结论", ""]
    L.append(f"- 生成时间：{a['generated']}")
    L.append(f"- 输入目录：`{a['src']}`")
    if a["cfg"].get("配置文件"):
        L.append(f"- 用户配置：`{a['cfg']['配置文件']}`（图例名称、样品编号、测点、参与开关由此表设置）")
    L.append(f"- 数据规模：{len(a['files'])} 个文件，{sum(f['n'] for f in a['files'])} 次有效压痕")
    L.append("- 图片索引：`out/figures/index.md`（主图/补充图/保留图与单文件图）")
    L.append("")
    head("数据口径")
    L.append("- 弹性模量 E、硬度 H 取仪器 Results 表在最大载荷处的计算结果；使用同一针尖标定（面积系数一致），可直接横向比较。")
    L.append("- 载荷-位移曲线用于残余深度、蠕变、卸载刚度与 pop-in 等机理线索，不用于重算绝对 E/H。")
    L.append("")
    head("分组统计")
    L.append("| 样品编号 | 文件数 | 压痕数 | E 均值±SD (GPa) | E COV | H 均值±SD (GPa) | H COV |")
    L.append("|---|---:|---:|---|---:|---|---:|")
    for sample, g in sorted(a["groups"].items(), key=lambda kv: str(kv[0])):
        L.append(f"| {sample} | {g['n_files']} | {g['n_indents']} | "
                 f"{_fmt(g['E_mean'])}±{_fmt(g['E_sd'])} | {_fmt(g['E_cov'],1)}% | "
                 f"{_fmt(g['H_mean'])}±{_fmt(g['H_sd'])} | {_fmt(g['H_cov'],1)}% |")
    L.append("")

    # 文件内 Test 1..N 偏差分析与合并
    mg = a["merge"]
    if mg and mg["files"]:
        head("重复压痕偏差分析与合并（文件内 Test 1..N）")
        L.append(f"判定口径：每个 xls 内的各次压痕，E_COV ≤ {_fmt(mg['E上限'],1)}% "
                 f"且 H_COV ≤ {_fmt(mg['H上限'],1)}%、有效压痕 ≥ {mg['最小压痕数']} 时，"
                 f"判为该测点内部一致，只画一条代表曲线。"
                 f"合并功能{'开启' if mg['启用'] else '关闭'}。")
        L.append("")
        L.append("| 文件 | 测点 | 有效压痕 | E_COV | H_COV | E最大偏差 | H最大偏差 | 判定 | 代表Test |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---|---:|")
        for r in mg["files"]:
            L.append(f"| {r['source'][:26]} | {r['point']} | {r['n_tests']} | "
                     f"{_fmt(r['E_cov'],2)}% | {_fmt(r['H_cov'],2)}% | "
                     f"{_fmt(r['E_maxdev'],2)}% | {_fmt(r['H_maxdev'],2)}% | "
                     f"{r['decision']} | {r['representative_test'] or '—'} |")
        L.append("")
        L.append(f"- {mg['n_merged']} / {len(mg['files'])} 个文件的重复压痕合并为一条代表曲线，"
                 f"其余文件保留各次压痕曲线。")
        L.append("")
        L.append("> 合并只改变曲线的聚合方式（多条 → 一条代表曲线），"
                 "不改 E/H 数值——仪器 Results 已是该点全部有效压痕的统计结果。")
        L.append("")

    sp = a["split"]
    if sp:
        head("关键发现：存在两类力学响应")
        L.append(f"按硬度对文件做最大间隙切分，得到间隙 {_fmt(sp['gap'])} GPa（占全距 {_fmt(sp['gap_ratio']*100,1)}%）：")
        L.append("")
        L.append(f"- 高硬度组（{len(sp['high_ids'])} 文件：{', '.join(sp['high_files'])}）："
                 f"E ≈ {_fmt(sp['high_E'],1)} GPa，H ≈ {_fmt(sp['high_H'],2)} GPa")
        L.append(f"- 低硬度组（{len(sp['low_ids'])} 文件：{', '.join(sp['low_files'])}）："
                 f"E ≈ {_fmt(sp['low_E'],1)} GPa，H ≈ {_fmt(sp['low_H'],2)} GPa")
        L.append("")
        L.append("> 这说明不同测点的力学性质差异远大于同测点下的样品编号间差异；此时不能把全部压痕混在一起只报一个平均值。")
        L.append("> 两组对应不同材料相/区域还是工艺异常，需结合测点位置与样品形貌判断。")
        L.append("")

    head("同测点样品配对比较")
    if a["paired"]:
        sa, sb = str(a["paired"][0]["sample_a"]), str(a["paired"][0]["sample_b"])
        L.append(f"| 测点 | E({sa}) | E({sb}) | ΔE | H({sa}) | H({sb}) | ΔH |")
        L.append("|---:|---:|---:|---:|---:|---:|---:|")
        for p in a["paired"]:
            L.append(f"| {p['point']} | {_fmt(p['E_a'],1)} | {_fmt(p['E_b'],1)} | "
                     f"{_fmt(p['dE'],1)} | {_fmt(p['H_a'],2)} | {_fmt(p['H_b'],2)} | "
                     f"{_fmt(p['dH'],2)} |")
        L.append("")
        for metric, unit in (("E", "GPa"), ("H", "GPa")):
            st = a["paired_stats"].get(metric)
            if st:
                verdict = "显著" if st["p"] < 0.05 else "不显著"
                L.append(f"- Δ{metric} 均值 {_fmt(st['mean'],2)} {unit}，"
                         f"{st['n']} 对，{st['method']}，p={_fmt(st['p'],4)} → {verdict}（α=0.05）。")
        n_stable = a["paired_stats"].get("stable_n", 0)
        if n_stable and n_stable < len(a["paired"]):
            L.append("")
            L.append(f"仅统计高硬度组测点（{n_stable} 对，排除软相测点）：")
            for metric, unit in (("E", "GPa"), ("H", "GPa")):
                st = a["paired_stats"].get(f"stable_{metric}")
                if st:
                    verdict = "显著" if st["p"] < 0.05 else "不显著"
                    L.append(f"- Δ{metric} 均值 {_fmt(st['mean'],2)} {unit}，"
                             f"p={_fmt(st['p'],4)} → {verdict}（α=0.05）。")
        L.append("")
    else:
        L.append("未找到同一测点下同时存在两种样品编号的文件，无法做配对比较；可在配置表的「配对样品对」中修改对比对象。")
        L.append("")

    if a["group_tests"]:
        head("样品编号组间比较（全体压痕）")
        for metric in ("E", "H"):
            gt = a["group_tests"][metric]
            verdict = "显著" if gt["p"] < 0.05 else "不显著"
            L.append(f"- {metric}：样品 {gt.get('a')} vs {gt.get('b')} 均值差 "
                     f"{_fmt(gt['diff'],2)} GPa，p={_fmt(gt['p'],4)} → {verdict}。")
        L.append("")

    head("数据质量")
    hard_covs, soft_covs = [], []
    if sp:
        hard_names, soft_names = set(sp["high_files"]), set(sp["low_files"])
        for f in a["files"]:
            if not math.isfinite(f["H_cov"]):
                continue
            (hard_covs if f["source"] in hard_names else soft_covs).append(f["H_cov"])
    else:
        hard_covs = [f["H_cov"] for f in a["files"] if math.isfinite(f["H_cov"])]
    if hard_covs:
        L.append(f"- 高硬度组硬度批内 COV：{_fmt(min(hard_covs),2)}% – {_fmt(max(hard_covs),2)}%，重复性良好。")
    if soft_covs:
        L.append(f"- 低硬度组批内 COV：{_fmt(min(soft_covs),2)}% – {_fmt(max(soft_covs),2)}%，"
                 f"离散显著；该组均值受单次压痕位置影响大，仅可作半定量参考。")
    if a["warnings"]:
        for w in a["warnings"]:
            L.append(f"- 提示：{w}")
    pw = [i for i in a["indents"] if i.get("popin_count")]
    L.append(f"- 加载段检出疑似 pop-in 的压痕 {len(pw)} 次，"
             f"需结合曲线图人工复核，不作为结论性证据。")
    head("方法边界")
    for k, v in a["methods"].items():
        L.append(f"- {k}：{v}")
    L.append("- 显著性判断基于置换检验，不改变原始仪器数据；绝对值仍受针尖面积函数标定影响。")
    L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="纳米压痕批量分析（MTS TestWorks xls）")
    root = Path(__file__).resolve().parent
    ap.add_argument("--src", default=str(root / "data"), help="输入目录（默认模块 data/）")
    ap.add_argument("--out", default=str(root / "out"), help="输出目录（默认模块 out/）")
    ap.add_argument("--config", default=None,
                    help="配置表 config.xlsx 或基础 config.json（默认先读 config.xlsx）")
    ap.add_argument("--no-curves", action="store_true", help="不解析原始载荷-位移曲线")
    ap.add_argument("--no-plots", action="store_true", help="不生成图片")
    args = ap.parse_args(argv)

    cfg, settings = load_settings(args.config, args.src)
    analysis = analyze(cfg, Path(args.src), Path(args.out),
                       want_curves=not args.no_curves, settings=settings)
    try:
        figs = write_outputs(analysis, args.out, want_plots=not args.no_plots)
    except ExcelLockedError as exc:
        print(f"[错误] 无法写入结果表：{exc.path}", file=sys.stderr)
        print("[提示] 文件可能正被 Excel / WPS 占用。请保存并关闭其中的 "
              "nano_results.xlsx 后重新运行。", file=sys.stderr)
        print("[提示] 若已关闭，请检查该文件是否只读，以及 out 目录是否有写入权限。",
              file=sys.stderr)
        return 4

    print(f"已分析 {len(analysis['files'])} 个文件，"
          f"{sum(f['n'] for f in analysis['files'])} 次有效压痕")
    for sample, g in sorted(analysis["groups"].items(), key=lambda kv: str(kv[0])):
        print(f"  样品 {sample}: n={g['n_indents']}, E={g['E_mean']:.1f}±{g['E_sd']:.1f} GPa, "
              f"H={g['H_mean']:.2f}±{g['H_sd']:.2f} GPa")
    if analysis["split"]:
        print(f"  双峰：H 高 {analysis['split']['high_H']:.2f} / 低 "
              f"{analysis['split']['low_H']:.2f} GPa")
    mg = analysis["merge"]
    if mg["files"]:
        print(f"  文件内压痕合并：{mg['n_merged']}/{len(mg['files'])} 个文件合并为一条代表曲线")
        for r in mg["files"]:
            print(f"    {r['source'][:26]:28s} E_COV={r['E_cov']:.1f}% "
                  f"H_COV={r['H_cov']:.1f}% -> {r['decision']}")
    print(f"输出目录：{args.out}（{len(figs)} 张图）")
    if analysis["warnings"]:
        print(f"质控提示 {len(analysis['warnings'])} 条，详见 out/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
