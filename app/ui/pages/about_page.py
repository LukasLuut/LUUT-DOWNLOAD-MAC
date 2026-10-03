"""About: version, technologies, licences and credits."""

from __future__ import annotations

import platform

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel

from app import APP_AUTHOR, APP_NAME, APP_VERSION
from app.core.services.update_service import UpdateInfo
from app.infrastructure.tools import find_ffmpeg
from app.ui import icons
from app.ui.context import AppContext
from app.ui.pages.base import Page
from app.ui.widgets.common import Card, Separator, hbox, make_button, make_label, vbox
from app.ui.workers import run_in_background


class AboutPage(Page):
    start_tutorial = Signal()

    def __init__(self, context: AppContext, ytdlp_updates=None) -> None:
        super().__init__("Sobre")
        self._ctx = context
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(88))
        intro = vbox(make_label(APP_NAME, "PageTitle"), make_label(f"Versão {APP_VERSION}", "Secondary"),
                     make_label(f"Uma ferramenta desenvolvida com carinho pelo {APP_AUTHOR} para seu querido amigo Jhow Jhow da Ana.",
                                "Secondary", wrap=True),
                     make_label("Powered by Luut", "SuccessText"), spacing=4)
        tutorial = make_button("Ver tutorial", None, "graduation-cap")
        tutorial.clicked.connect(self.start_tutorial.emit)
        header = Card(shadow=True)
        header.setLayout(hbox(logo, 12, intro, None, tutorial, spacing=12, margins=(28, 24, 28, 24)))
        self.content.addWidget(header)

        self.ytdlp_version = make_label("…", "Secondary")
        rows = [
            ("Python", platform.python_version(), "PSF License"),
            ("PySide6 / Qt", PYSIDE_VERSION, "LGPLv3"),
            ("yt-dlp (atualizado separadamente)", self.ytdlp_version, "Unlicense"),
            ("FFmpeg", "incluído" if find_ffmpeg() else "não encontrado", "LGPL/GPL (conforme a compilação)"),
            ("SQLite", "embutido no Python", "Domínio público"),
            ("Ícones Lucide", "—", "ISC License"),
        ]
        credits = Card()
        layout = vbox(make_label("Tecnologias e créditos", "SectionTitle"), 4, spacing=10, margins=(22, 18, 22, 18))
        for index, (name, version, licence) in enumerate(rows):
            if index:
                layout.addWidget(Separator())
            value = version if isinstance(version, QLabel) else make_label(version, "Secondary")
            row = hbox(make_label(name), None, value, 24, make_label(licence, "Muted"))
            layout.addLayout(row)
        layout.addSpacing(6)
        layout.addWidget(make_label(
            "Este aplicativo baixa apenas conteúdos acessíveis publicamente por meios tecnicamente suportados. "
            "Ele não contorna DRM, paywalls ou autenticação. Respeite os termos de uso de cada site e os direitos "
            "autorais do conteúdo.", "Muted", wrap=True))
        credits.setLayout(layout)
        self.content.addWidget(credits)

        updates = Card()
        self.update_label = make_label("", "Secondary", wrap=True)
        update_layout = vbox(make_label("Atualizações", "SectionTitle"), self.update_label, spacing=10,
                             margins=(22, 18, 22, 18))
        if context.updates.is_configured:
            self.update_label.setText("Verifique se há uma nova versão disponível.")
            check = make_button("Verificar atualizações", None, "refresh")
            check.clicked.connect(self._check_updates)
            update_layout.addLayout(hbox(check, None))
        else:
            self.update_label.setText("A verificação de atualizações ainda não está disponível nesta versão.")
        updates.setLayout(update_layout)
        self.content.addWidget(updates)
        copyright_label = make_label(f"© 2026 {APP_AUTHOR}", "Muted")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content.addWidget(copyright_label)
        self.finish()
        self._updates = ytdlp_updates
        if ytdlp_updates is not None:
            ytdlp_updates.changed.connect(self._show_ytdlp)
            self._show_ytdlp()

    def _show_ytdlp(self) -> None:
        self.ytdlp_version.setText(self._updates.installed or "não instalado")

    def _check_updates(self) -> None:
        self.update_label.setText("Verificando…")
        run_in_background(self._ctx.updates.check, self._show_update,
                          lambda _: self.update_label.setText("Não foi possível verificar agora."))

    def _show_update(self, info: UpdateInfo | None) -> None:
        if info is None:
            self.update_label.setText("Não foi possível verificar agora.")
        elif info.is_newer:
            self.update_label.setText(f"Nova versão disponível: {info.latest_version}\n{info.changelog}")
        else:
            self.update_label.setText("Você já está usando a versão mais recente.")
