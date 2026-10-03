"""Centralized design tokens and the Qt stylesheet built from them. No colors live elsewhere."""

from __future__ import annotations

from dataclasses import dataclass

FONT_FAMILY = "Segoe UI Variable Text, Segoe UI, Arial"
RADIUS = 12
RADIUS_SMALL = 8


@dataclass(frozen=True)
class Palette:
    name: str
    background: str
    sidebar: str
    surface: str
    surface_alt: str
    surface_hover: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    on_accent: str
    success: str
    warning: str
    error: str
    shadow: str


DARK = Palette(
    name="dark",
    background="#0E0F13", sidebar="#121319", surface="#171820", surface_alt="#1D1F29",
    surface_hover="#242734", border="#262937", border_strong="#353949",
    text="#ECEDF3", text_secondary="#A3A7B7", text_muted="#6E7285",
    accent="#7B61FF", accent_hover="#8F79FF", accent_pressed="#6A4FF0", accent_soft="#7B61FF2E",
    on_accent="#FFFFFF", success="#34D399", warning="#FBBF24", error="#F87171", shadow="#000000",
)

LIGHT = Palette(
    name="light",
    background="#F4F5F9", sidebar="#FFFFFF", surface="#FFFFFF", surface_alt="#F0F1F6",
    surface_hover="#E9EBF2", border="#E1E3EB", border_strong="#CDD0DC",
    text="#15161C", text_secondary="#4F5466", text_muted="#8A8FA3",
    accent="#6A4FF0", accent_hover="#5B40E0", accent_pressed="#4D33CC", accent_soft="#6A4FF01F",
    on_accent="#FFFFFF", success="#059669", warning="#B45309", error="#DC2626", shadow="#1A1B2A",
)

_PALETTES = {"dark": DARK, "light": LIGHT}
_current: Palette = DARK


def palette() -> Palette:
    return _current


def set_theme(name: str) -> Palette:
    global _current
    _current = _PALETTES.get(name, DARK)
    return _current


def _qt_color(value: str) -> str:
    """Convert #RRGGBBAA tokens to Qt's #AARRGGBB notation."""
    raw = value.lstrip("#")
    if len(raw) == 8:
        return f"#{raw[6:]}{raw[:6]}"
    return value


def build_stylesheet(p: Palette, combo_arrow: str = "") -> str:
    """`combo_arrow` is the path of an image used as the combo box arrow."""
    c = {k: _qt_color(v) for k, v in p.__dict__.items() if isinstance(v, str)}
    return f"""
* {{ font-family: {FONT_FAMILY}; font-size: 10pt; color: {c['text']}; outline: none; }}
QMainWindow, QDialog, #PageRoot {{ background: {c['background']}; }}
QWidget#Transparent, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['border_strong']};
    padding: 6px 8px; border-radius: 6px; }}

/* ---------- Sidebar ---------- */
#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
#SidebarTitle {{ font-size: 11pt; font-weight: 700; }}
#SidebarSubtitle, #SidebarFooter {{ color: {c['text_muted']}; font-size: 8.5pt; }}
QPushButton#NavButton {{ text-align: left; padding: 10px 14px; border: none; border-radius: {RADIUS_SMALL}px;
    color: {c['text_secondary']}; font-size: 10.5pt; background: transparent; }}
QPushButton#NavButton:hover {{ background: {c['surface_hover']}; color: {c['text']}; }}
QPushButton#NavButton:checked {{ background: {c['accent_soft']}; color: {c['text']}; font-weight: 600; }}
QPushButton#NavButton:focus {{ border: 1px solid {c['accent']}; }}

/* ---------- Typography ---------- */
#PageTitle {{ font-size: 20pt; font-weight: 700; }}
#PageSubtitle {{ color: {c['text_secondary']}; font-size: 10.5pt; }}
#SectionTitle {{ font-size: 12pt; font-weight: 600; }}
#FieldLabel {{ color: {c['text_muted']}; font-size: 8.5pt; font-weight: 600; }}
#Secondary {{ color: {c['text_secondary']}; }}
#Muted {{ color: {c['text_muted']}; font-size: 9pt; }}
#CardTitle {{ font-size: 11.5pt; font-weight: 600; }}
#StatValue {{ font-size: 22pt; font-weight: 700; }}
#StatLabel {{ color: {c['text_secondary']}; font-size: 9.5pt; }}
#ErrorText {{ color: {c['error']}; }}
#SuccessText {{ color: {c['success']}; font-weight: 600; }}
#WarningText {{ color: {c['warning']}; }}

/* ---------- Cards ---------- */
#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {RADIUS}px; }}
#Card[selected="true"] {{ border: 1px solid {c['accent']}; }}
#CardAlt {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: {RADIUS_SMALL}px; }}
#Banner {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; border-radius: {RADIUS_SMALL}px; }}
#Thumb {{ background: {c['surface_alt']}; border-radius: {RADIUS_SMALL}px; }}
#Separator {{ background: {c['border']}; max-height: 1px; min-height: 1px; }}

/* ---------- Chips ---------- */
#Chip {{ border: 1px solid transparent; border-radius: 10px; padding: 2px 10px; font-size: 8.5pt; font-weight: 600;
    background: {c['surface_hover']}; color: {c['text_secondary']}; }}
#Chip[tone="accent"] {{ background: {c['accent_soft']}; color: {c['accent_hover']}; }}
#Chip[tone="success"] {{ background: {_qt_color(p.success + '26')}; color: {c['success']}; }}
#Chip[tone="warning"] {{ background: {_qt_color(p.warning + '26')}; color: {c['warning']}; }}
#Chip[tone="error"] {{ background: {_qt_color(p.error + '26')}; color: {c['error']}; }}

/* ---------- Inputs ---------- */
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: {RADIUS_SMALL}px;
    padding: 8px 12px; selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{ border-color: {c['border_strong']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {c['text_muted']}; background: {c['surface']}; }}
QLineEdit#UrlInput {{ font-size: 12pt; padding: 12px 14px; border-radius: 10px; }}
QLineEdit#UrlInput[invalid="true"] {{ border: 1px solid {c['error']}; }}
QComboBox {{ padding-right: 30px; min-height: 20px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{combo_arrow}"); width: 14px; height: 14px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {c['surface_alt']}; border: 1px solid {c['border_strong']};
    border-radius: 8px; padding: 4px; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}

/* ---------- Buttons ---------- */
QPushButton {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: {RADIUS_SMALL}px;
    padding: 8px 16px; font-weight: 600; min-height: 18px; }}
QPushButton:hover {{ background: {c['surface_hover']}; border-color: {c['border_strong']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus {{ border: 1px solid {c['accent']}; }}
QPushButton:disabled {{ color: {c['text_muted']}; background: {c['surface']}; border-color: {c['border']}; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: {c['on_accent']}; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background: {c['accent_pressed']}; }}
QPushButton[variant="primary"]:focus {{ border: 2px solid {c['text']}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['surface_hover']}; border-color: {c['surface_hover']};
    color: {c['text_muted']}; }}
QPushButton[variant="danger"] {{ color: {c['error']}; }}
QPushButton[variant="danger"]:hover {{ background: {_qt_color(p.error + '1F')}; border-color: {c['error']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {c['text_secondary']}; }}
QPushButton[variant="ghost"]:hover {{ background: {c['surface_hover']}; color: {c['text']}; }}
QPushButton[variant="ghost"]:focus {{ border: 1px solid {c['accent']}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {c['accent_hover']}; padding: 4px 2px; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QPushButton[variant="icon"] {{ background: transparent; border: 1px solid transparent; padding: 6px; min-width: 20px; }}
QPushButton[variant="icon"]:hover {{ background: {c['surface_hover']}; }}
QPushButton[variant="icon"]:focus {{ border: 1px solid {c['accent']}; }}
QPushButton[variant="filter"] {{ background: transparent; border: 1px solid {c['border']}; border-radius: 16px;
    padding: 6px 14px; color: {c['text_secondary']}; font-weight: 600; }}
QPushButton[variant="filter"]:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['text']}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

/* ---------- Menus ---------- */
QMenu {{ background: {c['surface_alt']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; }}
QMenu::item:disabled {{ color: {c['text_muted']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}

/* ---------- Checkbox ---------- */
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['border_strong']};
    background: {c['surface_alt']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}

/* ---------- Slider ---------- */
QSlider::groove:horizontal {{ height: 4px; background: {c['border_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['text']}; border: 2px solid {c['accent']}; width: 12px; height: 12px;
    margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:focus {{ border-color: {c['accent_hover']}; }}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text_muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0; background: none; }}

/* ---------- Tutorial ---------- */
#TutorialBubble {{ background: {c['surface']}; border: 1px solid {c['accent']}; border-radius: 14px; }}
#EmptyTitle {{ font-size: 14pt; font-weight: 700; }}

/* ---------- Toast ---------- */
#Toast {{ background: {c['surface_alt']}; border: 1px solid {c['border_strong']}; border-radius: 10px; }}
#ToastTitle {{ font-weight: 700; }}

/* ---------- Floating widget ---------- */
#WidgetTitle {{ font-size: 9pt; font-weight: 800; letter-spacing: 1.5px; }}
#WidgetSection {{ color: {c['text_muted']}; font-size: 7.5pt; font-weight: 700; letter-spacing: 1px; }}
#WidgetCaption, #WidgetPowered {{ color: {c['text_muted']}; font-size: 7.5pt; }}
#WidgetBanner {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; border-radius: {RADIUS_SMALL}px; }}
#WidgetTitleText {{ font-weight: 600; }}
QFrame#QueueRow {{ background: transparent; border: 1px solid transparent; border-radius: {RADIUS_SMALL}px; }}
QFrame#QueueRow:hover {{ background: {c['surface_hover']}; }}
QFrame#QueueRow[selected="true"] {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; }}
QPushButton#QueueButton {{ background: {c['accent_soft']}; border: 1px solid transparent; border-radius: 12px;
    padding: 4px 10px; color: {c['text']}; font-size: 8.5pt; }}
QPushButton#QueueButton:checked {{ border: 1px solid {c['accent']}; }}
QPushButton#QueueButton:focus {{ border: 1px solid {c['accent']}; }}
#BubbleCount {{ font-weight: 800; font-size: 10.5pt; }}
QLineEdit[tourTarget="true"], QPushButton[tourTarget="true"] {{ border: 2px solid {c['accent']}; }}

/* ---------- Settings tabs and shortcuts ---------- */
QPushButton#ShortcutKey {{ background: {c['surface_alt']}; border: 1px solid {c['border_strong']}; border-radius: 6px;
    padding: 6px 12px; min-width: 150px; font-family: "Cascadia Mono", Consolas, {FONT_FAMILY}; font-weight: 600; }}
QPushButton#ShortcutKey:hover {{ border-color: {c['accent']}; }}
QPushButton#ShortcutKey:focus {{ border: 1px solid {c['accent']}; }}
QPushButton#ShortcutKey[disabledKey="true"] {{ color: {c['text_muted']}; font-style: italic; }}
#CaptureBox {{ background: {c['surface_alt']}; border: 1px dashed {c['accent']}; border-radius: {RADIUS_SMALL}px; }}
#CaptureKeys {{ font-size: 16pt; font-weight: 700; font-family: "Cascadia Mono", Consolas, {FONT_FAMILY}; }}
"""
