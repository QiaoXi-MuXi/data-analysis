# -*- coding: utf-8 -*-
"""CLSM二维高度图和三维表面图；不改变槽宽槽深计算。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

DPI = 300
SURFACE_STEP = 2
Z_EXAGGERATION = 30.0


def _prepare(result):
    """使用导出高度，恢复缺失掩码；坐标平移仅建立图幅局部原点。"""
    x = np.asarray(result["x"], dtype=float).copy()
    x -= x[0]
    z = np.asarray(result["Z"], dtype=float).copy()
    if z.ndim != 2 or z.shape[1] != x.size:
        raise ValueError("高度矩阵与X坐标不匹配")
    z[np.asarray(result["missing"], dtype=bool)] = np.nan
    finite = z[np.isfinite(z)]
    if not finite.size:
        raise ValueError("没有真实有效高度，无法绘图")
    dy = float(result["dy"])
    if not np.isfinite(dy) or dy <= 0:
        raise ValueError("Y步距必须为正数")
    y = np.arange(z.shape[0], dtype=float) * dy
    limits = (float(finite.min()), float(finite.max()))
    if limits[0] == limits[1]:
        limits = (limits[0] - 0.05, limits[1] + 0.05)
    return x, y, z, limits


def plot_surfaces(results, output_root):
    """每个样品生成一幅2D图和一幅3D图，并记录显示参数。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.ticker import MaxNLocator

    root = Path(output_root)
    folder_2d, folder_3d = root / "2D", root / "3D"
    folder_2d.mkdir(parents=True, exist_ok=True)
    folder_3d.mkdir(parents=True, exist_ok=True)
    # 低处蓝色，中部浅绿/米色，高处暖棕，参考附件的地形表面风格。
    cmap = LinearSegmentedColormap.from_list(
        "clsm_surface", ["#35678c", "#94bfd0", "#dce8df", "#e7ddbe", "#bf846c"], N=256)
    cmap.set_bad("white", alpha=0)
    records = []
    style = {"font.family": "DejaVu Sans", "font.size": 11,
             "axes.labelsize": 12, "axes.titlesize": 15,
             "axes.edgecolor": "#5b6670", "axes.labelcolor": "#273641",
             "xtick.color": "#42515e", "ytick.color": "#42515e",
             "savefig.facecolor": "white"}
    with plt.rc_context(style):
        for result in results:
            x, y, z, limits = _prepare(result)
            norm = Normalize(*limits)
            name = Path(result["path"]).stem
            assumed = bool(result.get("dy_assumed", True))
            y_note = (f"Y spacing assumed from X: {result['dy']:.4g} µm"
                      if assumed else f"Y spacing: {result['dy']:.4g} µm")

            fig, ax = plt.subplots(figsize=(8.2, 7.2))
            fig.subplots_adjust(left=.12, right=.83, bottom=.15, top=.90)
            image = ax.pcolormesh(x, y, np.ma.masked_invalid(z), shading="nearest",
                                 cmap=cmap, norm=norm, rasterized=True)
            ax.set(xlim=(x[0], x[-1]), ylim=(y[0], y[-1]),
                   xlabel="X (µm)", ylabel="Y (µm)", title=f"{name}   2D topography")
            ax.set_aspect("equal")
            ax.xaxis.set_major_locator(MaxNLocator(5))
            ax.yaxis.set_major_locator(MaxNLocator(5))
            bar = fig.colorbar(image, ax=ax, fraction=.046, pad=.045)
            bar.set_label("Height Z (µm)", labelpad=10)
            bar.outline.set_linewidth(.6)
            fig.text(.5, .045, y_note, ha="center", fontsize=9, color="#65717b")
            path_2d = folder_2d / f"{name}.png"
            fig.savefig(path_2d, dpi=DPI)
            plt.close(fig)

            # 仅对显示网格抽取原始节点，包含末行末列；不作高度平滑或插值。
            iy = np.unique(np.r_[np.arange(0, z.shape[0], SURFACE_STEP), z.shape[0]-1])
            ix = np.unique(np.r_[np.arange(0, z.shape[1], SURFACE_STEP), z.shape[1]-1])
            xx, yy = np.meshgrid(x[ix], y[iy])
            zz = z[np.ix_(iy, ix)]
            fig = plt.figure(figsize=(10.8, 8.0))
            ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
            fig.subplots_adjust(left=.02, right=.86, bottom=.10, top=.91)
            surface = ax.plot_surface(xx, yy, zz, cmap=cmap, norm=norm,
                                      rcount=len(iy), ccount=len(ix), linewidth=0,
                                      antialiased=True, shade=False, alpha=1.0)
            ax.set(xlim=(x[0], x[-1]), ylim=(y[0], y[-1]), zlim=limits,
                   xlabel="X (µm)", ylabel="Y (µm)", zlabel="Height Z (µm)")
            ax.set_title(f"{name}   3D surface", pad=12)
            ax.view_init(elev=30, azim=-55)
            ax.set_proj_type("ortho")
            ax.set_box_aspect((x[-1]-x[0], y[-1]-y[0],
                               (limits[1]-limits[0])*Z_EXAGGERATION), zoom=.90)
            for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
                axis.set_major_locator(MaxNLocator(5))
                axis.set_pane_color((1, 1, 1, 0))
            ax.tick_params(labelsize=9, pad=1)
            ax.xaxis.labelpad = 8
            ax.yaxis.labelpad = 8
            ax.zaxis.labelpad = 8
            bar_ax = fig.add_axes([.875, .26, .018, .43])
            bar = fig.colorbar(surface, cax=bar_ax)
            bar.set_label("Height Z (µm)", labelpad=10)
            bar.outline.set_linewidth(.6)
            bar.ax.tick_params(labelsize=9)
            fig.text(.5, .045, f"Z display exaggerated ×{Z_EXAGGERATION:g}; {y_note}",
                     ha="center", fontsize=9, color="#65717b")
            path_3d = folder_3d / f"{name}.png"
            fig.savefig(path_3d, dpi=DPI)
            plt.close(fig)
            records.append(dict(
                input=Path(result["path"]).name,
                input_sha256=hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest(),
                output_2d=str(path_2d.relative_to(root)), output_3d=str(path_3d.relative_to(root)),
                matrix_shape=list(z.shape), finite_height_range_um=[float(np.nanmin(z)), float(np.nanmax(z))],
                color_limits_um=list(limits), missing_pixels=int(np.isnan(z).sum()),
                x_range_um=[float(x[0]), float(x[-1])], y_range_um=[float(y[0]), float(y[-1])],
                dy_um=float(result["dy"]), dy_assumed=assumed,
                output_2d_sha256=hashlib.sha256(path_2d.read_bytes()).hexdigest(),
                output_3d_sha256=hashlib.sha256(path_3d.read_bytes()).hexdigest()))
            print(f"  形貌图 {name}: 2D + 3D", flush=True)
    manifest = dict(source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    height_source="exported measured Z; missing pixels masked; no leveling or smoothing",
                    color_scale="same limits within each sample's 2D/3D pair; limits may differ between samples",
                    dpi=DPI, surface_grid_step=SURFACE_STEP, vertical_display_exaggeration=Z_EXAGGERATION,
                    elevation_deg=30, azimuth_deg=-55, projection="orthographic", files=records)
    (root / "surface_plot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return manifest


def main(argv=None):
    """单独更新形貌图，不写入Excel、测量审计或质量掩膜。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extract_groove import find_inputs, read_3d
    parser = argparse.ArgumentParser(description="仅导出CLSM二维/三维形貌图")
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent / "data" / "3d")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    parser.add_argument("--dy", type=float, default=None)
    args = parser.parse_args(argv)
    results = []
    for path in find_inputs(args.dir):
        x, z, inferred_dy, missing = read_3d(path, return_mask=True)
        results.append(dict(path=path, x=x, Z=z, missing=missing,
                            dy=inferred_dy if args.dy is None else args.dy, dy_assumed=args.dy is None))
    if not results:
        parser.error("输入目录中没有形貌TXT文件")
    plot_surfaces(results, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
