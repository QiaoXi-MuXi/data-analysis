# -*- coding: utf-8 -*-
"""生成 Raman batch_display.xlsx 模板；默认拒绝覆盖用户已有配置。

可选 --src 用真实数据自动预填“光谱”“基准”两张表，便于本批数据直接改。
"""
import argparse
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from batch_display_config import (CompareConfigError, discover,
                                   write_batch_workbook)  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 Raman 分层批次展示配置模板")
    parser.add_argument("path", nargs="?", default=str(HERE / "batch_display.xlsx"))
    parser.add_argument("--src", default=None,
                        help="可选输入根目录；给定时自动预填光谱与基准清单")
    parser.add_argument("--force", action="store_true", help="覆盖已有目标文件")
    args = parser.parse_args(argv)
    scans, refs = [], []
    if args.src:
        if not os.path.isdir(args.src):
            print("ERROR: 输入目录不存在：%s" % args.src, file=sys.stderr)
            return 1
        info = discover(args.src, expected_points=20, scans_per_point=3)
        scans, refs = info["scans"], info["references"]
        for note in info["warnings"]:
            print("提示：%s" % note)
    try:
        target = write_batch_workbook(args.path, src=args.src, scans=scans,
                                      references=refs, overwrite=args.force)
    except CompareConfigError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    print("wrote %s（%d 扫描，%d 基准）" % (target, len(scans), len(refs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
