# Contributing to Tixi Voice

Thanks for your interest in Tixi Voice! This is a GPL-3.0-or-later open source project, and every contribution is welcome.

## How to contribute

- **Bug reports & feature ideas** — open a [GitHub issue](https://github.com/TiXi-Ai/Tixi-Voice/issues).
- **Code** — fork the repo, create a branch, and open a pull request.
- **Testing on real hardware** — the project is tested on simulated audio; reports from real microphones/speakers/GPUs are especially valuable.

## Getting started

1. Fork and clone the repository.
2. Install Python 3.13 x64 on Windows.
3. Run from source with `START-SOURCE.bat`, or install pinned dependencies with `pip install -r source/requirements.txt`.
4. Run the tests with `python -m unittest discover -s tests -v` (from `source/`).

## Guidelines

- Keep the app fully offline — no telemetry, no cloud dependency, no sending user data anywhere.
- Preserve the GPL-3.0-or-later license and third-party attribution in `THIRD-PARTY.md` / `LICENSES`.
- Add tests for new logic and make sure all tests pass before opening a PR.
- Files use UTF-8; Persian UI text is written right-to-left. Keep naming and structure consistent.

## Rebuilding the EXE

`BUILD-EXE.bat` (in `source/`) rebuilds the portable EXE with PyInstaller. Do not commit built artifacts (`Tixi-Voice.exe`, `_internal/`) — they are gitignored.

## License

By contributing you agree that your contributions are licensed under GPL-3.0-or-later. See `LICENSE.txt`.
