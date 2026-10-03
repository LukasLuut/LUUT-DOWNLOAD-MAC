"""Left navigation."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from app import APP_VERSION
from app.ui import icons, theme
from app.ui.widgets.common import Chip, hbox, make_label, vbox

NAV_ITEMS = (
    ("home", "Início", "home", "Página inicial (Ctrl+1)"),
    ("downloads", "Downloads", "download", "Fila de downloads (Ctrl+2)"),
    ("history", "Histórico", "history", "Histórico de downloads (Ctrl+3)"),
    ("settings", "Configurações", "settings", "Configurações (Ctrl+,)"),
    ("about", "Sobre", "info", "Sobre o aplicativo"),
)


class Sidebar(QFrame):
    page_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(232)
        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._logo = QLabel()
        self._logo.setPixmap(icons.logo_pixmap(36))
        brand = hbox(self._logo, vbox(make_label("LUUT", "SidebarTitle"),
                                      make_label("Video Downloader", "SidebarSubtitle"), spacing=0), None, spacing=10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(4)
        layout.addLayout(brand)
        layout.addSpacing(24)
        for key, text, icon_name, tooltip in NAV_ITEMS:
            button = QPushButton(f"  {text}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tooltip)
            button.setAccessibleName(text)
            button.setProperty("icon_name", icon_name)
            button.clicked.connect(lambda _=False, k=key: self.page_requested.emit(k))
            self._group.addButton(button)
            self._buttons[key] = button
            if key == "downloads":
                self._badge = Chip("", "accent")
                self._badge.hide()
                button.setLayout(hbox(None, self._badge, margins=(0, 0, 10, 0)))
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(make_label(f"Versão {APP_VERSION}", "SidebarFooter"))
        layout.addWidget(make_label("Powered by Luut", "SidebarFooter"))
        self.refresh_icons()

    def set_current(self, key: str) -> None:
        button = self._buttons.get(key)
        if button:
            button.setChecked(True)
        self.refresh_icons()

    def button(self, key: str) -> QPushButton | None:
        return self._buttons.get(key)

    def set_active_count(self, count: int) -> None:
        self._badge.setText(str(count))
        self._badge.setVisible(count > 0)

    def refresh_icons(self) -> None:
        palette = theme.palette()
        for button in self._buttons.values():
            color = palette.accent_hover if button.isChecked() else palette.text_secondary
            button.setIcon(icons.icon(button.property("icon_name"), color, 18))
