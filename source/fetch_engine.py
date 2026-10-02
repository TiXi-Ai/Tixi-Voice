# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare the official Windows x64 whisper.cpp engine for a source build."""
from pathlib import Path
import hashlib
import io
import shutil
import zipfile
import requests
import PySide6

URL='https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.6/whisper-bin-x64.zip'
SHA256='b07ea0b1b4115a38e1a7b07debf581f0b77d999925f8acb8f39d322b0ba0a822'
NEEDED={'whisper-cli.exe','whisper.dll','ggml.dll','ggml-base.dll','ggml-cpu.dll'}
RUNTIMES={'msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','vcruntime140.dll','vcruntime140_1.dll','vcomp140.dll'}

def prepare(folder=None):
    folder=Path(folder or Path(__file__).resolve().parent/'engines'/'whisper');folder.mkdir(parents=True,exist_ok=True)
    response=requests.get(URL,timeout=(20,90));response.raise_for_status();data=response.content
    if hashlib.sha256(data).hexdigest()!=SHA256:raise RuntimeError('Engine archive checksum mismatch')
    found=set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            basename=Path(name).name
            if basename in NEEDED:
                (folder/basename).write_bytes(archive.read(name));found.add(basename)
    if found!=NEEDED:raise RuntimeError('Engine archive is incomplete')
    # Official Microsoft redistributable runtimes included with the pinned PySide6 wheel.
    qt=Path(PySide6.__file__).resolve().parent
    for name in RUNTIMES:
        source=next((x for x in qt.iterdir() if x.name.lower()==name),None)
        if source is None:raise RuntimeError('Missing runtime in PySide6: '+name)
        shutil.copy2(source,folder/name)
    print('Windows whisper.cpp 1.8.6 engine prepared:',folder)

if __name__=='__main__':prepare()
