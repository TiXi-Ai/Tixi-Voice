"""``python -m tixi`` runs the application."""

from __future__ import annotations

import sys

from .app.application import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
