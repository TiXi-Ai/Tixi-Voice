# Privacy

Tixi Voice is an offline application. This page documents every piece of data it touches, so
you can verify that claim instead of trusting it.

## What never happens

* Audio is never uploaded. Speech recognition and speech synthesis run locally.
* Text you type or dictate is never sent anywhere. Diacritization and normalisation are local.
* There is no telemetry, no analytics, no crash reporting service and no account.
* The application does not contact any server you have not configured: only GitHub (for
  update checks) and PyPI (for engine packs), and only when you ask for it or when automatic
  update checks are enabled.

## What is stored, and where

| Data | Location | Notes |
| --- | --- | --- |
| Settings | `%APPDATA%\TixiVoice\settings.json` and the `settings` table | Human-readable mirror plus the SQLite copy. |
| Database | `%LOCALAPPDATA%\TixiVoice\tixi.db` | History, audio metadata, dictionary, model registry. |
| Generated audio | `%LOCALAPPDATA%\TixiVoice\recordings` (or the export folder you choose) | Never deleted unless you delete it. |
| Models | `%LOCALAPPDATA%\TixiVoice\models` | Move the folder in **Settings ▸ General**. |
| Engine packs | `%LOCALAPPDATA%\TixiVoice\engine_packs` | Verified wheels, unpacked. |
| Logs | `%LOCALAPPDATA%\TixiVoice\logs` | Timings, errors and paths. Recognised text is **not** logged unless you enable *Include recognised text in the log*. |

Portable mode (`portable.flag` next to the executable, or `TIXI_PORTABLE=1`) keeps everything
inside the application folder instead, which is useful on a USB stick.

## Controls you have

* **Offline Mode** (Settings ▸ Privacy) blocks every network call — update checks, model
  downloads and engine packs. The application keeps working with everything already installed.
* **History retention**: keep forever, or delete entries older than *n* days.
* **Auto-delete audio after *n* days**, separately from the history.
* **Content logging** is off by default; turning it on is the only way recognised text can
  reach the log.
* Paths in logs can be anonymised (on by default in packaged builds' support reports).
* Every delete asks first, and the “also delete the files” checkbox is always **off** by
  default.

## Network calls in detail

| When | Where | What is sent |
| --- | --- | --- |
| You press *Install* on a model | `huggingface.co` (model files) | an HTTPS GET for the file, plus a token only if you supplied one for a gated model. |
| You press *Install engine pack* | `pypi.org`, `files.pythonhosted.org` | an HTTPS GET for the wheel metadata and the wheel itself. |
| Automatic or manual update check | `api.github.com` | an HTTPS GET for the release list with a version header. |
| You open a model/release page | your browser | normal browser traffic. |

Nothing else is contacted, and no identifiers are attached to any of these requests.
