# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, report it privately to the maintainer by email at **tixiychannel@gmail.com** with the subject `Tixi-Voice security report`.

We aim to respond within a few days. If the issue is confirmed, we will work on a fix and coordinate disclosure.

## Scope

- Source code in `source/`
- Portable build artifacts (`Tixi-Voice.exe`, `_internal/`)
- Model handling, path traversal protections, archive extraction safety, and data handling in the app

## Notes

- This project is fully offline by design — telemetry is disabled and no user data is sent to any server.
- Personal voice samples are stored locally without encryption; the app is not a tool for impersonation or bypassing authentication, and misuse is out of scope.
- Known limitations are documented in `TESTING.md` and `README.md`.
