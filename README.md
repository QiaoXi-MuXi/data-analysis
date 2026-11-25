# Data Analysis

Windows 下的实验数据处理工具，包含 Zygo 面粗糙度 Ra 提取、Raman 光谱绘图（单谱/堆叠/拼图/基准对比）与 CLSM 槽宽/槽深测量。

## 桌面工作台

双击根目录 **`DataAnalysis.exe`** 打开 Windows 原生桌面窗口，在左侧切换 Ra、Raman、CLSM、ER 和 sci-pic 科研绘图。启动时不弹控制台，应用与任务栏使用统一图标；重复启动切回已有窗口，启动失败显示中文提示。Ra/Raman 支持直接编辑样本表和右侧参数、批量设置、撤销/重做；项目以 JSON 保存，兼容导入 Excel/CSV 和导出 CSV。Raman 新增“基准对比分析”模式：一个基准谱对多个样品做峰一一匹配、缺失/新增峰识别、峰位偏差和全谱 Pearson，并归档配置与基准摘要。界面提供后台分析、折叠日志、运行历史及图片/工作簿/CSV/JSON结果预览。

命令行运行和Excel配置继续可用。GUI每次分析在所选输出位置下新建独立的 `runs/时间戳_编号/`，保存配置快照、输入摘要和结果，结果页按本次产物显示；Ra/ER默认位于模块的 `out/runs/`。CLSM的GUI入口不执行整目录清理。任务运行中可切换项目，同模块不重复启动；完成后再关闭工作台。命令行运行的结果位置及处理行为保留。

Ra样本页提供“一键配置”：可按BMP/DAT等后缀批量设置数值标注开关、字号、颜色、位置和描边，支持预览及一次撤销；Excel配置导出包含相同的可编辑标注列，原有Excel和JSON继续兼容。修改后保存项目，并使用“样本配置”模式重新分析以生成新图。

```powershell
.\run_gui.bat --check
```

GUI使用共享便携Python，新增可选依赖锁定在`python/requirements-gui.txt`；正常启动不安装或下载。需要重装时运行`.\python\python.exe -s -m pip install -r .\python\requirements-gui.txt`。详见[桌面工作台说明](gui/README.md)。

EXE为便携启动器，移动或分发时复制完整项目目录，保留同级 `python/`、`gui/` 与业务模块。`run_gui.bat`作为兼容入口转交同一EXE；`run_gui.bat --check`可在终端显示启动诊断。启动器源码与重建方式见[启动器维护](gui/launcher/README.md)。

## 目录与运行

各模块仅保留一个 `run.bat`，直接启动处理脚本；重复的旧启动器已合并移除。使用前先放入输入、检查配置，保存关闭 Excel 后运行。

**Excel 占用约定**：写同名结果工作簿的模块（Ra、CLSM、Nanoindentation）在运行前预检，若文件正被 Excel / WPS 打开，立即提示保存并关闭后重跑（退出码 4），不会先清空旧结果；配置或输入工作簿只读，若正在被打开，程序提示本次使用磁盘上已保存的版本、未保存的修改不生效。ER 与 sci-pic 每次新建输出文件，已有结果被打开不影响下一次运行。

| 项目 | 输入与配置 | 启动入口 | 主要输出 |
|---|---|---|---|
| [Ra](Ra/README.md) | BMP/图片、Zygo DAT；`ra_config.xlsx` 控制启用行 | `Ra/run.bat` | `Ra/ra_results.xlsx`、`Ra/out/` |
| [Raman](Raman/README.md) | `Raman/data/` 两列 TXT；`stack.xlsx` 配置堆叠图，`montage.xlsx` 配置拼图，`compare.xlsx` 配置基准对比 | `Raman/run.bat`；拼图用 `Raman/run.bat --montage`，基准对比用 `Raman/run.bat --compare compare.xlsx` | 默认分目录：`Raman/out/single/`、`out/stack/`、`out/montage/`、`out/compare/` |
| [CLSM](CLSM/README.md) | `CLSM/data/` 内 TXT/LSM（无需 `3d/` 子目录）；命令行参数 | `CLSM/run.bat` | `CLSM/out/results/groove_paper_results.xlsx`、`CLSM/out/paper_v5/`、`CLSM/out/figures/` |
| [ER](ER/README.md) | `ER/templates/` Excel 模板、`ER/data/` PDF/图片票据 | `ER/run.bat` | `ER/物品验收单_时间戳.xlsx`、`ER/out/` |
| [sci-pic](sci-pic/README.md) | `templates/` 中的 Excel 模板；GUI 提供样式预览 | `sci-pic/run.bat` | `sci-pic/out/运行时间_编号/` |
| [Nanoindentation Analysis](Nanoindentation%20Analysis/README.md) | `Nanoindentation Analysis/data/` 内 MTS TestWorks `.xls`；`fill_config.bat` 导入 `config.xlsx` 后由用户设置分组/图例/阈值 | `Nanoindentation Analysis/run.bat` | `Nanoindentation Analysis/out/` |
| [python](python/README.md) | 共用便携 Python 3.12 与依赖 | 无需激活 Conda | 不自动安装、更新依赖 |

`sci-pic/` 已接入 EXE 的“科研绘图”：先浏览两种模板预览，点击“填写数据”创建 Excel 副本，保存后生成并预览 PNG/PDF/SVG。模板集中在 `templates/`，样式图集中在 `previews/`。也可使用原命令行入口：填写 `sci-pic/templates/sci_pic.xlsx` 后双击 `sci-pic/run.bat`，每次在 `sci-pic/out/` 下独立归档 PNG、PDF、SVG、输入快照和数据。详见 [SCI 绘图模板](sci-pic/README.md)。新克隆可能没有空的 `data/`、`out/`，按各模块说明创建输入目录即可；`package.bat` 分发出的压缩包已为每个模块预建这两个空目录。

## 统一检查与运行约定

双击 `check.bat`，或在根目录执行 `./check.bat --no-pause`。检查包括解释器位置、依赖导入、模块入口、源码语法及批处理换行；检查不会处理数据、改写配置或清理输出。检查通过仅说明代码与运行环境基础可用，不代表测量算法或票据识别准确。

- 全部入口使用项目 `python/python.exe`，禁用用户包和 Python 字节码缓存，清除继承的 `PYTHONPATH`、`PYTHONHOME`；缺少共享解释器时停止，不调用系统 Python。
- 命令行批量运行时可先设置 `$env:DATA_ANALYSIS_NO_PAUSE='1'`，让失败直接返回退出码；成功/失败状态保留。该变量仅影响当前终端及子进程，不修改系统环境。Raman 无参数单谱模式仍需要交互输入。
- Python/Markdown 使用 UTF-8；现有 BAT 使用 GBK + CRLF。`.editorconfig` 约定后续编辑格式，不批量重排现有科学代码。
- 各模块 README 均按“输入 / 配置 / 运行 / 输出 / 环境检查”提供快速使用表。

## 数据与方法边界

原始输入默认只读。CLSM 默认运行会在输入检查通过后清理本模块 `out/` 下的本次结果（`out/runs/` 历史归档保留）；长期保存结果请放到 `out/runs/` 或其他 `out/` 之外的位置，试算按模块说明指定独立输出。

Ra 的 `fill_config.bat` 默认按当前 `data/` 清理过期配置行并备份；显式 `--dir` 时仍为追加导入。Raman 的 `fill_stack.bat` 按当前 TXT 重建配置行，运行前核对输入目录。所有模块自动发现输入时按文件名自然顺序排列（`1-1`、`1-2`、`1-10`、`2-1`、`10-1`），不会把 `10-1` 排在 `2-1` 前；用户已在配置表中写好的行顺序不受影响。Raman 基准对比必须用 `--compare` 显式启动，不会因存在 `compare.xlsx` 自动切换；匹配只代表峰位差在容差内，不代表物相鉴定。

CLSM 只测已有形貌，不能由单次扫描推导绝对去除量；paper-v5 槽宽仍有浅壁边界误差候选，论文采用前需独立核验。LSM 高度比例、符号及有效掩码未确认时不得直接发布定量结论。Ra 去噪/高通为候选处理，ER 的 OCR 品名仍需人工复核。

## 新增模块约定

后续模块直接放在项目根目录，保持如下结构即可，无需修改同步脚本或逐个添加忽略规则：

```text
新模块/
  README.md       使用说明
  run.bat         启动入口（按需提供）
  分析脚本.py      代码与必要的配置文件
  data/           本地输入数据，不上传
  out/            本地生成结果，不上传
```

新模块的代码、配置和说明会随 `sync_github.bat` 自动纳入同步；任意层级的 `data/`、`out/` 及全部内容都排除，包括模块下的嵌套子目录。新模块的结果统一写入 `out/` 的二级子目录（如 `out/results/`、`out/figures/`），按类别归类；不在这些目录里的普通文件会被纳入同步，独立生成目录需另加忽略规则。现有 Ra、ER 的模块根目录结果仍按明确规则排除；CLSM 的生成结果已全部统一到 `CLSM/out/`，随 `out/` 规则整目录忽略。

Git 不记录空目录；只有 `data/` 和 `out/` 的模块不会出现在仓库中，应至少添加代码或说明。不要在模块自己的 `.gitignore` 中重新放行这些目录，也不要强制添加数据或输出文件。

## GitHub 同步范围

仓库用于保存代码、文档、配置表与便携运行时。`.gitignore` 排除以下内容：

- 任意层级的 `data/` 和 `out/`，包括其中所有文件。
- Ra 的生成结果 `Ra/ra_results.xlsx` 和 `Ra/surface_plots/`。
- CLSM 的整个 `CLSM/out/`（结果工作簿、`paper_v5` 图表与审计、`figures` 二维三维图、`runs` 历史结果）、`.backups/` 以及旧版根目录工作簿。
- ER 根目录的生成 XLSX。
- Python 缓存、Office 临时文件与 `.env` 等本地环境配置。

`Raman/stack.xlsx` 是绘图配置表，纳入版本管理。忽略规则只影响 Git，不删除或修改本地数据。

## 一键同步到 GitHub

双击项目根目录的 `sync_github.bat`，自动暂存改动、生成带时间的提交并推送到本私有仓库。没有文件改动时跳过提交，仍会推送此前尚未上传的提交。完成或失败都会停留显示结果。

- 需要已安装 Git、GitHub CLI，并通过 `gh auth login` 登录。
- 只在本仓库的 `main` 分支运行，目标固定为 `QiaoXi-MuXi/data-analysis`。
- 使用现有 `.gitignore`，额外检查数据与结果的忽略规则；发现已被强制跟踪的 `data/`、`out/` 或 Ra 结果时停止同步。
- 自动提交包括文件新增、修改和删除；本地文件不会因同步脚本被删除。
- 推送失败时保留本地提交。脚本不会自动拉取、合并或强制推送；远端有新提交时先手动处理分支差异，再重试。
- 正在合并、变基或拣选时停止，避免误提交中间状态。

也可以从终端运行：

```powershell
.\sync_github.bat
.\sync_github.bat --no-pause
```

`--no-pause` 适合终端调用；成功返回 0，失败返回 1。提交前仍应确认项目改动；不要用 `git add -f` 强制添加数据或结果。

## 分发打包

双击 `package.bat`，按提示逐项选择 Ra、Raman、CLSM、ER、sci-pic 和纳米压痕模块（直接回车打包，输入 `n` 跳过）；便携 Python、GUI、启动器和说明文件固定包含。在项目根目录生成 `DataAnalysis_时间_模块.zip`。任意层级的 `data/`、`out/`、`__pycache__` 及生成结果均排除，根目录的批量原始数据目录也不打包。终端调用可用 `.\package.bat --no-pause`。

解压后的目录结构约定（压缩包内只有一层 `data analysis/` 顶层目录，解压后直接得到完整项目根，不会把文件散落到解压位置，也不会和旁边已有文件混在一起）：

```text
data analysis/
  DataAnalysis.exe  run_gui.bat  check.bat  README.md
  python/  gui/  out/
  Ra/       data/  out/
  Raman/    data/  out/
  CLSM/     data/  out/       （sci-pic 只有 out/，模板在 templates/）
  ER/       data/  out/
  Nanoindentation Analysis/  data/  out/
```

打包时每个被选模块的空 `data/`、`out/` 会被自动补建，用户拿到压缩包就能看到输入该放哪里、结果会出现在哪里：把原始数据放进对应模块的 `data/`，运行 `run.bat` 或双击 `DataAnalysis.exe`，结果写入对应模块的 `out/`。目录本身随包分发，`data/` 内不放任何占位文件，避免被模块当成输入扫描。
