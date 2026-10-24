# Data Analysis

Windows 下的实验数据处理工具，包含 Zygo 面粗糙度 Ra 提取与 Raman 光谱绘图。

## 目录与运行

- `Ra/`：将 Zygo 截图放入 `Ra/data/`，双击 `Ra/run_ra.bat`。详见 [Ra 说明](Ra/README.md)。
- `Raman/`：将两列光谱 TXT 放入 `Raman/data/`，使用 `stack.xlsx` 配置堆叠图，双击 `Raman/run.bat`。详见 [Raman 说明](Raman/README.md)。
- `python/`：两个工具共用的便携 Python 3.12 和依赖，随仓库保留；无需修改本机 Python 或 Conda 环境。详见 [运行时说明](python/README.md)。

首次克隆后，按需自行创建 `Ra/data/` 和 `Raman/data/` 并放入本地实验数据。Git 不保存空目录。

## GitHub 同步范围

仓库用于保存代码、文档、配置表与便携运行时。`.gitignore` 排除以下内容：

- 任意层级的 `data/` 和 `out/`，包括其中所有文件。
- Ra 的生成结果 `Ra/ra_results.xlsx` 和 `Ra/surface_plots/`。
- Python 缓存、Office 临时文件与 `.env` 等本地环境配置。

`Raman/stack.xlsx` 是绘图配置表，纳入版本管理。忽略规则只影响 Git，不删除或修改本地数据。

后续同步：

```powershell
git status
git add .
git diff --cached --stat
git commit -m "更新分析工具"
git push
```

提交前检查暂存清单；不要使用 `git add -f` 强制添加数据或结果。
