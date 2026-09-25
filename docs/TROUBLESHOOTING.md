# Troubleshooting

The first place to look is always **About ▸ Copy diagnostics** (it contains versions, paths and
installed models, never your text) and the log file:

```
%LOCALAPPDATA%\TixiVoice\logs\tixi-voice.log      (rotated, max 2 MB × 3)
%LOCALAPPDATA%\TixiVoice\logs\crashes.log         (unhandled exceptions)
```

## “The Piper engine pack is not installed”

The text-to-speech runtime is a separate download. Open **AI Models ▸ Engine packs ▸ Install
engine pack**. If the download fails, check:

* Offline Mode is off (**Settings ▸ Privacy**);
* a proxy or firewall allows `pypi.org` and `files.pythonhosted.org`;
* free disk space (the pack is ~15 MB, the unpacked result ~40 MB).

Every downloaded wheel is verified against the SHA-256 hash published by PyPI. A mismatch is
reported and nothing is unpacked.

## “No voice model is installed”

Voices are models, not engines. Open **AI Models**, filter on *Persian Text-to-Speech* and
install *Persian — Amir (male)* (~63 MB) or *Gyro*. Voices are stored in
`%LOCALAPPDATA%\TixiVoice\models`.

## Speech recognition returns empty or nonsense text

1. Check the microphone in **Settings ▸ Audio devices** (the level meter on the Speech to Text
   page shows whether audio arrives at all).
2. Use `small` or larger — Whisper `tiny`/`base` are unreliable for Persian.
3. Set the language to **Persian** explicitly instead of automatic detection.
4. Keep the voice-activity filter on and raise the microphone gain if the level meter barely
   moves.
5. Very noisy input: enable *Spectral reduction* in the Audio settings.

## Dictation types nothing

The readiness checks on the **Voice Typing** page list every precondition. The usual causes:

| Symptom | Cause | Fix |
| --- | --- | --- |
| “Global shortcut is only available on Windows” | Running on Linux/macOS or from a non-Windows build | Expected — hotkeys need Windows. |
| Shortcut does nothing | Another application owns the shortcut | Use *Test conflict* and choose another combination. |
| Nothing is typed but the text appears in the fallback window | The focused window is elevated (or Tixi Voice is) | Run both at the same privilege level. Windows blocks input from a standard process into an administrator window. |
| Nothing is typed in games / custom-rendered controls | They ignore synthetic input | Use the fallback window's **Copy** button. |
| Nothing is typed during a UAC prompt or on the lock screen | The secure desktop cannot receive input | By design. |
| Text is typed but Persian letters look wrong | The target editor does not handle Unicode input | Switch *Insertion method* to **Clipboard paste**. |

## The application window is invisible after starting

It probably started minimised to the tray. Left-click the tray icon or use
**Settings ▸ General ▸ Reopen the last page** combined with `--safe-mode` if a stored geometry
is off-screen.

## Updates

* **Rate limited** — the GitHub API allows 60 unauthenticated requests per hour. Wait, or use
  the *Open the releases page* button.
* **Signature invalid** — the installer's Authenticode signature did not validate; Tixi Voice
  refuses to run it. Download it manually from the releases page and verify the hash yourself.
* Updates are never installed silently: the installer is only launched from the
  *Application Updates* page after you confirm.

## Resetting

```powershell
TixiVoice.exe --reset-settings   # settings only; history, models and library are kept
```

To start completely fresh, close the application and delete `%LOCALAPPDATA%\TixiVoice` (models,
audio, database) and `%APPDATA%\TixiVoice` (settings). Nothing is removed automatically.

## Reporting a problem

Include the diagnostics text and the last 100 log lines. Redact paths if you prefer — nothing
in the log depends on your content being recognisable.
