# 打包成單一 exe：.\build.ps1
# 產出：dist\拼豆鏡子.exe
# 注意：虛擬環境是用 Anaconda 的 Python 建的，ctypes 需要的 libffi 在 Anaconda\Library\bin，
#       PyInstaller 不會自動帶上，要手動 --add-binary，否則在任何電腦上都會在 import ctypes 時失敗。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$pal = (Resolve-Path palettes).Path
$lb = "C:\Users\User\anaconda3\Library\bin"
& .\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm --clean --onefile --noconsole `
  --name "拼豆鏡子" `
  --add-data "$pal;palettes" `
  --add-binary "$lb\ffi-8.dll;." --add-binary "$lb\ffi-7.dll;." --add-binary "$lb\ffi.dll;." `
  --exclude-module tkinter --exclude-module matplotlib `
  pindou_mirror.py
Copy-Item README.txt dist\ -Force
