"""Generate minimal stub shared libraries so PySide6 can be imported headlessly.

Development/CI helper for Linux machines that do *not* have the X11/GL/DBus
runtime libraries PySide6 links against (e.g. bare containers).  It writes stub
``.so`` files into ``--out-dir`` and iterates until ``QApplication`` starts with
the ``offscreen`` platform plugin, filling in every symbol the dynamic linker
complains about.

The stubs satisfy the *loader* only — they implement no behaviour.  They are
useful for import checks and offscreen widget smoke tests, never for running the
application.  Windows builds (the supported target) do not need them at all.

Usage::

    python tools/make_qt_stubs.py                     # writes to ~/.cache/tixi-qtstubs
    LD_LIBRARY_PATH=~/.cache/tixi-qtstubs QT_QPA_PLATFORM=offscreen pytest -m gui
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out-dir", default=str(pathlib.Path.home() / ".cache" / "tixi-qtstubs"))
parser.add_argument("--python", default=sys.executable, help="interpreter used for the probe")
ARGS, _ = parser.parse_known_args()

STUB_DIR = pathlib.Path(ARGS.out_dir)
STUB_DIR.mkdir(parents=True, exist_ok=True)

MISSING_LIBS = [
    "libGL.so.1", "libEGL.so.1", "libGLX.so.0", "libGLdispatch.so.0", "libOpenGL.so.0",
    "libxkbcommon.so.0", "libxkbcommon-x11.so.0", "libdbus-1.so.3",
    "libX11-xcb.so.1", "libwayland-client.so.0", "libwayland-cursor.so.0",
    "libwayland-egl.so.1", "libpulse.so.0", "libpulse-simple.so.0", "libasound.so.2",
    "libXrender.so.1", "libXfixes.so.3", "libXi.so.6", "libXcursor.so.1", "libXrandr.so.2",
    "libSM.so.6", "libICE.so.6",
]

PREFIX_MAP = {
    "dbus_": "libdbus-1.so.3",
    "xkb_": "libxkbcommon.so.0",
    "egl": "libEGL.so.1",
    "glx": "libGLX.so.0",
    "gl": "libGL.so.1",
    "xcb_": "libX11-xcb.so.1",
    "xcursor": "libXcursor.so.1",
    "xfixes": "libXfixes.so.3",
    "xrandr": "libXrandr.so.2",
    "xrender": "libXrender.so.1",
    "xi": "libXi.so.6",
    "sm_": "libSM.so.6",
    "ice_": "libICE.so.6",
    "pa_": "libpulse.so.0",
    "snd_": "libasound.so.2",
    "wl_": "libwayland-client.so.0",
}

VERSION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
SYMBOLS: dict[str, dict[str, set[str]]] = {lib: {} for lib in MISSING_LIBS}


def provider(name: str) -> str:
    low = name.lower()
    for prefix, lib in PREFIX_MAP.items():
        if low.startswith(prefix):
            return lib
    return "libxkbcommon.so.0"


def add(name: str, version: str, extra_lib: str = "") -> None:
    version = version if version and VERSION_RE.match(version) else ""
    libs = [provider(name)]
    low = name.lower()
    if low.startswith(("gl", "egl")):
        # QtGui links libGL/libEGL directly (the real Mesa provides the glX/EGL
        # entry points through them), so the symbols must live there too.
        for fallback in ("libGL.so.1", "libEGL.so.1"):
            if fallback not in libs:
                libs.append(fallback)
    if extra_lib and extra_lib in SYMBOLS and extra_lib not in libs:
        libs.append(extra_lib)
    for lib in libs:
        SYMBOLS[lib].setdefault(version, set()).add(name)


def build() -> None:
    for lib in MISSING_LIBS:
        entries = SYMBOLS[lib]
        c_path = STUB_DIR / f"{lib}.c"
        map_path = STUB_DIR / f"{lib}.map"
        functions: list[str] = []
        seen: set[str] = set()
        nodes: list[str] = []
        versioned = sorted((v, sorted(n)) for v, n in entries.items() if v)
        unversioned = sorted(entries.get("", set()))
        for version, names in versioned:
            for name in names:
                if name not in seen:
                    seen.add(name)
                    functions.append(f"void {name}(void) {{}}")
        for name in unversioned:
            if name not in seen:
                seen.add(name)
                functions.append(f"void {name}(void) {{}}")
        if versioned:
            for index, (version, names) in enumerate(versioned):
                # Unversioned references are satisfied because the linker treats the
                # first defined version as the default one.
                merged = sorted(set(names) | (set(unversioned) if index == 0 else set()))
                tail = " local: *;" if index == 0 else ""
                nodes.append(f"{version} {{ global: {'; '.join(merged)};{tail} }};")
        c_path.write_text("\n".join(functions) + "\n")
        map_path.write_text("\n".join(nodes) + "\n")
        cmd = ["gcc", "-shared", "-fPIC", str(c_path), "-o", str(STUB_DIR / lib)]
        if nodes:
            cmd.append(f"-Wl,--version-script={map_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("compile failed:", lib, result.stderr.strip().splitlines()[:2])


ENV = {
    "LD_LIBRARY_PATH": str(STUB_DIR),
    "QT_QPA_PLATFORM": "offscreen",
    "QT_DEBUG_PLUGINS": "1",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "LANG": "C.UTF-8",
}


def probe() -> str:
    proc = subprocess.run(
        [ARGS.python, "-c",
         "import PySide6.QtWidgets as w; app=w.QApplication([]); print('QT_IMPORT_OK')"],
        capture_output=True, text=True, env=ENV,
    )
    return proc.stdout + proc.stderr


build()
for attempt in range(150):
    out = probe()
    if "QT_IMPORT_OK" in out:
        print(f"SUCCESS after {attempt} iterations — stubs in {STUB_DIR}")
        break
    match = re.search(r"undefined symbol: ([\w.]+)(?:, version (\S+?))?\s*$", out, re.M)
    if not match:
        print("STUCK:\n" + out[-1200:])
        break
    name, version = match.group(1), match.group(2) or ""
    failing = re.search(r"(/[/\w.+-]+\.so[\w.]*): undefined symbol", out)
    extra = pathlib.Path(failing.group(1)).name if failing else ""
    add(name, version, extra)
    build()
    if attempt < 8 or attempt % 20 == 0:
        print(f"iter {attempt}: {provider(name)} += {name}@{version or 'unversioned'}")
else:
    print("gave up")
