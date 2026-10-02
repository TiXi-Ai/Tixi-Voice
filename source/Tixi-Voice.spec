# Windows x64 build. See BUILD-EXE.bat; engines/whisper is staged by fetch_engine.py.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
ROOT=Path(SPECPATH)
ENGINE=Path(os.environ.get('TIXI_ENGINE_DIR',str(ROOT/'engines')))
data=[(str(ROOT/'assets'),'assets')]
data+=collect_data_files('piper',includes=['espeak-ng-data/**'])
data+=collect_data_files('certifi')
# DLL dependencies for whisper-cli must remain beside the engine executable.
for p in (ENGINE/'whisper').glob('*'):
    if p.is_file():data.append((str(p),'engines/whisper'))
a=Analysis([str(ROOT/'main.py')],pathex=[str(ROOT)],binaries=[],datas=data,
    hiddenimports=['PySide6.QtSvg','piper.espeakbridge','_cffi_backend','sounddevice','soundfile','soxr','effects'],
    excludes=['PySide6.QtQml','PySide6.QtQuick','PySide6.QtWebEngineCore','PySide6.QtDesigner',
              'tkinter','torch','onnx','onnxruntime.tools','onnxruntime.quantization','onnxruntime.transformers',
              'onnxruntime.training','sympy','pytest','matplotlib','scipy','pandas','transformers',
              'piper.http_server','piper.train','piper.phonemize_chinese','piper.phonemize_japanese','piper.phonemize_thai'],
    noarchive=False)
# No ASIO driver is used or included. Native Windows UCRT remains an OS dependency.
a.binaries=[x for x in a.binaries if '-asio.' not in x[0].lower() and x[0].lower()!='ucrtbase.dll']
a.datas=[x for x in a.datas if '-asio.' not in x[0].lower()]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Tixi-Voice',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    icon=str(ROOT/'assets'/'tixi.ico'),version=str(ROOT/'windows-version.txt'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Tixi-Voice')
