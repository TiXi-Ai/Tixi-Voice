"""Application services.

The layers below (``storage``, ``engines``, ``models``, ``audio``, ``updater``,
``voice_typing``) are deliberately UI-free.  The ``services`` package is the
single place where they are combined into *features* the interface can call:
jobs run on the Qt thread pool, progress is reported with signals, and every
user-visible operation (synthesise, transcribe, install, update) has exactly one
implementation shared by the GUI, the tray and the voice-typing overlay.

Nothing in here touches the database schema or reads model files directly — it
always goes through the repositories and the managers so that state stays in one
place.
"""

from __future__ import annotations

from .jobs import JobCancelled, JobInfo, JobRunner

__all__ = [
    "JobRunner",
    "JobInfo",
    "JobCancelled",
]
