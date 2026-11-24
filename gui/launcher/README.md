# 桌面启动器维护

产品入口是项目根目录 `DataAnalysis.exe`。它先用共享 `python/python.exe` 检查 GUI 环境，通过后使用同目录 `python/pythonw.exe` 打开原生窗口；不显示控制台、不下载依赖、不修改系统环境变量。原有GUI单实例、项目配置和分析流程继续由 `gui/app.py` 管理。

启动器使用 Windows 自带 .NET Framework 4.x；构建使用其 C# 编译器。便携 Python 和 GUI 仍随完整项目分发，单独复制EXE无法运行。`run_gui.bat`是兼容转发入口，终端执行 `run_gui.bat --check` 会显示检查信息并保留退出码；直接执行 `DataAnalysis.exe --check` 仅检查并写日志，不打开窗口。

在项目根目录的 PowerShell 7 执行：

```powershell
pwsh -NoProfile -File .\gui\launcher\build.ps1
.\python\python.exe -I -B -X utf8 .\gui\launcher\test_launcher.py -v
```

构建脚本使用项目现有 PySide6/Pillow 将 `gui/assets/app.svg` 生成多尺寸 `app.ico`，再编译 `Launcher.cs`；默认输出根目录EXE，编译失败时保留原EXE。无需安装 PyInstaller 或修改 Python 环境。维护时更新源码后重新构建EXE，图标同步用于资源管理器、窗口和任务栏。

日志位于 `out/gui/startup_check.log`、`launcher_error.log`；GUI自身异常沿用 `startup_error.log`。启动器环境检查超时30秒时停止该检查进程并给出中文提示。原始输入和结果工作簿不参与启动检查。
