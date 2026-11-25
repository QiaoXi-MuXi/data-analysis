# -*- coding: utf-8 -*-
"""Raman 分层批次展示：配置读取、输入发现与数据清单。

本模块只做「有哪些文件、它们属于哪个组/点/扫描、参数是什么」，不读取光谱
数值、不检测峰、不画图。旧配置 stack.xlsx / compare.xlsx 不受影响。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import collections
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile

from compare_config import (CompareConfigError, normalize_header, parse_bool,
                            parse_number, warn_if_excel_open, _atomic_save)


PHOTO_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")
RE_SPECTRUM = re.compile(r"^(\d+)-(\d+)$")
RE_REFERENCE = re.compile(r"^0-(\d+)$")
RE_PHOTO = re.compile(r"^(\d+)-1-1$")
REPRESENTATIVES = ("mean", "first", "none")

SPECTRUM_HEADERS = ["文件", "组名", "点号", "扫描号", "图例名称",
                    "启用(0/1)", "峰高阈值", "实物图"]
REFERENCE_HEADERS = ["基准ID", "文件", "图例名称", "启用(0/1)", "峰高阈值"]
PARAMETER_HEADERS = ["参数", "值", "说明"]

SPECTRUM_ALIASES = {
    "file": ("文件", "文件名", "光谱文件", "file"),
    "group": ("组名", "分组", "group"),
    "point": ("点号", "点位", "point"),
    "scan": ("扫描号", "扫描", "scan"),
    "label": ("图例名称", "图例", "名称", "label"),
    "enabled": ("启用", "启用(0/1)", "enabled"),
    "height": ("峰高阈值", "阈值", "height"),
    "photo": ("实物图", "实拍图", "image", "photo"),
}
REFERENCE_ALIASES = {
    "ref_id": ("基准id", "基准ID", "编号", "ref_id", "id"),
    "file": ("文件", "文件名", "基准文件", "file"),
    "label": ("图例名称", "图例", "名称", "label"),
    "enabled": ("启用", "启用(0/1)", "enabled"),
    "height": ("峰高阈值", "阈值", "height"),
}

PARAMETER_SPECS = (
    ("代表谱", "mean", "mean / first / none；只控制显示，不改变定量"),
    ("归一化(0/1)", "1", "1=所有图按显示区间归一化；只影响显示"),
    ("区间下限", "", "与区间上限成对填写；留空取分析集合共同交集，不外推"),
    ("区间上限", "", "留空同上"),
    ("匹配容差", "10", "正数，cm-1；交给现有 compare 核心"),
    ("峰位校正", "0", "cm-1；全局同时作用于基准与全部样品"),
    ("激光线排除半宽", "50", "非负，cm-1；沿用现有口径"),
    ("总览列数", "4", "正整数；本批 20 点形成 4x5"),
    ("总览每格最多峰标签", "0", "非负整数；总览默认不标峰"),
    ("单点每条最多峰标签", "3", "非负整数；只控制显示"),
    ("启用跨组同点图", "1", "0/1"),
    ("启用基准指标图", "1", "0/1"),
    ("启用基准", "", "逗号分隔的基准ID；留空=全部启用的基准"),
    ("首页摘要基准", "", "基准ID；留空取第一个启用的基准"),
    ("期望点数", "0", "0=按数据自动推断；正数时校验缺扫描"),
    ("每点扫描数", "0", "0=按数据自动推断；正数时校验缺扫描"),
    ("配对组1", "", "跨组配对的一侧组名；留空按组名排序取第一组"),
    ("配对组2", "", "跨组配对的另一侧组名；留空取第二组"),
)

PARAMETER_ALIASES = {
    "representative": ("代表谱", "representative"),
    "normalize": ("归一化", "归一化(0/1)", "normalize"),
    "xmin": ("区间下限", "xmin"),
    "xmax": ("区间上限", "xmax"),
    "tolerance": ("匹配容差", "tolerance"),
    "shift": ("峰位校正", "shift"),
    "zero_guard": ("激光线排除半宽", "零位排除半宽", "zero_guard"),
    "overview_cols": ("总览列数", "overview_cols"),
    "overview_peaks": ("总览每格最多峰标签", "overview_peaks"),
    "point_peaks": ("单点每条最多峰标签", "point_peaks"),
    "enable_paired": ("启用跨组同点图", "enable_paired"),
    "enable_reference_metrics": ("启用基准指标图", "enable_reference_metrics"),
    "active_references": ("启用基准", "active_references"),
    "home_reference": ("首页摘要基准", "home_reference"),
    "expected_points": ("期望点数", "expected_points"),
    "scans_per_point": ("每点扫描数", "scans_per_point"),
    "pair_group1": ("配对组1", "pair_group1"),
    "pair_group2": ("配对组2", "pair_group2"),
}
PARAMETER_ALIASES_LOOKUP = {}
for _key, _aliases in PARAMETER_ALIASES.items():
    for _alias in _aliases:
        PARAMETER_ALIASES_LOOKUP[normalize_header(_alias)] = _key


@dataclass
class BatchScan:
    file: str
    path: Path
    group: str
    point: int
    scan: int
    label: str = ""
    enabled: bool = True
    height: float | None = None
    photo: str = ""
    status: str = "ok"
    message: str = ""
    source_row: int | None = None

    @property
    def key(self):
        return (self.group, self.point, self.scan)

    @property
    def display_label(self):
        return (self.label or "").strip() or "%s-P%02d-S%d" % (
            self.group, self.point, self.scan)


@dataclass
class BatchReference:
    ref_id: str
    file: str
    path: Path
    label: str = ""
    enabled: bool = True
    height: float | None = None
    source_row: int | None = None

    @property
    def display_label(self):
        return (self.label or "").strip() or self.ref_id


@dataclass
class BatchConfig:
    path: Path | None
    src: Path
    scans: list[BatchScan] = field(default_factory=list)
    references: list[BatchReference] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    representative: str = "mean"
    normalize: bool = True
    xmin: float | None = None
    xmax: float | None = None
    tolerance: float = 10.0
    shift: float = 0.0
    zero_guard: float = 50.0
    overview_cols: int = 4
    overview_peaks: int = 0
    point_peaks: int = 3
    enable_paired: bool = True
    enable_reference_metrics: bool = True
    active_references: list[str] = field(default_factory=list)
    home_reference: str = ""
    expected_points: int = 0
    scans_per_point: int = 0
    pair_groups: tuple[str, str] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def active_scans(self):
        return [s for s in self.scans if s.enabled and s.status == "ok"]

    @property
    def active_refs(self):
        if not self.active_references:
            return [r for r in self.references if r.enabled]
        wanted = set(self.active_references)
        return [r for r in self.references if r.enabled and r.ref_id in wanted]

    def home_ref(self):
        refs = self.active_refs
        if not refs:
            raise CompareConfigError("没有启用的基准谱，无法生成基准指标")
        if self.home_reference:
            for ref in refs:
                if ref.ref_id == self.home_reference:
                    return ref
            raise CompareConfigError("首页摘要基准 %r 不在启用的基准中" % self.home_reference)
        return refs[0]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_input(src, name):
    """相对路径按输入根目录解析；绝对路径原样使用。"""
    text = str(name or "").strip()
    if not text:
        raise CompareConfigError("文件名为空")
    path = Path(text)
    if not path.is_absolute():
        path = Path(src) / path
    if not path.exists() and path.suffix.casefold() != ".txt":
        candidate = Path(str(path) + ".txt")
        if candidate.exists():
            path = candidate
    return path.resolve()


def relative_name(src, path):
    try:
        return Path(path).resolve().relative_to(Path(src).resolve()).as_posix()
    except ValueError:
        return str(path)


def _infer_expectations(scans):
    """从实际数据推断期望点号与扫描号，用于补出缺扫描占位。"""
    points = sorted({s.point for s in scans if s.status == "ok"})
    scan_ids = sorted({s.scan for s in scans if s.status == "ok"})
    return points, scan_ids


def add_missing_scans(scans, expected_points, scans_per_point, warnings):
    """把缺失的 (组,点,扫描) 补成 status=missing 的占位行。"""
    result = list(scans)
    present = {s.key for s in result if s.status == "ok"}
    groups = sorted({s.group for s in result if s.status == "ok"})
    points = sorted({s.point for s in result if s.status == "ok"})
    scans_found = sorted({s.scan for s in result if s.status == "ok"})
    if expected_points:
        points = list(range(1, int(expected_points) + 1))
    if scans_per_point:
        scans_found = list(range(1, int(scans_per_point) + 1))
    for group in groups:
        for point in points:
            for scan in scans_found:
                key = (group, point, scan)
                if key in present:
                    continue
                result.append(BatchScan(
                    file="", path=Path(), group=group, point=point, scan=scan,
                    status="missing", message="预期扫描文件不存在"))
                warnings.append("%s P%02d S%d：缺少扫描文件" % (group, point, scan))
    result.sort(key=lambda s: (s.group, s.point, s.scan, s.status != "missing"))
    return result


def discover(src, expected_points=0, scans_per_point=0):
    """自动发现：根目录 0-N.txt 为基准，子目录 <点>-<扫描>.txt 为样品。

    返回 (config 雏形字典, warnings)。只接受约定命名，意外文件只记警告。
    """
    src = Path(src).resolve()
    if not src.is_dir():
        raise CompareConfigError("输入目录不存在：%s" % src)
    warnings = []
    references, scans = [], []
    for entry in sorted(src.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("~$") or entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix.casefold() == ".txt":
            match = RE_REFERENCE.match(entry.stem)
            if match:
                rid = "R%s" % match.group(1)
                references.append(BatchReference(
                    rid, relative_name(src, entry), entry, label="基准 %s" % entry.stem))
            else:
                warnings.append("根目录未识别的 TXT，已忽略：%s" % entry.name)
    groups = []
    for entry in sorted(src.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name.startswith("~"):
            continue
        photo_by_point = {}
        spectra = []
        unexpected = []
        for child in sorted(entry.iterdir(), key=lambda p: p.name):
            if child.name.startswith("~$") or child.name.startswith("."):
                continue
            if not child.is_file():
                continue
            stem, ext = child.stem, child.suffix.casefold()
            spectrum = RE_SPECTRUM.match(stem)
            photo = RE_PHOTO.match(stem)
            if ext == ".txt" and spectrum:
                spectra.append(BatchScan(
                    relative_name(src, child), child.resolve(), entry.name,
                    int(spectrum.group(1)), int(spectrum.group(2))))
            elif ext in PHOTO_EXTS and photo:
                photo_by_point[int(photo.group(1))] = relative_name(src, child)
            else:
                unexpected.append(child.name)
        if not spectra:
            if unexpected:
                warnings.append("子目录 %s 未发现约定光谱，已整体忽略（含 %d 个意外文件）"
                                % (entry.name, len(unexpected)))
            continue
        groups.append(entry.name)
        for scan in spectra:
            scan.photo = photo_by_point.get(scan.point, "")
        if unexpected:
            warnings.append("%s 有 %d 个意外文件，未纳入分析：%s"
                            % (entry.name, len(unexpected),
                               ", ".join(unexpected[:5])))
        missing_photos = sorted({s.point for s in spectra if not s.photo})
        if missing_photos:
            warnings.append("%s 以下点位缺实物图，详情页将不含照片：%s"
                            % (entry.name, ", ".join("P%02d" % p for p in missing_photos)))
        scans.extend(spectra)

    if not references:
        raise CompareConfigError("未在根目录发现 0-N.txt 基准谱")
    if not scans:
        raise CompareConfigError("未在子目录发现 <点>-<扫描>.txt 光谱")
    keys = [s.key for s in scans]
    duplicates = [key for key, count in collections.Counter(keys).items() if count > 1]
    if duplicates:
        raise CompareConfigError("重复的 (组名,点号,扫描号)：%s"
                                 % ", ".join(str(k) for k in duplicates[:5]))
    scans = add_missing_scans(scans, expected_points, scans_per_point, warnings)
    return {
        "src": src,
        "references": references,
        "scans": scans,
        "groups": sorted(groups),
        "warnings": warnings,
    }


def _columns(header, aliases, required):
    lookup = {}
    for key, names in aliases.items():
        for name in names:
            lookup[normalize_header(name)] = key
    columns = {}
    for index, name in enumerate(header):
        key = lookup.get(normalize_header(name))
        if not key:
            continue
        if key in columns:
            raise CompareConfigError("存在重复列：%s" % name)
        columns[key] = index
    for key in required:
        if key not in columns:
            raise CompareConfigError("缺少必需列：%s" % key)
    return columns


def _rows(ws):
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise CompareConfigError("工作表为空")
    header = ["" if cell is None else str(cell).strip() for cell in rows[0]]
    body = []
    for raw in rows[1:]:
        cells = ["" if cell is None else str(cell).strip() for cell in raw]
        if not any(cells):
            continue
        if cells[0].startswith("#"):
            continue
        if any(str(cell).startswith("=") for cell in cells):
            raise CompareConfigError("配置表包含公式；请填写实际值")
        body.append(cells)
    return header, body


def _get(cells, columns, key):
    index = columns.get(key)
    return cells[index] if index is not None and index < len(cells) else ""


def load_batch_config(path, src):
    """读取 batch_display.xlsx；无 path 或空路径时改走自动发现。"""
    if not path:
        found = discover(src)
        return BatchConfig(path=None, warnings=found["warnings"], **{
            k: v for k, v in found.items() if k != "warnings"})
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.casefold() != ".xlsx":
        raise CompareConfigError("请选择存在的 batch_display.xlsx 配置表")
    warn_if_excel_open(path)
    try:
        import openpyxl
    except ImportError:
        raise CompareConfigError("读取 batch_display.xlsx 需要 openpyxl") from None
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        if "光谱" not in workbook.sheetnames or "基准" not in workbook.sheetnames                 or "参数" not in workbook.sheetnames:
            raise CompareConfigError("batch_display.xlsx 需要“光谱”“基准”“参数”三张工作表")
        spec_header, spec_body = _rows(workbook["光谱"])
        spec_cols = _columns(spec_header, SPECTRUM_ALIASES, ("file", "group", "point", "scan"))
        ref_header, ref_body = _rows(workbook["基准"])
        ref_cols = _columns(ref_header, REFERENCE_ALIASES, ("ref_id", "file"))
        par_header, par_body = _rows(workbook["参数"])
        if normalize_header(par_header[0]) not in ("参数", "parameter") \
                or normalize_header(par_header[1] if len(par_header) > 1 else "") not in ("值", "value"):
            raise CompareConfigError("参数表前两列应为“参数、值”")
    finally:
        workbook.close()

    params = {}
    for cells in par_body:
        key = PARAMETER_ALIASES_LOOKUP.get(normalize_header(cells[0]))
        if key is None:
            raise CompareConfigError("未知参数：%s" % cells[0])
        if key in params:
            raise CompareConfigError("参数重复：%s" % cells[0])
        params[key] = cells[1] if len(cells) > 1 else ""

    src = Path(src).resolve()
    if not src.is_dir():
        raise CompareConfigError("输入目录不存在：%s" % src)
    warnings = []
    references = []
    for number, cells in enumerate(ref_body, 2):
        rid = str(_get(cells, ref_cols, "ref_id")).strip()
        file_name = str(_get(cells, ref_cols, "file")).strip()
        if not rid or not file_name:
            raise CompareConfigError("基准表第 %d 行缺少基准ID或文件" % number)
        if any(r.ref_id == rid for r in references):
            raise CompareConfigError("基准ID重复：%s" % rid)
        resolved = resolve_input(src, file_name)
        if not resolved.is_file():
            raise CompareConfigError("基准文件不存在：%s" % resolved)
        references.append(BatchReference(
            rid, file_name, resolved,
            str(_get(cells, ref_cols, "label")),
            parse_bool(_get(cells, ref_cols, "enabled"), True, "基准启用"),
            parse_number(_get(cells, ref_cols, "height"), "基准峰高阈值"),
            number))
    if not references:
        raise CompareConfigError("基准表没有数据行")

    scans = []
    seen = set()
    for number, cells in enumerate(spec_body, 2):
        file_name = str(_get(cells, spec_cols, "file")).strip()
        group = str(_get(cells, spec_cols, "group")).strip()
        try:
            point = int(float(str(_get(cells, spec_cols, "point")).strip()))
            scan = int(float(str(_get(cells, spec_cols, "scan")).strip()))
        except (TypeError, ValueError):
            raise CompareConfigError("光谱表第 %d 行的点号/扫描号不是整数" % number) from None
        if not file_name or not group:
            raise CompareConfigError("光谱表第 %d 行缺少文件或组名" % number)
        key = (group, point, scan)
        if key in seen:
            raise CompareConfigError("重复的 (组名,点号,扫描号)：%s" % (key,))
        seen.add(key)
        resolved = resolve_input(src, file_name)
        if not resolved.is_file():
            raise CompareConfigError("光谱文件不存在：%s" % resolved)
        photo = str(_get(cells, spec_cols, "photo")).strip()
        photo_path = resolve_input(src, photo) if photo else None
        if photo and (not photo_path.is_file()
                      or photo_path.suffix.casefold() not in PHOTO_EXTS):
            raise CompareConfigError("实物图不存在或不是图片：%s" % photo_path)
        scans.append(BatchScan(
            file_name, resolved, group, point, scan,
            str(_get(cells, spec_cols, "label")),
            parse_bool(_get(cells, spec_cols, "enabled"), True, "光谱启用"),
            parse_number(_get(cells, spec_cols, "height"), "峰高阈值"),
            photo, source_row=number))
    if not scans:
        raise CompareConfigError("光谱表没有数据行")

    expected_points = parse_number(params.get("expected_points"), "期望点数", nonnegative=True)
    scans_per_point = parse_number(params.get("scans_per_point"), "每点扫描数", nonnegative=True)
    xmin = parse_number(params.get("xmin"), "区间下限")
    xmax = parse_number(params.get("xmax"), "区间上限")
    if (xmin is None) != (xmax is None):
        raise CompareConfigError("区间下限与区间上限必须同时填写")
    if xmin is not None and xmin >= xmax:
        raise CompareConfigError("区间下限必须小于区间上限")
    tolerance = parse_number(params.get("tolerance"), "匹配容差", positive=True)
    zero_guard = parse_number(params.get("zero_guard"), "激光线排除半宽", nonnegative=True)
    shift = parse_number(params.get("shift"), "峰位校正")
    overview_cols = parse_number(params.get("overview_cols"), "总览列数", positive=True)
    overview_peaks = parse_number(params.get("overview_peaks"), "总览峰标签数", nonnegative=True)
    point_peaks = parse_number(params.get("point_peaks"), "单点峰标签数", nonnegative=True)
    representative = str(params.get("representative") or "mean").strip().lower()
    if representative not in REPRESENTATIVES:
        raise CompareConfigError("代表谱只能是 mean / first / none")
    pair1 = str(params.get("pair_group1") or "").strip()
    pair2 = str(params.get("pair_group2") or "").strip()
    active_text = str(params.get("active_references") or "").strip()
    active = [piece.strip() for piece in active_text.replace("，", ",").split(",") if piece.strip()]
    groups = sorted({s.group for s in scans if s.status == "ok"})
    if pair1 and pair1 not in groups:
        raise CompareConfigError("配对组1 %r 不在数据组中" % pair1)
    if pair2 and pair2 not in groups:
        raise CompareConfigError("配对组2 %r 不在数据组中" % pair2)
    if bool(pair1) != bool(pair2):
        raise CompareConfigError("配对组1与配对组2必须同时填写")
    pair_groups = None
    if pair1 and pair2:
        if pair1 == pair2:
            raise CompareConfigError("配对组1与配对组2不能相同")
        pair_groups = (pair1, pair2)
    elif len(groups) >= 2:
        pair_groups = (groups[0], groups[1])
    scans = add_missing_scans(scans, int(expected_points or 0),
                              int(scans_per_point or 0), warnings)
    return BatchConfig(
        path=path, src=src, scans=scans, references=references, groups=groups,
        representative=representative,
        normalize=parse_bool(params.get("normalize"), True, "归一化"),
        xmin=xmin, xmax=xmax,
        tolerance=10.0 if tolerance is None else tolerance,
        shift=0.0 if shift is None else shift,
        zero_guard=50.0 if zero_guard is None else zero_guard,
        overview_cols=int(4 if overview_cols is None else overview_cols),
        overview_peaks=int(0 if overview_peaks is None else overview_peaks),
        point_peaks=int(3 if point_peaks is None else point_peaks),
        enable_paired=parse_bool(params.get("enable_paired"), True, "启用跨组同点图"),
        enable_reference_metrics=parse_bool(
            params.get("enable_reference_metrics"), True, "启用基准指标图"),
        active_references=active,
        home_reference=str(params.get("home_reference") or "").strip(),
        expected_points=int(expected_points or 0),
        scans_per_point=int(scans_per_point or 0),
        pair_groups=pair_groups,
        warnings=warnings,
    )


def write_batch_workbook(path, src=None, scans=None, references=None,
                         params=None, overwrite=False):
    """生成 batch_display.xlsx 模板；可传入自动发现结果预填清单。"""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        raise CompareConfigError("生成 batch_display.xlsx 需要 openpyxl") from None
    path = Path(path).resolve()
    if path.suffix.casefold() != ".xlsx":
        raise CompareConfigError("配置表请使用 .xlsx 扩展名")
    if path.exists() and not overwrite:
        raise CompareConfigError("目标已存在，未覆盖：%s" % path)
    workbook = openpyxl.Workbook()
    try:
        sheet = workbook.active
        sheet.title = "光谱"
        sheet.append(SPECTRUM_HEADERS)
        for scan in scans or []:
            # 缺扫描的占位行 file 为空，只作为 warning 存在，不写进配置表；
            # 否则读取时会被当成“缺少文件”而报错。
            if not getattr(scan, "file", None):
                continue
            sheet.append([scan.file, scan.group, scan.point, scan.scan, scan.label,
                          int(scan.enabled), scan.height, scan.photo])
        reference_sheet = workbook.create_sheet("基准")
        reference_sheet.append(REFERENCE_HEADERS)
        for ref in references or []:
            reference_sheet.append([ref.ref_id, ref.file, ref.label,
                                    int(ref.enabled), ref.height])
        parameter_sheet = workbook.create_sheet("参数")
        parameter_sheet.append(PARAMETER_HEADERS)
        values = dict(params or {})
        for name, default, note in PARAMETER_SPECS:
            parameter_sheet.append([name, values.get(name, default), note])
        info = workbook.create_sheet("说明")
        for line in (
            "Raman 分层批次展示",
            "",
            "用法：run.bat --batch-display batch_display.xlsx --src 输入根目录",
            "不传配置表时可用 --batch-display 空值并显式 --src，走自动发现。",
            "“光谱”一行一条扫描；文件相对 --src，须保留组目录以避开同名文件。",
            "点号是不同位置，扫描号是同点重复；两者不得混为一谈。",
            "“代表谱”和“归一化”只控制显示；定量仍由现有 compare 核心完成。",
            "缺失扫描、无实物图、意外文件会写入清单和 batch_run.json，不会静默补值。",
        ):
            info.append([line])
        for target in (sheet, reference_sheet, parameter_sheet):
            for cell in target[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="315B50")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            target.freeze_panes = "A2"
            target.sheet_view.showGridLines = False
        for name, width in {"A": 34, "B": 18, "C": 10, "D": 10,
                            "E": 22, "F": 12, "G": 12, "H": 26}.items():
            sheet.column_dimensions[name].width = width
        for name, width in {"A": 12, "B": 34, "C": 22, "D": 12, "E": 12}.items():
            reference_sheet.column_dimensions[name].width = width
        for name, width in {"A": 24, "B": 48, "C": 72}.items():
            parameter_sheet.column_dimensions[name].width = width
        info.column_dimensions["A"].width = 110
        for target, column in ((sheet, "F"), (reference_sheet, "D")):
            rule = DataValidation(type="list", formula1='"0,1"', allow_blank=True)
            target.add_data_validation(rule)
            rule.add("%s2:%s1048576" % (column, column))
        _atomic_save(workbook, path)
        return path
    finally:
        workbook.close()
