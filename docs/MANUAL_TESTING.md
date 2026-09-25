# Manual test checklist

Use this before a release. It covers the things an automated suite cannot: real audio
devices, global hotkeys, focus stealing and the Windows installer.

Environment for a full pass: Windows 11 x64, Python 3.11, no network after step 4.

```
git clone https://github.com/TiXi-Ai/Tixi-Voice
cd Tixi-Voice
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                 # must be green before anything else
set PYTHONPATH=src
python -m tixi --diagnostics        # prints versions; exit code must be 0
```

## 0. Automated suite (must pass first)

- [ ] `python -m pytest -q` → all tests pass, no skips other than platform-marked ones.
- [ ] `python -m compileall -q src\tixi` → clean.

## 1. Cold start

- [ ] `python -m tixi` opens the dashboard with no console errors.
- [ ] Delete `%LOCALAPPDATA%\TixiVoice`, start again: settings, database and folders are
      recreated without an error dialog.
- [ ] Start a second copy → the existing window is focused instead of a second instance.
- [ ] `python -m tixi --safe-mode` opens with the default theme and no engines loaded.
- [ ] `python -m tixi --open updates` opens directly on the requested page.
- [ ] Kill the process mid-job, restart: the log shows no corruption and the database
      still opens.

## 2. Engines and models (needs network once)

- [ ] *AI Models* lists the four engine packs with size, licence and the exact
      modules each provides.
- [ ] Install **Piper** — the wheel's SHA-256 is verified, the pack is unpacked to
      `engine_packs\piper`, and the page reports it as installed after a restart.
- [ ] Install **faster-whisper** the same way.
- [ ] Download `piper-fa-ir-amir` and `whisper-small`; progress, speed and remaining
      time update, and Cancel really stops the transfer with a resumable `.part` file.
- [ ] Start a download, cancel it, then resume: the final size matches the catalogue.
- [ ] Turn on *Offline Mode* and try to download again → a clear message appears, and
      nothing is written to disk.
- [ ] Remove a model → files are deleted and the row returns to "not installed".
- [ ] Import a valid Piper `.onnx` + `.onnx.json` pair → it appears and can be selected.
- [ ] Try to import a broken file → validation refuses it and explains why.

## 3. Persian normalisation and diacritization

- [ ] Type `كتاب من روي ميز است` (Arabic yeh/kaf) → the output uses `کتاب من روی میز است`.
- [ ] Type `می  روم` → the double gap becomes ZWNJ: `می‌روم`.
- [ ] Type `امروز ۱۴۰۳/۰۵/۱۲ ساعت ۱۰:۳۰ است و ۳۵ درصد` → dates, times and percentages
      are verbalised, and the digits keep their Persian form before verbalisation.
- [ ] Switch diacritization to *full* and *smart*: smart adds fewer marks, and the diff
      view highlights exactly what changed.
- [ ] Edit the diacritized text by hand → the correction is stored as a learned override
      and offered next time.
- [ ] Long text (> 2046 chars for the neural model) is split and rejoined with no
      characters lost.
- [ ] With **no** diacritization model installed, the workspace still works and says the
      result is approximate — the phrase "approximate" must be visible.
- [ ] ZWNJ, mixed Persian/English (`Tixi Voice را باز کن`) and punctuation survive the
      whole pipeline unchanged apart from the intended normalisation.

## 4. Text to speech

- [ ] Paste a Persian paragraph, press *Generate*: audio plays back, the waveform
      appears, and the elapsed time is plausible for the model used.
- [ ] Change speed/pitch/volume → the next render reflects it and the settings persist
      after a restart.
- [ ] Switch voices mid-session (Amir ↔ Gyro) without restarting.
- [ ] Generate a 5 000-character document: the UI stays responsive, progress advances,
      and Cancel stops within a couple of seconds.
- [ ] Export to WAV, FLAC, MP3 and OGG; each file plays in an external player.
- [ ] Export twice to the same name → the second file is `name (2).ext`, the first is
      untouched.
- [ ] Cut off the process during an export: no truncated file is left in place.

## 5. Speech to text

- [ ] Record a 30-second Persian clip → transcript is in Persian, timings are sane.
- [ ] Transcribe an imported MP3/M4A file → same result (PyAV decodes it).
- [ ] Toggle VAD off/on and confirm silence is no longer transcribed with VAD on.
- [ ] Export SRT/VTT/JSON/TXT → timestamps are correct and play in VLC.
- [ ] Unplug the microphone mid-recording: the app reports the failure, keeps the audio
      recorded so far, and does not crash.
- [ ] Try *Transcribe* with no model installed → a clear message pointing at *AI Models*.

## 6. Voice typing (the acceptance test that matters most)

- [ ] Default shortcut `Ctrl+Shift+Space` registers; the tray tooltip and Settings agree.
- [ ] Hold the shortcut, speak Persian, release → text appears in **Notepad** with
      correct Persian letters, no `?` or reversed text.
- [ ] Repeat into Word, Chrome, Telegram Desktop and VS Code.
- [ ] Start in Notepad, alt-tab to another app while speaking: text still lands in the
      window that was focused when recording began (or the app reports that the target
      was lost — never silently misroutes).
- [ ] Clipboard contents before and after insertion are unchanged.
- [ ] Overlay appears in the configured position, shows a live level meter, and the tray
      icon turns red while dictating.
- [ ] Change the shortcut in Settings, apply, and confirm the old one stops working.
- [ ] Add an app to the block list → insertion is refused there with a notification.
- [ ] Speak with the microphone on mute → "no speech detected", nothing is typed.
- [ ] Press the shortcut while a model is loading → the app queues or explains; it must
      not type half a sentence.

## 7. Library, history and exports

- [ ] Every recording, synthesis and transcription appears in *History* with the right
      kind, language and timestamp.
- [ ] Search by text and filter by kind; pin an entry and confirm it survives the
      retention purge.
- [ ] Delete an entry → it disappears from the list; the audio file is only removed when
      you ask for it.
- [ ] Import an audio file into the *Library*, play it, rename it, tag it, delete it.
- [ ] Export history as JSON/CSV/Markdown → files open cleanly and contain the same
      number of rows as the UI.
- [ ] Export the pronunciation dictionary → every learned override is present.

## 8. Themes, layout and accessibility

- [ ] Switch light/dark/system → every one of the ten pages re-renders, including open
      dialogs and the floating overlay.
- [ ] Try all seven accents plus a custom colour.
- [ ] Move UI scale (0.8–1.6) and corner radius: nothing clips or overlaps.
- [ ] Turn animations off; reduce transparency on.
- [ ] Switch the editor font and text direction: Persian stays right-to-left and Latin
      runs stay readable.
- [ ] Tab through each page: every control is reachable, tooltips explain icons, and
      no button is a dead placeholder.
- [ ] Resize to 1024×700: nothing is cut off, and the layout restores on restart.

## 9. Audio settings and DSP

- [ ] Pick each input/output device and run the level test; the meter responds.
- [ ] Apply gain, normalise, trim silence and denoise to a noisy recording → the export
      is audibly better and the original file is untouched.
- [ ] 16-bit/24-bit/32-bit float exports open everywhere expected.

## 10. Updates

- [ ] *Application Updates* shows the current version, checks GitHub Releases and
      reports "up to date" when appropriate.
- [ ] With no network, the check fails gracefully with a readable message.
- [ ] With a newer release present, the download shows progress, verifies SHA-256, and
      only installs after you confirm; the installer never runs silently from a source
      checkout.
- [ ] Corrupt the downloaded file → verification fails and it is deleted.
- [ ] Release notes and the "open on GitHub" link work.

## 11. Packaging (the < 110 MB gate)

```
pyinstaller --clean --noconfirm packaging\tixi-voice.spec
```

- [ ] `dist\TixiVoice\TixiVoice.exe` starts, all ten pages render, and the app is
      functional with no Python installed on the machine.
- [ ] `dist` size is **under 110 MB** (the shipped application, model files excluded).
- [ ] No `torch`, `onnxruntime`, `ctranslate2` or `av` DLLs are inside `dist` — those
      arrive through engine packs.
- [ ] Install with Inno Setup, launch from the Start Menu, verify the per-user install
      needs no administrator rights.
- [ ] Uninstall: it asks before deleting `%LOCALAPPDATA%\TixiVoice`, and declining keeps
      your recordings and history.
- [ ] Fresh machine with no models: the app explains how to get them instead of crashing.

## 12. Robustness

- [ ] Pull the network cable mid-download → the download pauses/retries and never
      leaves a corrupt model registered.
- [ ] Fill the disk (or set a tiny quota) → the pre-flight free-space check refuses the
      download with a readable message.
- [ ] Close the window while a job runs → the app asks/persists safely and exits cleanly.
- [ ] Check `logs\tixi.log` and any crash file: no tracebacks for normal use.
