# -*- mode: python ; coding: utf-8 -*-
# Mac 打包設定：在 Mac 上執行 `pyinstaller pindou_mac.spec`，產出 dist/PindouMirror.app
# NSCameraUsageDescription 一定要有，否則 macOS 不會跳出相機授權，程式也拿不到畫面。
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

a = Analysis(
    ["pindou_mirror.py"],
    datas=[("palettes", "palettes"), ("assets", "assets")] + collect_data_files("mediapipe"),
    binaries=collect_dynamic_libs("mediapipe"),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PindouMirror",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PindouMirror")
app = BUNDLE(
    coll,
    name="PindouMirror.app",
    bundle_identifier="com.whatsthemix.pindoumirror",
    info_plist={
        "CFBundleDisplayName": "拼豆鏡子",
        "CFBundleName": "拼豆鏡子",
        "CFBundleShortVersionString": "1.1.0",
        "NSCameraUsageDescription": "拼豆鏡子需要使用相機，把畫面即時變成拼豆圖；對鏡頭比 5 可以拍下拼豆照片。",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.entertainment",
    },
)
