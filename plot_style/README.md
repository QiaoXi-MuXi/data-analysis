# Plot style package (P1)

`plot_style` is the framework-free shared style package for Raman, Ra, sci-pic, Nanoindentation and GUI figures. Project integration is deferred to P2/P3/P4; this package does not modify project code.

## Stable API

- `FigureStyle`, `PAPER_STYLE`, `STYLE_FIELDS`, `STYLE_SCHEMA_VERSION`
- `resolve_style(excel_settings, project_defaults=None, explicit_overrides=None, source_name="excel", *, strict=False, location=None, row_settings=None)`
- `style_context(style, font_fallback=None)`
- `upgrade_workbook(path_or_workbook, output_path=None, template_rows=None, *, allow_overwrite=False)`
- Parsers: `normalize_header`, `parse_bool`, `parse_number`, `parse_color`, `parse_color_list`, `parse_enum`, `parse_legend_location`, `parse_style_preset`, `parse_output_formats`, `normalize_settings`

## Fields and units

| Field | Default | Unit / accepted values | Chinese aliases |
| --- | --- | --- | --- |
| style_preset | paper | enum: paper | 样式预设 |
| font | project default (None) | font family name; empty inherits | 字体 |
| figure_title_font_size_pt | 8 | pt | 图标题字号（pt） |
| axis_title_font_size_pt | 8 | pt | 轴标题字号（pt） |
| tick_font_size_pt | 7 | pt | 刻度字号（pt） |
| legend_font_size_pt | 7 | pt | 图例字号（pt） |
| value_label_font_size_pt | 8 | pt | 数值标注字号（pt） |
| peak_label_font_size_pt | 10 | pt | 峰值标注字号（pt） |
| min_peak_label_font_size_pt | 8 | pt, valid 6–24; never 5 | 最小标注字号（pt） |
| line_width_pt | 0.75 | pt | 线宽（pt） |
| axis_line_width_pt | 0.7 | pt | 轴线宽（pt） |
| legend_location | auto | auto / best / upper right / upper left / outside right | 图例位置 |
| legend_text_color | #222222 | exact #RRGGBB | 图例文字颜色 |
| series_colors | None | `;`/`；` separated #RRGGBB; consumed cyclically; empty inherits palette | 系列颜色 |
| palette | project default (None) | project palette name; empty inherits | 配色 |
| background_color | #FFFFFF | exact #RRGGBB | 背景颜色 |
| grid_enabled | False | 是/否, 1/0, true/false; empty inherits project default | 网格线 |
| grid_color | #D9D9D9 | exact #RRGGBB | 网格颜色 |
| dpi | 600 | 150–1200, integer | 分辨率（dpi） |
| output_formats | None | png/pdf/svg; empty inherits project default | 输出格式 |

Point fields end in `_pt`, resolution in `_dpi`/`dpi`; future physical sizes must end in `_cm`. There is no ambiguous `font_size` key.

## Precedence and strict mode

Schema defaults < project defaults < Excel settings < optional Excel row settings < explicit CLI overrides. Blank or `None` values inherit from the lower level and are not recorded as changed. `resolve_style` returns `(style, sources)`; each field source is one of `schema`, `default`, `project`, `excel`, `row`, `cli`.

Unknown keys are **ignored with a `UserWarning` by default** and collected in the warning text, including the original parameter, rejected value and optional cell location; pass `strict=True` to turn them into `StyleInputError`. This choice keeps loose legacy workbooks usable while allowing a build-time strict gate. Invalid known values always raise `StyleInputError` naming the original parameter, rejected value and optional location such as `设置表!C4`.

## Matplotlib context

`style_context` temporarily sets fonts (Arial/DejaVu Sans plus Microsoft YaHei/SimHei/Noto Sans CJK SC fallbacks), `mathtext.fontset=dejavusans`, `axes.unicode_minus=False`, `pdf.fonttype=42`, `svg.fonttype="none"`, widths, colors and DPI. It yields the effective rcParams dict and restores all prior rcParams on exit. Missing fonts produce warnings, never errors. `outside right` is normalized to rcParams `upper left`; outside-figure placement remains a caller-level concern.

## Workbook upgrade

`upgrade_workbook` is additive-only: it appends missing field rows to an existing setting sheet, never rewrites/moves existing cells, never changes user sheets, column order or styles, and is idempotent. A path input is saved to a new `*.upgraded*.xlsx` unless `output_path` and `allow_overwrite=True` explicitly permit in-place replacement. Passing a Workbook requires `output_path`.

## Assumptions

- The docstring contains no example code, and `template_rows` accepts a list of row lists so callers control header labels/column order for new sheets.
- Project-specific Raman 200 dpi compatibility, Raman off-grid default, Nano panel rules, and the legacy 5 pt minimum are intentionally not adopted as schema defaults; they belong to project-level defaults or later phases.
- `PAPER_STYLE` is a frozen `FigureStyle` instance; callers create modified copies with `dataclasses.replace` or `resolve_style`.
