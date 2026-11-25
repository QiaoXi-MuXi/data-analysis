# -*- coding: utf-8 -*-
"""按磁盘上实际存在的数据重建 Raman 的配置表。

不同表的填充策略不一样，所以按 --target 分派，一套一套实现：

  stack    --src 里每个 txt 一行（顶层 + 一级子目录，子目录名写成 `<目录>/<文件>.txt`）；
           行序跟目录一致；同名行原有的 label / normalize / peaks 等改动保留；
           height 默认按每条谱自适应重算，--keep-height 保留手填值；
           --exclude 可排除若干文件（按相对路径或文件名匹配）。
  montage  --src 里每组 <组名>-1-1.png 配 <组名>-<序号>.txt，一行一条谱；
           同组同文件原有的设置保留；新行沿用该组的归一化与颜色顺序。

用法:
    python _fill_stack.py                              # stack.xlsx, data/
    python _fill_stack.py --target montage --src ..\1100
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _make_stack_xlsx import (KEYS as STACK_KEYS, norm_header,
                              write_workbook)  # noqa: E402
from plot_raman import auto_threshold, load_txt, natural_key  # noqa: E402
from raman_montage import (MONTAGE_COLORS, MONTAGE_KEYS, discover_groups,
                           montage_header, split_spectrum)  # noqa: E402
import _make_montage_xlsx  # noqa: E402

TARGETS = ("stack", "montage")


# --------------------------------------------------------------------- helpers
def file_key(name):
    """文件名归一成小写、反斜杠、.txt 结尾，用来对表里的行和磁盘上的文件。

    支持一级子目录（`1100\\1-1.txt`），这样 1100 与 1210 里同名的谱不会撞行。
    """
    text = str(name or "").strip().replace("/", "\\")
    while text.startswith(".\\"):
        text = text[2:]
    text = text.lower()
    return text if text.endswith(".txt") else text + ".txt"


def scan_stack_files(src):
    """src 顶层 + 一级子目录里的 txt，返回相对路径（顶层不带前缀）。

    先列顶层文件，再按名字自然序列子目录，保证图上 0 系列在最下、两个
    数据集按 1100 / 1210 的顺序排。
    """
    files, dirs = [], []
    for entry in sorted(os.listdir(src), key=natural_key):
        if entry.startswith("~$"):
            continue
        path = os.path.join(src, entry)
        if os.path.isdir(path):
            dirs.append(entry)
        elif entry.lower().endswith(".txt"):
            files.append(entry)
    names = list(files)
    for d in dirs:
        for f in sorted(os.listdir(os.path.join(src, d)), key=natural_key):
            if f.lower().endswith(".txt") and not f.startswith("~$"):
                names.append(os.path.join(d, f))
    return names


def _text(value):
    return "" if value is None else str(value).strip()


def _to_bool(value, default=False):
    text = _text(value).lower()
    if not text:
        return default
    return text in ("1", "1.0", "y", "yes", "true", "是")


def _open_error(path):
    print("ERROR: %s 正被 Excel 打开，先关掉再运行" % os.path.basename(path),
          file=sys.stderr)
    return 1


# ----------------------------------------------------------------------- stack
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
                pos = {k: header.index(k) for k in STACK_KEYS if k in header}
                continue
            if "file" not in pos or pos["file"] >= len(cells):
                continue
            name = str(cells[pos["file"]] or "").strip()
            if not name:
                continue
            out = [cells[pos[k]] if k in pos and pos[k] < len(cells) else None
                   for k in STACK_KEYS]
            rows.append((name, out))
        return rows
    finally:
        wb.close()


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


def fill_stack(stack, data_dir, keep_height=False, exclude=()):
    files = scan_stack_files(data_dir)
    if exclude:
        import fnmatch
        files = [f for f in files
                 if not any(fnmatch.fnmatch(f, pat) or
                            fnmatch.fnmatch(os.path.basename(f), pat)
                            for pat in exclude)]
    if not files:
        print("ERROR: %s 里没有 txt 文件（或都被 --exclude 排除了）" % data_dir,
              file=sys.stderr)
        return 1

    try:
        existing = read_existing(stack)
    except PermissionError:
        return _open_error(stack)

    old = {file_key(n): (n, row) for n, row in existing}
    # 新行的 normalize 跟着已有行走，避免混填报错；没有已有行时用 1
    norm = 1
    for _, row in existing:
        v = str(row[STACK_KEYS.index("normalize")] or "").strip().lower()
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
        row = [None] * len(STACK_KEYS)
        row[STACK_KEYS.index("file")] = f
        folder, stem = os.path.split(os.path.splitext(f)[0])
        # 子目录里的谱默认把目录写进图例，否则 1100 和 1210 的 1-1 同名没法区分
        if folder:
            row[STACK_KEYS.index("label")] = "%s %s" % (folder, stem)
        row[STACK_KEYS.index("normalize")] = norm
        row[STACK_KEYS.index("peaks")] = 1
        rows.append(row)
        added.append(row)

    dropped = [n for n, _ in old.values()]

    # 自适应峰高阈值: 默认每条谱重算, --keep-height 保留原表手填值
    hi = STACK_KEYS.index("height")
    fi = STACK_KEYS.index("file")
    if not keep_height:
        for row in rows:
            thr = auto_height(row[fi], data_dir)
            if thr is not None:
                row[hi] = thr

    try:
        write_workbook(stack, rows)
    except PermissionError:
        return _open_error(stack)

    print("data 里有 %d 个 txt，stack.xlsx 现在 %d 行" % (len(files), len(rows)))
    if keep_height:
        print("峰高阈值: 保留原表手填值 (--keep-height)")
    else:
        print("峰高阈值: 已按每条谱自适应重算 (max(5.5σ, 1.1%×最强峰))")
    if added:
        print("新加 %d 行:" % len(added))
        for row in added:
            print("  " + row[fi])
    if dropped:
        print("data 里已不存在，删掉 %d 行:" % len(dropped))
        for n in dropped:
            print("  " + n)
    if not added and not dropped:
        print("表格本来就和 data 一致（已按原样重写）")
    return 0


# --------------------------------------------------------------------- montage
def read_montage_existing(path):
    """读 montage.xlsx 的数据行，返回 [{key: value}]。

    组名留空的行按文件名推（与 raman_montage.load_montage 口径一致），
    这样少写一列的表格也能正确对上。
    """
    if not os.path.exists(path):
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        rows, idx = [], None
        for raw in wb.worksheets[0].iter_rows(values_only=True):
            cells = [None if c is None else c for c in raw]
            if not any(_text(c) for c in cells):
                continue
            if idx is None:
                header = [montage_header(c) for c in cells]
                idx = {k: i for i, k in enumerate(header) if k in MONTAGE_KEYS}
                continue
            if _text(cells[0] if cells else "").startswith("#"):
                continue

            def get(name):
                i = idx.get(name)
                return cells[i] if i is not None and i < len(cells) else None

            group = _text(get("group"))
            name = _text(get("file"))
            if not group and not name:
                continue
            if not group:
                parsed = split_spectrum(os.path.splitext(name)[0])
                group = parsed[0] if parsed else ""
            if not group or not name:
                # 残缺行交给出图时校验，这里跳过避免把表写坏
                continue
            row = {"group": group}
            for k in MONTAGE_KEYS[1:]:
                row[k] = get(k)
            rows.append(row)
        return rows
    finally:
        wb.close()


def fill_montage(path, src):
    """按 src 里的实物图/光谱重建 montage.xlsx。

    同组同文件的 label / normalize / peaks / height / color / offset /
    区间 一律保留；新行沿用该组的归一化设置，颜色按组内顺序。
    """
    src = os.path.abspath(src)
    if not os.path.isdir(src):
        print("ERROR: 输入目录不存在: %s" % src, file=sys.stderr)
        return 1

    groups, notes = discover_groups(src)
    for note in notes:
        print("提示: " + note)
    if not groups:
        print("ERROR: %s 里没有能配成组的实物图+光谱（实物图要叫 <组名>-1-1.png）"
              % src, file=sys.stderr)
        return 1

    try:
        existing = read_montage_existing(path)
    except PermissionError:
        return _open_error(path)

    old = {}
    for row in existing:
        old[(row["group"], file_key(row["file"]))] = row

    # 每组的归一化以已有行为准；同组里出现两种值就先报错，
    # 免得填完表还是要在出图时才发现
    norm_by_group = {}
    for (group, _fk), row in old.items():
        value = _to_bool(row.get("normalize"), None)
        if value is None:
            continue
        prev = norm_by_group.setdefault(group, value)
        if prev != value:
            print("ERROR: %s 里组 %s 的归一化不统一，先统一成全 0 或全 1"
                  % (os.path.basename(path), group), file=sys.stderr)
            return 1

    rows, added, dropped = [], [], []
    for g in groups:
        name = g["name"]
        photo = os.path.basename(g["photo"])
        norm = norm_by_group.get(name, False)
        for k, r in enumerate(g["rows"]):
            hit = old.pop((name, file_key(r["file"])), None)
            if hit is not None:
                row = dict(hit)
                row["group"] = name
                row["photo"] = photo
                rows.append(row)
                continue
            row = {"group": name, "photo": photo, "file": r["file"],
                   "label": r["label"],
                   "normalize": 1 if norm else 0,
                   "peaks": 1,
                   "height": None,
                   "color": r["color"] or MONTAGE_COLORS[k % len(MONTAGE_COLORS)],
                   "offset": None, "xmin": None, "xmax": None}
            rows.append(row)
            added.append(row)

    dropped = [row for row in old.values()]

    out_rows = []
    for row in rows:
        cells = []
        for k in MONTAGE_KEYS:
            v = row.get(k)
            if k in ("normalize", "peaks") and v is not None and v != "":
                v = int(_to_bool(v))
            elif v == "":
                v = None
            cells.append(v)
        out_rows.append(cells)

    try:
        _make_montage_xlsx.write_workbook(path, out_rows, overwrite=True)
    except PermissionError:
        return _open_error(path)

    total = len(rows)
    print("%s 里 %d 组、%d 条谱，%s 现在 %d 行"
          % (src, len(groups), sum(len(g["rows"]) for g in groups),
             os.path.basename(path), total))
    if added:
        print("新加 %d 行:" % len(added))
        for row in added:
            print("  %s / %s" % (row["group"], row["file"]))
    if dropped:
        print("表里这些行在 %s 里找不到，已删掉 %d 行:" % (src, len(dropped)))
        for row in dropped:
            print("  %s / %s" % (row["group"], row["file"]))
    if not added and not dropped:
        print("表格本来就和输入目录一致（已按原样重写）")
    return 0


# ------------------------------------------------------------------------ main
def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    target = "stack"
    src = None
    path = None
    keep_height = False
    excludes = []
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--keep-height":
            keep_height = True
        elif a in ("--target", "-t"):
            target = next(it, "")
        elif a.startswith("--target="):
            target = a.split("=", 1)[1]
        elif a in ("--src", "-s"):
            src = next(it, "")
        elif a.startswith("--src="):
            src = a.split("=", 1)[1]
        elif a in ("--path", "-p"):
            path = next(it, "")
        elif a.startswith("--path="):
            path = a.split("=", 1)[1]
        elif a == "--exclude":
            excludes.append(next(it, ""))
        elif a.startswith("--exclude="):
            excludes.append(a.split("=", 1)[1])
        else:
            rest.append(a)

    if rest:
        print("未知参数: %s" % " ".join(rest), file=sys.stderr)
        return 1
    target = (target or "stack").strip().lower()
    if target not in TARGETS:
        print("ERROR: --target 只支持 %s（其余表格再逐步接入）"
              % " / ".join(TARGETS), file=sys.stderr)
        return 1

    if target == "stack":
        data_dir = os.path.abspath(src) if src else os.path.join(HERE, "data")
        if not os.path.isdir(data_dir):
            print("ERROR: 输入目录不存在: %s" % data_dir, file=sys.stderr)
            return 1
        return fill_stack(path or os.path.join(HERE, "stack.xlsx"),
                          data_dir, keep_height, excludes)

    if not src:
        print("ERROR: --target montage 需要 --src（放实物图和光谱 txt 的目录）",
              file=sys.stderr)
        return 1
    return fill_montage(path or os.path.join(HERE, "montage.xlsx"), src)


if __name__ == "__main__":
    sys.exit(main())
