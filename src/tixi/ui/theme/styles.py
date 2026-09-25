"""Qt stylesheet generation from :class:`~tixi.ui.theme.tokens.ThemeTokens`.

Everything the application paints with CSS lives here.  The stylesheet is rebuilt
whenever the theme, accent, density, radius or scaling changes, which is why
switching themes is instantaneous and complete — no widget keeps a stale colour.
"""

from __future__ import annotations

from .accents import alpha_hex, darken, lighten, mix, with_alpha
from .tokens import ThemeTokens


def build_stylesheet(tokens: ThemeTokens) -> str:
    """Return the complete application stylesheet for ``tokens``."""
    t = tokens
    radius = f"{t.radius}px"
    radius_small = f"{t.radius_small}px"
    radius_large = f"{t.radius_large}px"
    accent = t.accent_primary
    accent_hover = t.accent_hover
    accent_pressed = t.accent_pressed
    accent_text = t.accent_text
    surface_border = with_alpha(t.border_strong, 0.55 if t.is_dark else 0.75)

    return f"""
/* ==========================================================================
   Base
   ========================================================================== */
QWidget {{
    color: {t.text};
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Vazirmatn", "Tahoma", sans-serif;
    font-size: 13px;
}}
QWidget:disabled {{
    color: {t.text_faint};
}}
QMainWindow, QDialog {{
    background: {t.background};
}}
QToolTip {{
    background: {t.background_elevated};
    color: {t.text};
    border: 1px solid {surface_border};
    border-radius: {radius_small};
    padding: 6px 9px;
}}

/* ==========================================================================
   Containers
   ========================================================================== */
#RootFrame, #ContentFrame {{
    background: {t.background};
}}
#Sidebar {{
    background: {t.sidebar};
    border-right: 1px solid {with_alpha(t.border, 0.7)};
}}
#SidebarGlass {{
    background: {with_alpha(t.sidebar, t.glass.panel_alpha)};
    border-right: 1px solid {with_alpha(t.border, 0.5)};
}}
#TitleBar {{
    background: {with_alpha(t.background, t.glass.surface_alpha)};
    border-bottom: 1px solid {with_alpha(t.border, 0.5)};
}}
#HeaderBar {{
    background: transparent;
}}
#Card, QFrame[card="true"] {{
    background: {t.surface};
    border: 1px solid {with_alpha(t.border, 0.85)};
    border-radius: {radius};
}}
#Card[interactive="true"]:hover {{
    background: {t.surface_alt};
    border-color: {t.accent_border};
}}
#GlassCard {{
    background: {with_alpha(t.surface, t.glass.panel_alpha)};
    border: 1px solid {with_alpha(t.border_strong, t.glass.border_alpha)};
    border-radius: {radius};
}}
#ElevatedCard {{
    background: {t.background_elevated};
    border: 1px solid {with_alpha(t.border, 0.9)};
    border-radius: {radius};
}}
#Toolbar {{
    background: {with_alpha(t.background_alt, 0.85)};
    border: 1px solid {with_alpha(t.border, 0.6)};
    border-radius: {radius};
}}
#Separator {{
    background: {with_alpha(t.border, 0.8)};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}
#VSeparator {{
    background: {with_alpha(t.border, 0.8)};
    max-width: 1px;
    min-width: 1px;
    border: none;
}}
#StatusBar {{
    background: {with_alpha(t.background_alt, 0.9)};
    border-top: 1px solid {with_alpha(t.border, 0.6)};
}}
#HeroPanel {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {with_alpha(accent, 0.20 if t.is_dark else 0.14)},
        stop:0.55 {with_alpha(t.accent_secondary, 0.10 if t.is_dark else 0.07)},
        stop:1 {t.surface});
    border: 1px solid {with_alpha(accent, 0.32)};
    border-radius: {radius_large};
}}
#Banner {{
    background: {with_alpha(t.info, 0.14)};
    border: 1px solid {with_alpha(t.info, 0.32)};
    border-radius: {radius};
}}
#Banner[severity="warning"] {{
    background: {with_alpha(t.warning, 0.14)};
    border-color: {with_alpha(t.warning, 0.36)};
}}
#Banner[severity="error"] {{
    background: {with_alpha(t.danger, 0.14)};
    border-color: {with_alpha(t.danger, 0.38)};
}}
#Banner[severity="success"] {{
    background: {with_alpha(t.success, 0.14)};
    border-color: {with_alpha(t.success, 0.34)};
}}

/* ==========================================================================
   Typography
   ========================================================================== */
#H1 {{ font-size: 24px; font-weight: 700; }}
#H2 {{ font-size: 18px; font-weight: 650; }}
#H3 {{ font-size: 15px; font-weight: 600; }}
#SectionTitle {{
    font-size: 12px;
    font-weight: 650;
    color: {t.text_muted};
    letter-spacing: 0.6px;
    text-transform: uppercase;
}}
#Caption {{ font-size: 11px; color: {t.text_muted}; }}
#CaptionFaint {{ font-size: 11px; color: {t.text_faint}; }}
#Muted {{ color: {t.text_muted}; }}
#Faint {{ color: {t.text_faint}; }}
#Danger {{ color: {t.danger}; }}
#Success {{ color: {t.success}; }}
#WarningText {{ color: {t.warning}; }}
#AccentText {{ color: {accent}; }}
#MetricValue {{ font-size: 22px; font-weight: 700; }}
#MetricLabel {{ font-size: 11px; color: {t.text_muted}; text-transform: uppercase; letter-spacing: 0.5px; }}
#Monospace {{
    font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New", monospace;
    font-size: 12px;
}}
#Kbd {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    background: {t.surface_alt};
    border: 1px solid {with_alpha(t.border_strong, 0.8)};
    border-bottom-width: 2px;
    border-radius: 5px;
    padding: 2px 6px;
    color: {t.text};
}}
#Pill {{
    border-radius: {t.radius_small};
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
    background: {t.accent_soft};
    color: {accent if t.is_dark else darken(accent, 0.15)};
}}

/* ==========================================================================
   Buttons
   ========================================================================== */
QPushButton {{
    background: {t.surface_alt if t.is_dark else "#FFFFFF"};
    color: {t.text};
    border: 1px solid {with_alpha(t.border_strong, 0.85)};
    border-radius: {radius_small};
    padding: 7px 14px;
    min-height: 20px;
    font-weight: 550;
}}
QPushButton:hover {{
    background: {t.surface_hover if t.is_dark else t.surface_alt};
    border-color: {with_alpha(t.border_strong, 1.0)};
}}
QPushButton:pressed {{
    background: {darken(t.surface_alt if t.is_dark else "#FFFFFF", 0.12)};
    padding-top: 8px;
    padding-bottom: 6px;
}}
QPushButton:focus {{
    border: 1px solid {t.accent_focus};
    outline: none;
}}
QPushButton:disabled {{
    background: {with_alpha(t.surface_alt, 0.6 if t.is_dark else 0.5)};
    color: {t.text_faint};
    border-color: {with_alpha(t.border, 0.5)};
}}
QPushButton[primary="true"] {{
    background: {accent};
    color: {accent_text};
    border: 1px solid {with_alpha(darken(accent, 0.15), 0.6)};
}}
QPushButton[primary="true"]:hover {{
    background: {accent_hover};
}}
QPushButton[primary="true"]:pressed {{
    background: {accent_pressed};
}}
QPushButton[primary="true"]:disabled {{
    background: {with_alpha(accent, 0.4)};
    color: {with_alpha(accent_text, 0.7)};
}}
QPushButton[danger="true"] {{
    background: {with_alpha(t.danger, 0.14)};
    color: {t.danger if t.is_dark else darken(t.danger, 0.2)};
    border: 1px solid {with_alpha(t.danger, 0.45)};
}}
QPushButton[danger="true"]:hover {{
    background: {with_alpha(t.danger, 0.24)};
}}
QPushButton[flat="true"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {t.text_muted};
    padding: 6px 9px;
}}
QPushButton[flat="true"]:hover {{
    background: {with_alpha(t.text, 0.08)};
    color: {t.text};
}}
QPushButton[flat="true"]:checked {{
    background: {t.accent_soft};
    color: {accent if t.is_dark else darken(accent, 0.15)};
}}
QPushButton[segmented="true"] {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_small};
    padding: 6px 12px;
    color: {t.text_muted};
    font-weight: 550;
}}
QPushButton[segmented="true"]:hover {{
    background: {with_alpha(t.text, 0.07)};
    color: {t.text};
}}
QPushButton[segmented="true"]:checked {{
    background: {t.surface_alt if t.is_dark else "#FFFFFF"};
    color: {t.text};
    border-color: {with_alpha(t.border_strong, 0.7)};
}}
QPushButton[iconOnly="true"] {{
    padding: 6px;
    border-radius: {radius_small};
    min-width: 24px;
}}
QPushButton#TinyButton {{
    padding: 3px 8px;
    font-size: 11px;
    min-height: 16px;
}}
QPushButton#LinkButton {{
    background: transparent;
    border: none;
    color: {accent if t.is_dark else darken(accent, 0.12)};
    padding: 2px 4px;
    font-weight: 550;
    text-decoration: underline;
}}
QPushButton#LinkButton:hover {{
    color: {accent_hover};
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_small};
    padding: 5px;
    color: {t.text_muted};
}}
QToolButton:hover {{
    background: {with_alpha(t.text, 0.08)};
    color: {t.text};
}}
QToolButton:checked, QToolButton:pressed {{
    background: {t.accent_soft};
    color: {accent if t.is_dark else darken(accent, 0.15)};
}}
QToolButton::menu-indicator {{
    image: none;
    width: 0px;
}}

/* ==========================================================================
   Inputs
   ========================================================================== */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    background: {t.background_elevated if not t.is_dark else with_alpha("#000000", 0.22)};
    color: {t.text};
    border: 1px solid {with_alpha(t.border_strong, 0.75)};
    border-radius: {radius_small};
    padding: 6px 9px;
    selection-background-color: {t.selection};
    selection-color: {t.text if not t.is_dark else "#FFFFFF"};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {with_alpha(t.border_strong, 1.0)};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {t.accent_focus};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QSpinBox:disabled {{
    background: {with_alpha(t.surface_alt, 0.5)};
    color: {t.text_faint};
}}
QLineEdit[readOnly="true"] {{
    background: {with_alpha(t.surface_alt, 0.45)};
}}
QLineEdit#SearchBox {{
    padding-left: 30px;
    border-radius: {radius_small};
}}
QLineEdit#ShortcutBox {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    letter-spacing: 0.4px;
}}
QTextEdit, QPlainTextEdit#RtlEditor {{
    line-height: 160%;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 18px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {t.text_muted};
    width: 0; height: 0;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_muted};
    width: 0; height: 0;
}}
QComboBox {{
    background: {t.background_elevated if not t.is_dark else with_alpha("#000000", 0.22)};
    color: {t.text};
    border: 1px solid {with_alpha(t.border_strong, 0.75)};
    border-radius: {radius_small};
    padding: 6px 28px 6px 10px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {with_alpha(t.border_strong, 1.0)};
}}
QComboBox:focus {{
    border: 1px solid {t.accent_focus};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_muted};
    width: 0; height: 0;
    margin-right: 8px;
}}
QComboBox::down-arrow:on {{
    border-top-color: {accent};
}}
QComboBox QAbstractItemView {{
    background: {t.background_elevated};
    color: {t.text};
    border: 1px solid {with_alpha(t.border_strong, 0.8)};
    border-radius: {radius_small};
    padding: 4px;
    outline: none;
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
}}
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {t.text};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid {with_alpha(t.border_strong, 0.9)};
    background: {t.background_elevated if not t.is_dark else with_alpha("#000000", 0.2)};
}}
QCheckBox::indicator {{
    border-radius: 5px;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {accent};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
    image: none;
}}
QRadioButton::indicator:checked {{
    background: {with_alpha(accent, 0.25)};
    border: 5px solid {accent};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {with_alpha(t.surface_alt, 0.6)};
    border-color: {with_alpha(t.border, 0.6)};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {with_alpha(t.text, 0.16)};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {t.background_elevated if not t.is_dark else "#FFFFFF"};
    border: 1px solid {with_alpha(t.border_strong, 0.8)};
    width: 15px;
    height: 15px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    border-color: {accent};
}}
QSlider::handle:horizontal:pressed {{
    background: {accent};
}}
QSlider::groove:vertical {{
    width: 4px;
    background: {with_alpha(t.text, 0.16)};
    border-radius: 2px;
}}
QSlider::sub-page:vertical {{
    background: {accent};
    border-radius: 2px;
}}
QSlider::handle:vertical {{
    background: {t.background_elevated if not t.is_dark else "#FFFFFF"};
    border: 1px solid {with_alpha(t.border_strong, 0.8)};
    width: 15px;
    height: 15px;
    margin: 0 -6px;
    border-radius: 8px;
}}
QProgressBar {{
    background: {with_alpha(t.text, 0.10)};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: {t.text_muted};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent}, stop:1 {t.accent_secondary});
    border-radius: 5px;
}}
QProgressBar[state="error"]::chunk {{
    background: {t.danger};
}}
QProgressBar[state="paused"]::chunk {{
    background: {t.warning};
}}
QProgressBar[state="success"]::chunk {{
    background: {t.success};
}}
QProgressBar#ThinProgress {{
    height: 4px;
    border-radius: 2px;
}}

/* ==========================================================================
   Tabs, lists, tables, trees
   ========================================================================== */
QTabWidget::pane {{
    border: 1px solid {with_alpha(t.border, 0.75)};
    border-radius: {radius};
    background: {t.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {t.text_muted};
    padding: 8px 16px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-top-left-radius: {radius_small};
    border-top-right-radius: {radius_small};
    font-weight: 550;
}}
QTabBar::tab:hover {{
    background: {with_alpha(t.text, 0.06)};
    color: {t.text};
}}
QTabBar::tab:selected {{
    background: {t.surface};
    color: {accent if t.is_dark else darken(accent, 0.12)};
    border-color: {with_alpha(t.border, 0.8)};
    border-bottom-color: {t.surface};
}}
QTabBar::close-button {{
    image: none;
    background: transparent;
}}
QTabBar::close-button:hover {{
    background: {with_alpha(t.danger, 0.4)};
    border-radius: 5px;
}}
QListWidget, QListView, QTreeView, QTableView, QColumnView {{
    background: {t.surface if not t.is_dark else with_alpha("#000000", 0.12)};
    alternate-background-color: {with_alpha(t.surface_alt, 0.5)};
    border: 1px solid {with_alpha(t.border, 0.8)};
    border-radius: {radius};
    padding: 4px;
    outline: none;
    gridline-color: {with_alpha(t.border, 0.5)};
}}
QListWidget::item, QListView::item, QTreeView::item {{
    border-radius: {radius_small};
    padding: 6px 8px;
    margin: 1px 0;
    color: {t.text};
}}
QListWidget::item:hover, QListView::item:hover, QTreeView::item:hover {{
    background: {t.surface_hover if t.is_dark else t.surface_alt};
}}
QListWidget::item:selected, QListView::item:selected, QTreeView::item:selected {{
    background: {t.accent_soft};
    color: {t.text};
    border: 1px solid {t.accent_border};
}}
QTableView::item {{
    padding: 5px 8px;
    border: none;
}}
QTableView::item:selected {{
    background: {t.accent_soft};
    color: {t.text};
}}
QHeaderView::section {{
    background: {with_alpha(t.surface_alt, 0.9)};
    color: {t.text_muted};
    border: none;
    border-bottom: 1px solid {with_alpha(t.border, 0.9)};
    border-right: 1px solid {with_alpha(t.border, 0.5)};
    padding: 7px 9px;
    font-size: 11px;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}}
QHeaderView::section:hover {{
    color: {t.text};
}}
QHeaderView::up-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {accent};
    width: 0; height: 0;
}}
QHeaderView::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {accent};
    width: 0; height: 0;
}}
QTableCornerButton::section {{
    background: {t.surface_alt};
    border: none;
}}
QTreeView::branch {{
    background: transparent;
}}

/* ==========================================================================
   Scrollbars
   ========================================================================== */
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {with_alpha(t.text_muted, 0.42)};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {with_alpha(t.text_muted, 0.7)};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {with_alpha(t.text_muted, 0.42)};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {with_alpha(t.text_muted, 0.7)};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0; border: none; background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
QAbstractScrollArea::corner {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

/* ==========================================================================
   Menus, docks, dialogs
   ========================================================================== */
QMenuBar {{
    background: transparent;
    color: {t.text};
    padding: 2px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 10px;
    border-radius: {radius_small};
}}
QMenuBar::item:selected {{
    background: {with_alpha(t.text, 0.09)};
}}
QMenu {{
    background: {t.background_elevated};
    border: 1px solid {with_alpha(t.border_strong, 0.85)};
    border-radius: {radius};
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 26px 7px 12px;
    border-radius: {radius_small};
    color: {t.text};
}}
QMenu::item:selected {{
    background: {t.accent_soft};
    color: {accent if t.is_dark else darken(accent, 0.12)};
}}
QMenu::item:disabled {{
    color: {t.text_faint};
}}
QMenu::separator {{
    height: 1px;
    background: {with_alpha(t.border, 0.85)};
    margin: 5px 8px;
}}
QMenu::icon {{
    padding-left: 6px;
}}
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {t.text_muted};
}}
QDockWidget::title {{
    background: {t.surface_alt};
    padding: 6px 10px;
    border-bottom: 1px solid {with_alpha(t.border, 0.8)};
}}
QSplitter::handle {{
    background: {with_alpha(t.border, 0.7)};
}}
QSplitter::handle:hover {{
    background: {t.accent_border};
}}
QGroupBox {{
    border: 1px solid {with_alpha(t.border, 0.85)};
    border-radius: {radius};
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {t.text_muted};
    font-size: 12px;
}}
QStatusBar {{
    background: {with_alpha(t.background_alt, 0.92)};
    color: {t.text_muted};
    border-top: 1px solid {with_alpha(t.border, 0.65)};
}}
QStatusBar::item {{
    border: none;
}}
QDialogButtonBox QPushButton {{
    min-width: 84px;
}}
QMessageBox {{
    background: {t.background};
}}
QMessageBox QLabel {{
    color: {t.text};
}}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {with_alpha(t.border, 0.8)};
}}

/* ==========================================================================
   Sidebar navigation
   ========================================================================== */
#NavButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius_small};
    padding: 8px 12px;
    color: {t.text_muted};
    text-align: left;
    font-weight: 550;
    min-height: 22px;
}}
#NavButton:hover {{
    background: {with_alpha(t.text, 0.07)};
    color: {t.text};
}}
#NavButton:checked {{
    background: {t.accent_soft};
    color: {accent if t.is_dark else darken(accent, 0.15)};
    border-color: {with_alpha(accent, 0.22)};
    font-weight: 650;
}}
#NavButton:checked:hover {{
    background: {with_alpha(accent, 0.22)};
}}
#NavBadge {{
    background: {with_alpha(accent, 0.22)};
    color: {accent if t.is_dark else darken(accent, 0.15)};
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 700;
}}
#NavBadge[severity="warning"] {{
    background: {with_alpha(t.warning, 0.22)};
    color: {t.warning if t.is_dark else darken(t.warning, 0.28)};
}}
#NavBadge[severity="danger"] {{
    background: {with_alpha(t.danger, 0.2)};
    color: {t.danger if t.is_dark else darken(t.danger, 0.2)};
}}
#NavBadge[severity="success"] {{
    background: {with_alpha(t.success, 0.2)};
    color: {t.success if t.is_dark else darken(t.success, 0.25)};
}}

/* ==========================================================================
   Specialised widgets
   ========================================================================== */
#DropZone {{
    background: {with_alpha(accent, 0.06)};
    border: 2px dashed {with_alpha(t.border_strong, 0.9)};
    border-radius: {radius_large};
    color: {t.text_muted};
}}
#DropZone[dragActive="true"] {{
    background: {with_alpha(accent, 0.14)};
    border-color: {accent};
    color: {t.text};
}}
#EmptyState {{
    color: {t.text_muted};
}}
#EmptyStateTitle {{
    font-size: 15px;
    font-weight: 650;
    color: {t.text};
}}
#LevelMeter {{
    background: transparent;
}}
#CardTitle {{
    font-size: 13px;
    font-weight: 650;
}}
#CardValue {{
    font-size: 20px;
    font-weight: 700;
}}
#SegmentedControl {{
    background: {with_alpha(t.text, 0.07)};
    border: 1px solid {with_alpha(t.border, 0.6)};
    border-radius: {radius_small};
    padding: 3px;
}}
#ColourSwatch {{
    border: 1px solid {with_alpha(t.border_strong, 0.8)};
    border-radius: {radius_small};
}}
#AccentSwatch[selected="true"] {{
    border: 2px solid {accent};
}}
#DiffAdded {{
    background: {with_alpha(t.success, 0.22)};
    color: {t.text};
}}
#DiffRemoved {{
    background: {with_alpha(t.danger, 0.20)};
    text-decoration: line-through;
}}
#DiacriticMark {{
    color: {accent if t.is_dark else darken(accent, 0.1)};
}}
#WaveformPreview {{
    background: {with_alpha("#000000", 0.16) if t.is_dark else with_alpha("#FFFFFF", 0.6)};
    border: 1px solid {with_alpha(t.border, 0.8)};
    border-radius: {radius};
}}
#TranscriptPane {{
    background: {t.surface};
    border: 1px solid {with_alpha(t.border, 0.85)};
    border-radius: {radius};
}}
#ModelRow {{
    background: {t.surface};
    border: 1px solid {with_alpha(t.border, 0.85)};
    border-radius: {radius};
}}
#ModelRow:hover {{
    background: {t.surface_alt};
    border-color: {t.accent_border};
}}
#StatTile {{
    background: {t.surface};
    border: 1px solid {with_alpha(t.border, 0.8)};
    border-radius: {radius};
}}
#WelcomeStep {{
    background: {with_alpha(t.surface_alt, 0.65)};
    border: 1px solid {with_alpha(t.border, 0.7)};
    border-radius: {radius};
}}
#ToastFrame {{
    background: {t.background_elevated};
    border: 1px solid {with_alpha(t.border_strong, 0.9)};
    border-radius: {radius};
}}
#TitleLabel {{
    font-size: 13px;
    font-weight: 600;
}}
#WindowControl {{
    background: transparent;
    border: none;
    border-radius: {radius_small};
    padding: 4px 5px;
    color: {t.text_muted};
}}
#WindowControl:hover {{
    background: {with_alpha(t.text, 0.10)};
    color: {t.text};
}}
#CloseWindowControl:hover {{
    background: {t.danger};
    color: #FFFFFF;
}}
#ThemePreview {{
    border: 1px solid {with_alpha(t.border_strong, 0.8)};
    border-radius: {radius_small};
    padding: 3px;
}}
#ThemePreview[selected="true"] {{
    border: 2px solid {accent};
}}

/* ==========================================================================
   Calendar / time pickers
   ========================================================================== */
QCalendarWidget QWidget {{
    alternate-background-color: {t.surface_alt};
}}
QCalendarWidget QAbstractItemView:enabled {{
    color: {t.text};
    background: {t.surface};
    selection-background-color: {accent};
    selection-color: {accent_text};
}}
QCalendarWidget QToolButton {{
    color: {t.text};
    background: transparent;
}}

/* ==========================================================================
   Accessibility: high-contrast focus ring for keyboard users
   ========================================================================== */
QWidget[highContrast="true"]:focus {{
    border: 2px solid {accent};
}}
"""


def build_scroll_indicator_style(tokens: ThemeTokens) -> str:
    return f"""
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: {alpha_hex(tokens.border, 0.4)};
}}
"""


def colour_pair(tokens: ThemeTokens, key: str) -> tuple[str, str]:
    """(light, dark) pair for a semantic colour — used by the appearance preview."""
    from .tokens import _DARK_BASE, _LIGHT_BASE

    if key in _LIGHT_BASE and key in _DARK_BASE:
        return (_LIGHT_BASE[key], _DARK_BASE[key])
    light = getattr(_tokens_for("light", tokens), key, tokens.text)
    dark = getattr(_tokens_for("dark", tokens), key, tokens.text)
    return (light, dark)


def danger_state(colour: str) -> str:
    return darken(colour, 0.18)


def _tokens_for(mode: str, fallback: ThemeTokens | None = None) -> ThemeTokens:
    existing = _REGISTERED_TOKENS.get(mode)
    if existing is not None:
        return existing
    from .tokens import build_tokens

    return build_tokens(mode=mode) if fallback is None else fallback


def build_badge_style(tokens: ThemeTokens, severity: str) -> str:
    colour_map = {
        "info": tokens.info,
        "success": tokens.success,
        "warning": tokens.warning,
        "danger": tokens.danger,
        "accent": tokens.accent_primary,
        "neutral": tokens.text_muted,
    }
    colour = colour_map.get(severity, tokens.text_muted)
    foreground = colour if tokens.is_dark else darken(colour, 0.25)
    return (
        f"background: {with_alpha(colour, 0.16 if tokens.is_dark else 0.14)};"
        f" color: {foreground};"
        f" border: 1px solid {with_alpha(colour, 0.35)};"
        f" border-radius: {tokens.radius_small};"
        " padding: 2px 8px; font-size: 11px; font-weight: 650;"
    )


def mix_tokens(tokens: ThemeTokens, colour: str, background: str) -> str:
    return mix(colour, background, 0.5)


# Populated by the theme manager so :func:`_tokens_for` can resolve without a
# circular import; harmless if it stays empty.
_REGISTERED_TOKENS: dict[str, ThemeTokens] = {}


def register_tokens(mode: str, tokens: ThemeTokens) -> None:
    _REGISTERED_TOKENS[mode] = tokens
