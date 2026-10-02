# Licenses and notices — Tixi Voice 1.1

Tixi Voice application code is provided under **GPL-3.0-or-later**. See LICENSE.txt. The application source, model manifest, tests and build scripts are in source/ in the portable distribution. There is no warranty, signature lock, anti-tamper restriction or prohibition on rebuilding/relinking/replacing the libraries. Reverse engineering to debug library modifications is permitted.

The user's supplied **Tixi logo** is not relicensed by this project; all rights remain with its respective owner. The logo PNG is unchanged and the ICO is a resized derivative. **Vazirmatn** is by Saber Rastikerdar and contributors, under SIL OFL 1.1, with its license retained in assets/fonts and LICENSES.

## Bundled components

- CPython 3.13.14: PSF and bundled component licenses. Python's full Windows LICENSE.txt includes Microsoft redistributable conditions and libffi notices. SQLite is public domain. OpenSSL 3.0.21 notices are retained.
- PySide6 Essentials, Shiboken6 and Qt 6.8.3: applicable open-source licenses, including LGPL v3 for the linked Qt/PySide libraries. No commercial Qt license is claimed. Separate replaceable Qt DLLs are used. Upstream Qt copyright/license/third-party attribution files are in LICENSES.
- Piper 1.8.0: GPL-3.0-or-later. Its eSpeak-NG bridge statically includes eSpeak-NG sources at commit 724808c (as pinned by Piper's upstream CMakeLists); GPL texts, Unicode data notices and build source references are included. No training dependencies are bundled.
- whisper.cpp / GGML 1.8.6: MIT, official Windows CPU release. The included executable is run as a subprocess, not a server. Microsoft MSVCP/VCRUNTIME/VCOMP DLLs are copied from the official PySide6 wheel beside this engine. No CUDA/GPU build or SDL interface is used.
- ONNX Runtime 1.23.2: MIT, plus upstream ThirdPartyNotices.txt. CPU provider only; application code disables telemetry events.
- NumPy 2.2.6 and bundled OpenBLAS: their BSD and component licenses/notices.
- python-sounddevice 0.5.6 and PortAudio: MIT notices. Only non-ASIO Windows x64 PortAudio is used; ASIO binaries are not included.
- SoundFile 0.14.0, bundled libsndfile and its codec dependencies: BSD/LGPL and their respective component notices. DLLs remain replaceable.
- python-soxr 1.1.0 and libsoxr: their respective BSD/LGPL notices; replaceable binary module.
- CFFI, requests, urllib3, certifi, charset-normalizer, idna, packaging, coloredlogs, humanfriendly, flatbuffers and incidental Python dependencies: their upstream licenses are in LICENSES. Inclusion of build dependency notices does not imply every build tool is bundled at runtime.
- PyInstaller 6.22.3: GPL with its bootloader/distribution exception, included in LICENSES.

## Downloaded models (not bundled)

Model weights are fetched only with the user's explicit download action. The pinned upstream revisions, exact URLs and SHA-256 digests are in assets/models.json. Download and inference do not upload the user's speech or text.

- Persian Amir medium and English LJ Speech medium: their original MODEL_CARD texts are retained in assets/notices. These identify the datasets/licensing information (CC0 for the Amir dataset; public domain for LJ Speech). Dataset/model terms are separate from the application's GPL license; no additional rights to third-party voices or training data are granted here. Review the model's source/card for your intended use.
- Whisper Base/Small/Medium quantized models: upstream OpenAI Whisper model license and source references apply. Model size and quality do not imply perfect transcription or guaranteed accuracy.

Source locations, versions and replacement/rebuild instructions are in SOURCE-LINKS.md. Full GPL/LGPL texts, Qt attribution files and third-party copyright notices are included in LICENSES. Keep these notices with redistributions and meet the respective source-availability obligations; this notice does not replace the license texts.


## Optional personal-voice components (downloaded, not bundled as weights/runtime)

- OpenVoice V2: MyShell authors; MIT license. License text and the community export card are retained in assets/notices and LICENSES/openvoice. Application inference is an independent NumPy/ONNX adaptation, without PyTorch, MeloTTS, Whisper reference extraction or training libraries. It transfers tone color to Piper-generated speech; it is not a native Persian OpenVoice TTS model.
- Hinotsuba/OpenVoice-ONNX-v2: community ONNX export, revision bfc3335585a356228f19df7b1ebf36906b731207. The card attributes MyShell/OpenVoice and describes MIT weights. This is not an official Microsoft or MyShell Windows application/export. SHA-256 pins are in the model manifest.
- onnxruntime-directml 1.24.4 CP313 Windows AMD64: optional official Microsoft PyPI wheel. ONNX Runtime MIT and bundled DirectML/component terms apply; complete upstream LICENSE/ThirdPartyNotices from that wheel are in LICENSES/onnxruntime-directml-optional and the installed runtime. The executable extension is disclosed in the download confirmation. It stays separate from bundled CPU ONNX Runtime 1.23.2 and runs only in isolated workers.
- The custom conversion path adds ordinary removable RIFF INFO metadata declaring AI-generated audio. It does not integrate OpenVoice's optional wavmark library and does not claim an audible/robust watermark.
- Voice-reference consent is recorded locally. No identity-verification service or additional rights to someone else's voice are supplied.
