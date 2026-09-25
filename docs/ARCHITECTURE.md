# Architecture

Tixi Voice is a single-process PySide6 desktop application. Everything runs in-process;
workers live on `QThreadPool` jobs, never on the GUI thread.

```
src/tixi/
├── app/            composition root: paths, settings, logging, bootstrap, CLI
├── utils/          atomic file helpers (unique_path, ensure_free_space)
├── storage/        SQLite schema, migrations, repositories
├── engines/        model-backed inference, kept behind small interfaces
│   ├── text_normalization/   Persian rules, numbers, sentence splitting
│   ├── diacritization/       neural ONNX engine + lexicon fallback
│   ├── tts/                  Piper
│   └── stt/                  faster-whisper (CTranslate2)
├── audio/          devices, DSP, WAV I/O, format conversion, recorder, playback
├── models/         catalogue, registry, downloader, validator, installer, engine packs
├── services/       feature-level orchestration used by the UI
├── updater/        GitHub Releases client + installer hand-off
├── voice_typing/   hotkeys, controller, text insertion, floating overlay
└── ui/             theme, reusable widgets, ten pages, main window
```

## Layers and the direction of dependencies

```
ui/  ──▶  services/  ──▶  engines/ , audio/ , models/ , storage/ , updater/
                          └──▶  utils/
app/bootstrap.py wires all of the above into a single AppContext
```

* **`ui/` never touches a model, a socket or SQLite.** Pages call services; services
  return plain dataclasses (`ModelRow`, `SynthesisOutcome`, `TranscriptionOutcome`,
  `ExportResult`, …) that the widgets render.
* **`engines/` knows nothing about Qt.** Each engine implements a tiny interface from
  `engines/base.py` (`synthesize`, `transcribe`, `diacritize`, `describe`) so it can be
  unit-tested with stubs — see `tests/test_services.py`.
* **Blocking work is submitted to `services/jobs.py`.** `JobRunner` wraps a callable,
  hands it `progress`/`cancel` callbacks, and delivers results back to the GUI thread
  through Qt signals. Cancellation is cooperative: engines poll `cancel()` between
  chunks/segments, so a cancelled job stops quickly and leaves no half-written file.

## Composition root

`tixi.app.bootstrap.build_context()` creates the `AppContext` exactly once. It owns:

| Group | Members |
| --- | --- |
| Environment | `paths`, `database`, `settings_store`, `settings_repository` |
| Data | `history`, `library_repository`, `dictionary`, `model_registry` |
| Engine plumbing | `engine_packs`, `engine_manager`, `pipeline`, `diacritization` |
| Threading | `jobs` |
| Feature services | `models`, `library`, `synthesis`, `transcription`, `exports`, `updates`, `dictation` (optional), `notes` |
| Lifecycle | `settings()`, `save_settings()`, `refresh_pipeline()`, `note()`, `start()`, `shutdown()` |

`build_context(offline=True)` builds a context that never opens a socket — that is what
`--offline` and the whole test suite use, so tests are hermetic.

## Data flow: text to speech

1. `TextPipeline.process()` runs normalisation (letter folding, ZWNJ, digit
   conversion, number verbalisation, abbreviation expansion, pronunciation dictionary)
   and then the diacritizer, returning the final text **plus a per-stage report** so the
   UI can show a side-by-side diff and let you edit the result.
2. `SynthesisService` splits the text with `sentences.build_chunks()` and streams chunks
   to the engine, so long documents start playing immediately and the progress bar
   reflects real work.
3. `PiperEngine` (or your own voice) returns float samples; `audio/dsp.py` applies gain,
   trims silence and joins chunks with the configured pause.
4. `AudioConverter` writes WAV with the standard library, FLAC/OGG via libsndfile and
   MP3 via `lameenc` — all to a temporary file that is renamed into place, and never
   over an existing file unless you pass `overwrite=True` (`utils/atomic.unique_path`
   picks `name (2).ext` instead).

## Data flow: speech to text and voice typing

1. `audio/recorder.py` records through `sounddevice`, applying gain, an optional noise
   gate and a level meter; audio is kept in RAM and only written when you ask for it.
2. `TranscriptionService` pre-processes (high-pass, denoise, resample to 16 kHz mono)
   and calls the engine with VAD settings from `settings.stt`.
3. The result is a `TranscriptionResult` with segments, word timings, language and
   confidence. Subtitle renderers produce SRT, VTT, JSON, Markdown and plain text.
4. Voice typing chains the same services from a global hotkey
   (`voice_typing/controller.py`): hotkey → record → transcribe → optional dictation
   commands (`voice_typing/commands.py`) → `TextInserter.insert()`.
5. `TextInserter` types with `SendInput`-style Unicode injection, falling back to the
   clipboard method with a configurable delay and clipboard restore; it captures the
   target window handle when recording starts so focus changes cannot misroute text,
   and it refuses to paste into apps on the block list.

## Storage

SQLite (`storage/database.py`) with a versioned migration chain
(`storage/migrations.py`, `SCHEMA_VERSION = 3`). One connection per thread through a
thread-local factory; WAL is enabled so a long transcription never blocks the UI's
reads. Tables cover history, audio library assets, dictionary entries, model installs,
settings mirror, and job notes. Every user-owned file lives under
`%LOCALAPPDATA%\TixiVoice` (or `$XDG_DATA_HOME/tixivoice` elsewhere):

```
config/   settings.json, window state
models/   downloaded model weights, and engine_packs/<pack>/ for pip-style runtimes
data/     tixi.db, library audio, exports, learned overrides
logs/     tixi.log (rotating) and crash reports
```

Deleting a model directory is safe: the registry reconciles at startup and offers a
re-download. Uninstalling asks before removing `data/` — your recordings are never
deleted silently.

## Engines and the engine packs

The installer contains Python, PySide6-Essentials, NumPy, sounddevice, soundfile,
requests and lameenc — nothing else. Piper, faster-whisper and ONNX Runtime are
**engine packs**: `models/engine_packs.py` resolves the correct wheel for your Python
and platform, verifies the SHA-256 published by PyPI, unzips it into
`engine_packs/<pack>/`, and appends that directory to `sys.path`. That keeps the
shipped application small while still giving you real, local inference — and GPL
components are only ever downloaded by you, never redistributed inside the installer.

If a wheel has no PyPI-published SHA-256, Tixi Voice refuses to install it. In Offline
Mode all pack installs and model downloads are blocked with an explanatory message.

## Diacritization

`DiacritizationService` holds an optional neural engine and always has the lexicon
engine available:

* **Neural path** — `engines/diacritization/onnx_engine.py` runs the CANINE
  classification head through ONNX Runtime, feeds raw text (no re-normalisation, so
  indices stay aligned), takes `argmax` per character and slices away the `[CLS]`/`[SEP]`
  positions. Smart mode drops marks below the 0.55 confidence threshold.
* **Fallback path** — `LexiconDiacritizer` looks words up in the curated lexicon
  (Arabic yeh/kaf folded so an Arabic keyboard still hits the entry), then applies
  morphology rules for plurals, verb suffixes and the Ezafe kasra. Unknown words are
  left untouched, and the result carries a warning string the UI always displays.

`describe()` reports which engine is active so the UI can label results honestly, and
`coverage_report()` tells you what share of a text the lexicon actually knows.

## Updates

`updater/releases.py` talks to the GitHub Releases API for `TiXi-Ai/Tixi-Voice` with an
ETag cache and rate-limit handling. A downloaded installer is verified against the
release's SHA-256 (and refuses to run if the asset advertises an invalid signature),
then launched detached with `/SILENT /NORESTART /CLOSEAPPLICATIONS` — and only from a
frozen Windows build, never from a source checkout. Automatic checks can be disabled,
are throttled by `updates.check_interval_hours`, and never download without consent.

## Threading rules

* Every long operation goes through `JobRunner`; the GUI thread only paints.
* Engines expose `unload()` and the manager respects `ai.unload_after_idle_s` /
  `ai.max_loaded_models`, so RAM comes back after a job.
* The recorder and playback use dedicated callbacks; the UI reads levels through
  signals.
* Single-instance is enforced at startup (Win32 mutex + window lookup, PID lock file
  elsewhere), so two copies can never fight over the same SQLite database.
