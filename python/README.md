# 便携 Python 运行时

Ra 和 Raman 两个脚本共用这一个解释器，不依赖本机 conda 或 PATH 里的 Python。

```
python\
  python.exe        3.12.10 (64-bit, embeddable)
  python312._pth    已改成 Lib\site-packages + import site
  Lib\site-packages numpy / matplotlib / pillow / openpyxl / pywin32
  requirements.txt  重装依赖用
```

调用方式（两个 bat 里都是这样写的，路径相对脚本自身）：

```bat
for %%i in ("%~dp0..\python\python.exe") do set "PYEXE=%%~fi"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
```

`PYTHONNOUSERSITE=1` 保证只读运行时自带的包，不会误用本机用户 site-packages。

## 迁移

整个 `data analysis` 文件夹一起拷走即可，`python\` 和 `Ra\`、`Raman\` 的相对位置不能变。

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
