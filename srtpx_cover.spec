# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller srtpx_cover.spec  (hoặc chạy build_exe.bat)
a = Analysis(["run_app.py"], pathex=["."], datas=[], hiddenimports=[],
             excludes=["numpy", "PIL", "pytest", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="srtpx_cover", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="srtpx_cover")
