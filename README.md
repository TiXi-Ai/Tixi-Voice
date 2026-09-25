# Tixi Voice

**Offline speech tools with Persian-first text processing — text to speech, transcription,
global push-to-talk dictation and context-aware diacritization.**

Tixi Voice runs entirely on your own computer. After a model has been downloaded once, the
application needs no internet connection, no account and no cloud service: audio never leaves
the machine.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Home dashboard · Text to Speech · Speech to Text · Voice Typing         │
│  AI Models · Audio Library · History · Settings · Updates · About        │
└──────────────────────────────────────────────────────────────────────────┘
```

## Highlights

| Feature | What it does |
| --- | --- |
| **Text to Speech** | Local neural voices (Piper). Persian voices built in (`fa_IR` Amir / Gyro), 14 more languages available. Sentence chunking with pauses, speed/pitch/volume, WAV/FLAC/OGG/MP3 (+Opus/M4A with FFmpeg). |
| **Persian text normalisation** | Arabic → Persian letter folding (ي→ی, ك→ک), ZWNJ repair, number/date/time/currency verbalisation (`۱۴۰۳/۰۵/۱۲` → «دوازدهم مرداد هزار و چهارصد و سه»), Persian punctuation and spacing. |
| **Diacritization** | Neural CANINE model (ONNX) predicts one mark per letter from sentence context — *smart* (only confident marks) and *full* modes, side-by-side diff, manual editing, per-word overrides. Without the neural model a clearly-labelled curated lexicon is used instead. |
| **Speech to Text** | faster-whisper (tiny → large-v3-turbo) on CPU or GPU, VAD filtering, word timestamps, live-editable transcript, speaker labels in captions, SRT/VTT/JSON/Markdown/TXT export. |
| **Voice Typing** | Hold `Ctrl+Shift+Space` anywhere in Windows, speak, and the Persian text is typed at the caret of the focused window with Unicode keystrokes (clipboard fallback, UIPI/secure-desktop aware, blocked-app list). |
| **Audio Library** | Every clip you generate, record or import, with metadata, tags, favourites, playback, waveform, gap detection for missing files and bulk export. |
| **History** | Searchable log of everything produced, with pinning, retention policy, CSV/JSON/Markdown export and one-click “send back to the editor”. |
| **AI Models** | Catalogue of every model with licence, languages, hardware needs and download size. Install, verify, import your own files or remove. Nothing is downloaded without an explicit click, and resumable partial downloads are kept. |
| **Updates** | Checks the GitHub Releases API of `TiXi-Ai/Tixi-Voice`, verifies the SHA-256 of the installer and never runs anything without confirmation. Offline Mode turns every network call off. |
| **Themes** | Light / dark / follow-Windows, seven accents plus a custom colour, adjustable radius, density and scale, full QSS theming with WCAG-checked text contrast. |

## Requirements

* Windows 10 or 11 (64-bit). The application also runs on Linux/macOS for development —
  global hotkeys, text insertion and tray integration are Windows-only features and are
  reported as such instead of failing silently.
* ~500 MB free disk space for the application plus at least one Whisper model
  (`small` ≈ 487 MB is the recommended starting point for Persian).
* A microphone for dictation and transcription.

## Install

1. Download `TixiVoice-Setup-<version>.exe` from
   [Releases](https://github.com/TiXi-Ai/Tixi-Voice/releases) and run it.
2. Start Tixi Voice and open **AI Models**.
3. Install the **engine packs** (Piper for speech output, faster-whisper for recognition).
   These are downloaded from PyPI as verified wheels — they are *not* part of the installer,
   which keeps it small and avoids shipping GPL code.
4. Install at least one Persian voice (≈63 MB) and one Whisper model.
5. Dictate: hold `Ctrl+Shift+Space` in any text field.

Nothing above is required to open the application: every page explains what is missing and
what to do about it.

## Build from source

```bash
git clone https://github.com/TiXi-Ai/Tixi-Voice
cd Tixi-Voice
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m tixi            # run from the source tree
```

Useful switches:

```
python -m tixi --diagnostics      # versions, paths, installed models (no GUI)
python -m tixi --safe-mode        # ignore stored appearance settings
python -m tixi --offline          # force Offline Mode for this run
python -m tixi --reset-settings   # restore defaults (keeps history/models)
python -m tixi --open models      # start on a specific page
```

### Packaging

```powershell
pyinstaller packaging\tixi-voice.spec --noconfirm --clean
iscc packaging\installer.iss /DAppVersion=1.0.0
```

The PyInstaller spec keeps the bundle under 110 MB by trimming Qt to the modules the UI
imports and by excluding every machine-learning runtime: engines are engine packs, models
are downloads.

## Tests

```bash
.venv\Scripts\python -m pytest              # unit + service tests
.venv\Scripts\python -m pytest -m gui       # UI smoke tests (needs a Qt platform plugin)
```

The test suite covers the Persian text pipeline (normalisation, number verbalisation,
sentence segmentation), the diacritization lexicon and ONNX plumbing, the DSP helpers, the
audio I/O and converter, the storage layer, the job runner, the model catalogue/validator and
the bootstrapping of every service — including the rule that no file is ever overwritten
silently.

## Documentation

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the layers fit together
* [`docs/MODELS.md`](docs/MODELS.md) — every model with licence, languages and hardware needs
* [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — the usual causes and fixes
* [`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md) — the checklist used before a release
* [`docs/PRIVACY.md`](docs/PRIVACY.md) — exactly what is stored where

## Licence

Tixi Voice is MIT licensed. Piper is GPL-3.0-or-later and is therefore *never* bundled: it is
installed as a separate engine pack with its licence text. See `docs/MODELS.md` for every
component and its licence.
