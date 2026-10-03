# Tixi Voice — Offline Persian/English Speech Studio

![CI](https://github.com/TiXi-Ai/Tixi-Voice/actions/workflows/ci.yml/badge.svg)
![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue.svg)
![Platform: Windows x64](https://img.shields.io/badge/Platform-Windows%20x64-informational.svg)
![Python: 3.13](https://img.shields.io/badge/Python-3.13-3776AB.svg)
![Offline](https://img.shields.io/badge/Offline-100%25%20local-brightgreen.svg)

A fully **offline**, **portable** speech studio for Windows x64 that runs **entirely on your machine** — no installation, no Python, no account, no API, and no cloud.

- **Text-to-Speech** — Persian & English voices powered by [Piper TTS](https://github.com/rhasspy/piper).
- **Personal Voice Cloning** (experimental) — clone the *color* of your own voice with OpenVoice ONNX + DirectML.
- **Speech-to-Text** — [Whisper](https://github.com/openai/whisper) (Persian & English).
- **Global Dictation** — hold `Ctrl+Shift+Space` in any app and speak; it types for you.

> 📖 فارسی: [متن کامل فارسی](README.md)

---

## Run (Portable EXE)

1. **Extract All** the ZIP.
2. Open the **Tixi-Voice** folder and run **Tixi-Voice.exe**.
3. Keep the **`_internal`** folder next to the EXE. No Python, account, API, or subscription needed.
4. In **Offline Models**, download the model you need once. No internet required afterwards.

This package has no installer and no commercial digital signature. Target: Windows 10/11 x64 with an AVX2 CPU; 8 GB RAM and at least 1.5 GB free space recommended. Normal TTS and STT run on CPU. Personal voice uses DirectML when compatible, otherwise falls back to low-power CPU — no CUDA or PyTorch install required. Compatibility with all hardware is not guaranteed. ARM64, 32-bit and Windows 7/8 are not targets of this package.

If Windows or your antivirus warns, inspect the file and its source; do not disable your security tool.

## Models — Separate From the App

Sizes are approximate, in MiB:

| Model | Use | Approx. download |
|---|---|---:|
| Persian · Amir | Persian text → speech | 61 MB |
| English · LJ Speech | English text → speech | 61 MB |
| Whisper Small Q5_1 | Speech → text (Persian/English), balanced | 181 MB |
| Whisper Base Q5_1 | Lighter & faster, less accurate | 57 MB |
| My Voice · OpenVoice ONNX + DirectML | Personal voice color | 177 MB |
| Whisper Medium Q5_0 | Heavier, expects higher accuracy (esp. Persian) | 514 MB |

You don't need to install every model. The two Persian/English voices + Small total about **303 MB**. Persian-only + Small is about **242 MB**.

This release does not guarantee error-free output or cloud-service quality. Review Persian names, numbers, punctuation and speech. Downloads only happen on user request, from Hugging Face (and the official Microsoft package on PyPI for the DirectML plugin), verified with SHA-256. Nothing you type or convert is sent to any server.

## Features

### Text to Speech
- Type/paste text or load a TXT file (UTF-8/UTF-16); up to 10,000 characters per conversion.
- Persian/Amir or English/LJ Speech; pick the language matching your text.
- Speed 0.6×–1.6×, build, play, stop, and WAV export.
- Sound effects: robot, girl, boy, cartoon squirrel, deep, echo, and phone — applied locally on the same output.
- Files are saved to local history. No autoplay.

### My Voice — Experimental
1. In **Offline Models**, install the **My Voice · OpenVoice** package and a base language model.
2. Open **My Voice**; read the sample **20–45 seconds** with your microphone (acceptable range 8–60 s), then stop.
3. Name the profile, confirm the voice is yours (or you have permission), and **Build Voice Profile**.
4. The app switches to Text-to-Speech and selects the profile. Up to 20 local profiles are kept.
5. **This is not full model training or a perfect identity copy.** Pronunciation and accent come from Piper; OpenVoice approximates the voice color.

### Speech to Text & Microphone
- Input WAV, MP3, FLAC, OGG; up to 512 MB and 90 minutes per file.
- Microphone: choose input, start recording, stop, then convert to text (not live/streaming).
- Editable text with copy and UTF-8 TXT export; SRT output with approximate timing.
- Microphone is only opened when recording starts (Windows permission: Settings → Privacy → Microphone).

### Global Dictation
- Enable **Dictation** at the bottom of the Speech-to-Text tab. Global hotkey: `Ctrl+Shift+Space` (on by default).
- In any app, **hold** the key, speak, release — text is typed into the active window. Max 120 s per hold.
- Persian/English auto-detected. Everything is local.

## Privacy
- Default data folder: `%LOCALAPPDATA%\TixiVoice`
- `history.sqlite3`, `outputs`, `models`, `profiles`, `runtimes`, `backups`, `temp`, `recordings`, `application.log`
- Data is **not encrypted**. Deleting a result also deletes its generated WAV; the original input file is untouched.
- No text or audio is sent to a server; there is no online service or Google integration. ONNX Runtime telemetry is disabled in the app code.
- **Personal voice samples are stored locally only**, without encryption, on this device. Do not use for fraud, impersonation, or bypassing voice authentication. An "AI-generated" notice is recorded in the UI/history and in WAV metadata (metadata can be removed; it is not tamper-resistant).

## Keyboard Shortcuts
- Ctrl+1: Text to Speech · Ctrl+2: Speech to Text · Ctrl+3: History · Ctrl+4: Models · Ctrl+5: My Voice
- Ctrl+O: Open file · Ctrl+Enter: Start conversion · Ctrl+Shift+Space: Global dictation · Esc: Cancel/stop

## Upgrade from 1.0
Close the app, back up the data folder, and extract the new ZIP into a **separate** folder; don't merge `_internal` across versions. The database upgrades to schema 2; 1.0 must not run on the upgraded database.

## Run from Source & Rebuild
Code lives in `source` (included with the EXE).

- Install Python 3.13 x64 on Windows.
- Run from source: `START-SOURCE.bat`
- Rebuild the EXE: `BUILD-EXE.bat` (in `source`)

Both need internet on first run for dependencies and the whisper engine. Dependencies are pinned exactly in `requirements.txt`.

## Testing
Full report in `TESTING.md`. Tests ran with Windows Python and the real EXE under Wine, not a native Windows install. Real Persian/English conversion, UI and storage were verified; a real microphone/speaker wasn't available in the test environment. Verify audio quality and accuracy on your own files and hardware.

## License
- App code: **GPL-3.0-or-later**
- The user logo, font, and other third-party components have separate licenses.
- See `LICENSE.txt`, `THIRD-PARTY.md`, `SOURCE-LINKS.md` and the `LICENSES` folder.
