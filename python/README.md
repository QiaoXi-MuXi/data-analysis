# 便携 Python 运行时

Ra、Raman、CLSM 和 ER 共用这一个解释器，不依赖本机 conda 或 PATH 里的 Python。

```
python\
  python.exe        3.12.10 (64-bit, embeddable)
  python312._pth    已改成 Lib\site-packages + import site
  Lib\site-packages numpy / matplotlib / pillow / openpyxl / pywin32
                    rapidocr-onnxruntime / onnxruntime / pypdfium2 等
  requirements.txt  重装依赖用
```

调用方式（bat 使用相对脚本自身的路径）：

```bat
for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
```

`PYTHONNOUSERSITE=1` 保证只读运行时自带的包，不会误用本机用户 site-packages。

## 迁移

整个 `data analysis` 文件夹一起拷走即可，`python\` 和各模块的相对位置不能变。ER 的中文 OCR 模型随 `rapidocr_onnxruntime` 包保存在 `python\Lib\site-packages\`，正常运行不联网。

## 重装依赖

```bat
python\python.exe -m pip install -r python\requirements.txt
```

## 升级 Python

下载新的 `python-3.x.y-embed-amd64.zip` 覆盖 `python\`，把 `python3xx._pth` 改成：

```
python3xx.zip
.
Lib\site-packages
import site
```

再跑一次上面的 pip 安装命令。

## 可选桌面GUI

桌面工作台使用PySide6-Essentials 6.8.3与shiboken6 6.8.3，单独记录在`requirements-gui.txt`；不改变原命令行依赖表。重装GUI依赖请在项目根目录执行：

```powershell
.\python\python.exe -s -m pip install -r .\python\requirements-gui.txt
```

`-s`排除本机用户site-packages。正常运行通过BAT同样禁用用户包；检查依赖也应在该隔离条件下执行，避免把用户目录中的其他项目包混入检查。
