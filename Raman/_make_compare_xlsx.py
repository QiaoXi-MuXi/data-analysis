# -*- coding: utf-8 -*-
"""生成 Raman compare.xlsx 模板；默认拒绝覆盖用户已有配置。"""
import argparse
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from compare_config import CompareConfigError, write_compare_workbook  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 Raman 基准对比配置模板")
    parser.add_argument("path", nargs="?", default=str(HERE / "compare.xlsx"))
    parser.add_argument("--force", action="store_true", help="覆盖已有目标文件")
    args = parser.parse_args(argv)
    try:
        target = write_compare_workbook(args.path, [], {
            "基准文件": "reference/基准.txt",
            "基准名称": "",
            "基准峰高阈值": "",
            "区间下限": "",
            "区间上限": "",
            "匹配容差": "10",
            "归一化(0/1)": "1",
            "峰位校正": "0",
            "激光线排除半宽": "50",
        }, overwrite=args.force)
    except CompareConfigError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    print("wrote", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
