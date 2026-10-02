# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-contained palettes, Qt styles and vector icons (no web resources).

iOS-style glassmorphism theme: frosted, semi-transparent panels layered over a
soft gradient backdrop with hairline borders and rounded corners.
"""
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPalette
from PySide6.QtSvg import QSvgRenderer

from core import ASSETS

DARK = dict(
    bg='#0a0e1a', sidebar='rgba(18,24,42,190)', panel='rgba(28,36,62,185)',
    soft='rgba(44,54,90,160)', field='rgba(20,27,48,150)', text='#f2f5ff',
    muted='#b7c3dd', faint='#8b97b8', line='rgba(120,140,190,70)',
    accent='#4dd8ff', accentText='#062a3a', tint='rgba(44,96,150,110)',
    purple='#b4a1ff', purpleTint='rgba(60,50,110,120)', gold='#ffd27a',
    danger='#ff9fae', dangerBg='rgba(90,35,55,140)',
    gradientA='#141b33', gradientB='#0a0e1a', gradientC='#1a1630',
    hover='rgba(64,80,130,140)', glassTop='rgba(255,255,255,26)',
    glassBottom='rgba(255,255,255,6)', shadow='rgba(0,0,0,90)')
LIGHT = dict(
    bg='#e8edf7', sidebar='rgba(248,250,255,175)', panel='rgba(255,255,255,175)',
    soft='rgba(235,240,252,165)', field='rgba(255,255,255,170)', text='#1f2b49',
    muted='#4f6390', faint='#647796', line='rgba(90,110,160,55)',
    accent='#007aff', accentText='#ffffff', tint='rgba(214,232,255,150)',
    purple='#7355cb', purpleTint='rgba(240,235,255,150)', gold='#c47f1e',
    danger='#b12950', dangerBg='rgba(255,225,232,160)',
    gradientA='#f2f5fb', gradientB='#e8edf7', gradientC='#e4ecfb',
    hover='rgba(235,242,255,180)', glassTop='rgba(255,255,255,120)',
    glassBottom='rgba(255,255,255,20)', shadow='rgba(20,40,90,40)')


def _light(name, a, b, c, accent, accentText='#ffffff', tint=(214, 232, 255),
           text='#1f2b49', muted='#4f6390', faint='#647796',
           purple=None, purpleTint=None, gold='#c47f1e'):
    """Build a light palette from a three-stop backdrop gradient + accent."""
    ta = 'rgba(%d,%d,%d,150)' % tint
    return dict(
        bg='#e8edf7', sidebar='rgba(248,250,255,175)', panel='rgba(255,255,255,175)',
        soft='rgba(235,240,252,165)', field='rgba(255,255,255,170)', text=text,
        muted=muted, faint=faint, line='rgba(90,110,160,55)',
        accent=accent, accentText=accentText, tint=ta,
        purple=purple or accent, purpleTint=purpleTint or ('rgba(%d,%d,%d,150)' % tint),
        gold=gold, danger='#b12950', dangerBg='rgba(255,225,232,160)',
        gradientA=a, gradientB=b, gradientC=c,
        hover='rgba(235,242,255,180)', glassTop='rgba(255,255,255,120)',
        glassBottom='rgba(255,255,255,20)', shadow='rgba(20,40,90,40)')


def _dark(name, a, b, c, accent, accentText='#062a3a', tint=(44, 96, 150),
          purple=None, purpleTint=None, gold='#ffd27a'):
    """Build a dark palette from a three-stop backdrop gradient + accent."""
    ta = 'rgba(%d,%d,%d,110)' % tint
    return dict(
        bg='#0a0e1a', sidebar='rgba(18,24,42,190)', panel='rgba(28,36,62,185)',
        soft='rgba(44,54,90,160)', field='rgba(20,27,48,150)', text='#f2f5ff',
        muted='#b7c3dd', faint='#8b97b8', line='rgba(120,140,190,70)',
        accent=accent, accentText=accentText, tint=ta,
        purple=purple or accent, purpleTint=purpleTint or ('rgba(%d,%d,%d,120)' % tint),
        gold=gold, danger='#ff9fae', dangerBg='rgba(90,35,55,140)',
        gradientA=a, gradientB=b, gradientC=c,
        hover='rgba(64,80,130,140)', glassTop='rgba(255,255,255,26)',
        glassBottom='rgba(255,255,255,6)', shadow='rgba(0,0,0,90)')


PALETTES = {
    'ios':       _light('ios', '#f2f5fb', '#e8edf7', '#e4ecfb', '#007aff', tint=(214, 232, 255), purple='#7355cb', purpleTint='rgba(240,235,255,150)'),
    'mint':      _light('mint', '#eefbf4', '#e3f6ec', '#d9f3e7', '#00a86b', tint=(190, 240, 214), purple='#12b76a', purpleTint='rgba(220,250,235,150)', gold='#c47f1e'),
    'peach':     _light('peach', '#fdf3ee', '#fbebe2', '#f9e4d9', '#ff7a45', tint=(255, 222, 205), purple='#ff8f6b', purpleTint='rgba(255,235,225,150)', gold='#c47f1e'),
    'lavender':  _light('lavender', '#f5f2fd', '#ede9fb', '#e7e1f9', '#7a5cff', tint=(226, 215, 255), purple='#8b6bff', purpleTint='rgba(240,235,255,150)', gold='#c47f1e'),
    'ocean':     _light('ocean', '#eef6fb', '#e3f0f7', '#d9ebf5', '#0aa2c0', tint=(205, 235, 250), purple='#0fb5d6', purpleTint='rgba(225,245,252,150)', gold='#c47f1e'),
    'rose':      _light('rose', '#fdf0f4', '#fbe6ec', '#f9dce5', '#ff4d8d', tint=(255, 218, 233), purple='#ff6ba5', purpleTint='rgba(255,235,244,150)', gold='#c47f1e'),
    'golden':    _light('golden', '#fbf6ea', '#f6eede', '#f2e7d4', '#e8a13c', tint=(255, 236, 200), purple='#f0b65a', purpleTint='rgba(255,243,220,150)', gold='#a86a10'),
    'midnight':  _dark('midnight', '#141b33', '#0a0e1a', '#1a1630', '#4dd8ff', tint=(44, 96, 150), purple='#b4a1ff', purpleTint='rgba(60,50,110,120)'),
    'royal':     _dark('royal', '#1c1633', '#120d26', '#241a40', '#b48aff', tint=(70, 45, 130), purple='#c9a9ff', purpleTint='rgba(80,55,150,120)', gold='#ffd27a'),
    'emerald':   _dark('emerald', '#0f2b22', '#081b15', '#123329', '#37e6a8', tint=(30, 110, 80), purple='#5fe6c2', purpleTint='rgba(30,110,90,120)', gold='#ffd27a'),
}

# Friendly display names used in the appearance page.
NAMES = {
    'ios': 'iOS', 'mint': 'نعنایی Mint', 'peach': 'هلویی Peach',
    'lavender': 'اسطوخودوس Lavender', 'ocean': 'اقیانوس Ocean',
    'rose': 'رز Rose', 'golden': 'طلایی Golden', 'midnight': 'نیمه‌شب Midnight',
    'royal': 'سلطنتی Royal', 'emerald': 'زمردی Emerald',
}

DARK = PALETTES['midnight']
LIGHT = PALETTES['ios']

PATHS = {
'grid':'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
'sparkles':'<path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6zM20 2v4M18 4h4"/>',
'note':'<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
'star':'<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.4L12 17.5l-5.7 3.1 1.1-6.4-4.6-4.5 6.4-.9z"/>',
'plus':'<path d="M12 5v14M5 12h14"/>',
'x':'<path d="m6 6 12 12M6 18 18 6"/>',
'archive':'<path d="M4 8h16v13H4zM9 12h6"/><rect x="3" y="3" width="18" height="5" rx="1"/>',
'shield':'<path d="M12 3 3 6v6c0 5 9 10 9 10s9-5 9-10V6zM8 12l3 3 5-6"/>',
'search':'<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
'folder':'<path d="M3 6h7l2 3h9v11H3z"/>',
'copy':'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
'edit':'<path d="m16 3 5 5L9 20l-6 1 1-6zM13 6l5 5"/>',
'trash':'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
'download':'<path d="M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6"/>',
'upload':'<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
'sun':'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
'moon':'<path d="M20.7 13.3A9 9 0 0 1 10.7 3.2 9 9 0 1 0 20.7 13.3z"/>',
'monitor':'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
'layers':'<path d="m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5"/>',
'check':'<path d="m5 12 4 4L19 6"/>'
}

@lru_cache(maxsize=256)
def icon(name: str, color: str, filled=False) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color if filled else "none"}" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{PATHS.get(name, PATHS["note"])}</svg>'
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)

_icon_dir = ASSETS / 'icons'

@lru_cache(maxsize=128)
def icon_file(name: str) -> QIcon:
    """Load a packaged PNG icon (extracted from the ICON folder) with its native colors."""
    p = _icon_dir / f'{name}.png'
    if not p.is_file():
        return QIcon()
    pm = QPixmap(str(p))
    if pm.isNull():
        return QIcon()
    return QIcon(pm)

def stylesheet(p):
    return '''
    QWidget { color: %(text)s; font-family: "Vazirmatn", "Segoe UI", Tahoma; font-size: 13px; }
    QMainWindow, QDialog { background: %(bg)s; }
    QWidget#mainPanel { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(gradientA)s,stop:0.5 %(gradientB)s,stop:1 %(gradientC)s); }
    QFrame#sidebar { background: %(sidebar)s; border-right: 1px solid %(line)s; }
    QLabel { background: transparent; }
    QLabel[role="muted"] { color: %(muted)s; }
    QLabel[role="faint"] { color: %(faint)s; font-size: 11px; }
    QLabel[role="accent"] { color: %(accent)s; }
    QLabel#heading { font-size: 30px; font-weight: bold; }
    QLabel#dialogHeading { font-size: 22px; font-weight: bold; }
    QLabel#brandTitle { font-family: "Segoe UI", sans-serif; font-size: 23px; font-weight: bold; }
    QLabel#brandSub { font-family: "Segoe UI", sans-serif; font-size: 8px; color: %(faint)s; letter-spacing: 1px; }
    QPushButton { background: %(soft)s; border: 1px solid %(line)s; border-radius: 11px; padding: 9px 14px; min-height: 18px; }
    QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
    QPushButton:pressed { background: %(tint)s; }
    QPushButton:focus { border-color: %(accent)s; }
    QPushButton:disabled { color: %(faint)s; border-color: %(line)s; }
    QPushButton[role="primary"] { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(accent)s,stop:1 %(accent)s); color: %(accentText)s; border-color: %(accent)s; font-weight: bold; }
    QPushButton[role="primary"]:hover { border-color: %(purple)s; }
    QPushButton[role="danger"] { background: %(dangerBg)s; color: %(danger)s; border-color: %(dangerBg)s; }
    QPushButton[role="ghost"] { background: transparent; border-color: transparent; padding: 5px; }
    QPushButton[role="ghost"]:hover { background: %(soft)s; border-color: %(line)s; }
    QPushButton#navButton, QToolButton#navButton { min-height: 44px; max-height: 44px; background: transparent; color: %(muted)s; border: 1px solid transparent; border-radius: 12px; padding: 0px 8px; text-align: left; }
    QToolButton#navButton { padding-left: 6px; }
    QPushButton#navButton[compact="true"], QToolButton#navButton[compact="true"] { min-height: 32px; max-height: 32px; }
    QPushButton#navButton:hover, QToolButton#navButton:hover { background: %(soft)s; }
    QPushButton#navButton[active="true"], QToolButton#navButton[active="true"] { background: %(tint)s; border-color: %(line)s; color: %(text)s; border-right: 3px solid %(accent)s; }
    QLabel#countBadge { font-size: 10px; color: %(faint)s; background: %(soft)s; border: 1px solid %(line)s; border-radius: 6px; padding: 1px 5px; }
    QFrame#themeSwitch { background: %(soft)s; border: 1px solid %(line)s; border-radius: 12px; }
    QPushButton#themeButton { background: transparent; border-color: transparent; padding: 6px 3px; font-size: 10px; }
    QPushButton#themeButton:checked { background: %(tint)s; border-color: %(accent)s; color: %(accent)s; border-radius: 9px; }
    QPushButton#typeButton { background: %(field)s; color: %(muted)s; }
    QPushButton#typeButton:checked { background: %(tint)s; color: %(accent)s; border-color: %(accent)s; }
    QLineEdit, QPlainTextEdit, QComboBox { background: %(field)s; border: 1px solid %(line)s; border-radius: 11px; padding: 10px; selection-background-color: %(accent)s; selection-color: %(accentText)s; }
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: %(accent)s; }
    QComboBox { padding-right: 12px; padding-left: 25px; }
    QComboBox::drop-down { border: 0; width: 24px; }
    QComboBox QAbstractItemView { background: %(panel)s; color: %(text)s; selection-background-color: %(tint)s; border: 1px solid %(line)s; border-radius: 10px; padding: 5px; }
    QLineEdit#search { background: %(panel)s; border-radius: 13px; padding: 14px; }
    QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
    QScrollBar:vertical { background: transparent; width: 7px; margin: 0px; }
    QScrollBar::handle:vertical { background: %(line)s; min-height: 30px; border-radius: 3px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QFrame#noteCard { background: %(panel)s; border: 1px solid %(line)s; border-radius: 16px; }
    QFrame#noteCard:hover { border-color: %(accent)s; }
    QFrame#noteCard[noteType="note"]:hover { border-color: %(purple)s; }
    QLabel#typeBadge { color: %(accent)s; background: %(tint)s; border: 1px solid %(line)s; border-radius: 7px; padding: 4px 8px; font-size: 10px; }
    QLabel#typeBadge[noteType="note"] { color: %(purple)s; background: %(purpleTint)s; }
    QPushButton#cardTitle { background: transparent; border: 0; border-radius: 0; text-align: right; font-size: 15px; font-weight: bold; padding: 0; }
    QPushButton#cardTitle:hover { color: %(accent)s; }
    QLabel#previewText { color: %(muted)s; font-size: 12px; }
    QPushButton#tag { background: %(soft)s; color: %(muted)s; border: 1px solid %(line)s; border-radius: 6px; padding: 3px 6px; min-height: 12px; font-size: 10px; }
    QPushButton#tag:hover { color: %(purple)s; border-color: %(purple)s; }
    QPushButton#copyButton { color: %(accent)s; background: %(tint)s; border: 1px solid transparent; padding: 5px 9px; font-size: 11px; }
    QPushButton#copyButton:hover { border-color: %(accent)s; }
    QFrame#line { background: %(line)s; border: 0; min-height: 1px; max-height: 1px; }
    QFrame#emptyState { background: %(panel)s; border: 1px dashed %(line)s; border-radius: 18px; }
    QFrame#notice { background: %(tint)s; border: 1px solid %(line)s; border-radius: 12px; }
    QLabel#error { color: %(danger)s; background: %(dangerBg)s; padding: 10px; border-radius: 9px; }
    QCheckBox { spacing: 8px; color: %(muted)s; }
    QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid %(faint)s; border-radius: 5px; background: %(field)s; }
    QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }
    QRadioButton { spacing: 8px; }
    QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid %(faint)s; border-radius: 7px; background: %(field)s; }
    QRadioButton::indicator:checked { background: %(accent)s; border: 3px solid %(panel)s; }
    QToolTip { color: %(text)s; background: %(panel)s; border: 1px solid %(line)s; padding: 7px; }
    QMessageBox { background: %(panel)s; }
    QStatusBar { background: %(bg)s; color: %(muted)s; }
    ''' % p

def apply_palette(app, p):
    palette = QPalette()
    roles = {'Window': 'bg', 'WindowText': 'text', 'Base': 'field', 'AlternateBase': 'soft', 'Text': 'text', 'Button': 'panel', 'ButtonText': 'text', 'ToolTipBase': 'panel', 'ToolTipText': 'text', 'Highlight': 'accent', 'HighlightedText': 'accentText', 'PlaceholderText': 'faint'}
    for role, key in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(p[key]))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(p))
