# Source availability and rebuild instructions

Application source is included in source/ of the portable ZIP, under GPL-3.0-or-later. The app was built from that Python code with Windows CPython 3.13.14 and PyInstaller 6.22.3. Requirements and the build spec are supplied. Library binaries are unmodified upstream distributions; corresponding versioned source/build-system archives can be obtained without charge from the designated upstream locations below. They are not embedded as large source tarballs inside this ZIP.

## Versioned source downloads

- Piper 1.8.0: https://codeload.github.com/OHF-Voice/piper1-gpl/tar.gz/refs/tags/v1.8.0
- eSpeak-NG 724808c, pinned by that Piper version: https://codeload.github.com/espeak-ng/espeak-ng/tar.gz/724808c
- Piper's espeak build flags: https://github.com/OHF-Voice/piper1-gpl/blob/v1.8.0/CMakeLists.txt (also copied into LICENSES/piper-source).
- whisper.cpp / GGML 1.8.6: https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v1.8.6
- whisper Windows binary source release: https://github.com/ggml-org/whisper.cpp/releases/tag/v1.8.6
- ONNX Runtime 1.23.2: https://github.com/microsoft/onnxruntime/tree/v1.23.2
- CPython 3.13.14: https://www.python.org/ftp/python/3.13.14/Python-3.13.14.tar.xz
- Qt Base 6.8.3: https://codeload.github.com/qt/qtbase/tar.gz/refs/tags/v6.8.3
- Qt SVG 6.8.3: https://codeload.github.com/qt/qtsvg/tar.gz/refs/tags/v6.8.3
- Qt Image Formats 6.8.3: https://codeload.github.com/qt/qtimageformats/tar.gz/refs/tags/v6.8.3
- Qt Translations 6.8.3: https://codeload.github.com/qt/qttranslations/tar.gz/refs/tags/v6.8.3
- PySide6 / Shiboken6 6.8.3: https://codeload.github.com/pyside/pyside-setup/tar.gz/refs/tags/v6.8.3
- Full Qt sources: https://download.qt.io/archive/qt/6.8/6.8.3/single/
- Qt/PySide build instructions: https://doc.qt.io/qtforpython-6/building_from_source/index.html
- NumPy 2.2.6 and bundled BLAS source/build information: https://github.com/numpy/numpy/tree/v2.2.6 ; see NumPy's bundled license notice for OpenBLAS details.
- python-sounddevice 0.5.6: https://pypi.org/project/sounddevice/0.5.6/#files ; upstream repository https://github.com/spatialaudio/python-sounddevice
- PortAudio: https://github.com/PortAudio/portaudio ; binary build scripts https://github.com/spatialaudio/portaudio-binaries
- SoundFile 0.14.0: https://pypi.org/project/soundfile/0.14.0/#files ; upstream https://github.com/bastibe/python-soundfile
- libsndfile: https://github.com/libsndfile/libsndfile (see the SoundFile wheel notices for its bundled library version/dependencies).
- python-soxr 1.1.0: https://pypi.org/project/soxr/1.1.0/#files ; upstream https://github.com/dofuuz/python-soxr
- libsoxr: https://sourceforge.net/projects/soxr/files/
- OpenSSL 3.0.21: https://github.com/openssl/openssl/tree/openssl-3.0.21
- Qt software OpenGL Mesa 11.2.2: https://archive.mesa3d.org/older-versions/11.x/11.2.2/mesa-11.2.2.tar.xz
- LLVM 3.6.2 used by that software renderer: https://github.com/llvm/llvm-project/tree/llvmorg-3.6.2
- PyInstaller 6.22.3: https://github.com/pyinstaller/pyinstaller/tree/v6.22.3
- Vazirmatn: https://github.com/rastikerdar/vazirmatn
- Model weights, revisions and hashes: assets/models.json and MODEL-DOWNLOADS.html. Whisper's upstream source: https://github.com/openai/whisper
- Other pinned Python dependencies: source archives are available from each version's PyPI Files page; see requirements.txt and the component notices in LICENSES.

## Build application on Windows x64

Install CPython 3.13 x64. In the source directory, run BUILD-EXE.bat. It creates an isolated environment, installs requirements-build.txt, downloads/verifies the official whisper engine with fetch_engine.py, copies the redistributable MSVC libraries supplied by PySide6, runs unit tests and invokes PyInstaller.

Manual equivalents:

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python fetch_engine.py
.venv\Scripts\python -X utf8 -m unittest discover -s tests -v
.venv\Scripts\python -X utf8 -m PyInstaller --clean --noconfirm Tixi-Voice.spec
```

Output is dist/Tixi-Voice/Tixi-Voice.exe with its _internal directory. Add LICENSES, notices, source/build scripts and model references from the supplied portable release before redistributing. The script does not create an installer or digital signature. Python-source startup is provided by START-SOURCE.bat. The first setup needs internet for dependencies; model weights are installed separately through the UI.

## Modified libraries / relinking

Separate Qt DLLs, whisper engine DLLs and Python native modules can be replaced with compatible modified builds. Qt lives in _internal/PySide6; whisper in _internal/engines/whisper; Piper/eSpeak in _internal/piper; ONNX Runtime in _internal/onnxruntime. For combined rebuilds, install your modified wheels into the build environment after pinned dependency installation and before running PyInstaller. Preserve library names and ABI or rebuild dependent components too.

Piper's upstream CMakeLists contains the espeak static-link flags and exact source commit; rebuilding Piper regenerates its replaceable espeakbridge.pyd. No application integrity check prevents library replacement. Model file checksum checks apply only to the UI's fixed supported model catalog; the source manifest can be changed and the application rebuilt for a different model set.

The distributed EXE was built and tested with Windows binaries under Wine, not on a native Windows machine. Do not package Wine system DLLs; UCRT is provided by Windows 10/11. Native Microsoft runtime DLLs shipped with the official Qt/Python distributions are retained under their own redistribution terms.


## Personal voice 1.1

- OpenVoice upstream source and MIT terms: https://github.com/myshell-ai/OpenVoice
- Pinned community ONNX export: https://huggingface.co/Hinotsuba/OpenVoice-ONNX-v2/tree/bfc3335585a356228f19df7b1ebf36906b731207
- Optional ONNX Runtime DirectML 1.24.4 source: https://github.com/microsoft/onnxruntime/tree/v1.24.4
- Official runtime wheel metadata: https://pypi.org/project/onnxruntime-directml/1.24.4/#files
- Exact optional wheel URL, size and SHA-256: assets/models.json (clone-openvoice). It is not installed over the CPU environment. Preserve this separation when rebuilding. The wheel ABI requires CPython 3.13 x64.
- hardware.py, personal_voice.py and personal_ui.py contain the resource planner, DSP/isolated worker and profile UI. core.py contains the schema-2 migration and local profile lifecycle. Tests include both schema migration and personal-voice behavior.
