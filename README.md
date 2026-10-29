# Data Analysis

Windows 下的实验数据处理工具，包含 Zygo 面粗糙度 Ra 提取、Raman 光谱绘图与 CLSM 槽宽/槽深测量。

## 目录与运行

- `Ra/`：将 Zygo 截图放入 `Ra/data/`，双击 `Ra/run_ra.bat`。详见 [Ra 说明](Ra/README.md)。
- `Raman/`：将两列光谱 TXT 放入 `Raman/data/`，使用 `stack.xlsx` 配置堆叠图，双击 `Raman/run.bat`。详见 [Raman 说明](Raman/README.md)。
- `CLSM/`：将共聚焦高度矩阵 TXT 放入 `CLSM/data/3d/`，双击 `CLSM/run_clsm.bat`。每次运行在写入结果前清空整个 `CLSM/out/`，仅保留本次输出；长期保留的结果请另存到 `out/` 外，自检不清理。采用多位置条带平均剖面测量剩余槽深和开口宽度，测量结果写入 `CLSM/out/paper_v4/`，二维高度图、三维表面图分别写入 `CLSM/out/2D/` 和 `CLSM/out/3D/`。Excel 第一页为核心结论，第二页为完整汇总，后续各页保留测量明细和敏感性对照。详见 [CLSM 说明](CLSM/README.md)。
- `python/`：三个工具共用的便携 Python 3.12 和依赖，随仓库保留；无需修改本机 Python 或 Conda 环境。详见 [运行时说明](python/README.md)。

首次克隆后，按需自行创建 `Ra/data/`、`Raman/data/` 和 `CLSM/data/3d/` 并放入本地实验数据。Git 不保存空目录。CLSM 的主脚本 `extract_groove.py`、`profile_measurement.py` 与 `surface_plots.py` 需放在同一目录；只有 CMP 后形貌时，输出为剩余槽深，不能计算绝对去除量。

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

新模块的代码、配置和说明会随 `sync_github.bat` 自动纳入同步；任意层级的 `data/`、`out/` 及全部内容都排除，包括模块下的嵌套子目录。新模块的结果统一写入 `out/`；不在这些目录里的普通文件会被纳入同步，独立生成目录需另加忽略规则。现有 Ra 的旧输出位置已单独排除，保持原工具行为。

Git 不记录空目录；只有 `data/` 和 `out/` 的模块不会出现在仓库中，应至少添加代码或说明。不要在模块自己的 `.gitignore` 中重新放行这些目录，也不要强制添加数据或输出文件。

## GitHub 同步范围

仓库用于保存代码、文档、配置表与便携运行时。`.gitignore` 排除以下内容：

- 任意层级的 `data/` 和 `out/`，包括其中所有文件。
- Ra 的生成结果 `Ra/ra_results.xlsx` 和 `Ra/surface_plots/`。
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
