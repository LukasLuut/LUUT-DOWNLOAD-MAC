"""Configurações → Atualizações: Luut Video Downloader and yt-dlp versions, checks and installation."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import QWidget

from app import APP_NAME, APP_VERSION
from app.core.services.ytdlp_manager import UpdateStatus
from app.ui.context import AppContext
from app.ui.dialogs.confirm_dialog import ask
from app.ui.widgets.animated import ProgressBar, ToggleSwitch
from app.ui.widgets.common import Card, Chip, Separator, hbox, make_button, make_label, vbox
from app.ui.ytdlp_updates import Phase, YtDlpUpdateController
from app.utils.formatters import format_bytes

# (text, chip tone) of every visual state
STATE_LABELS = {
    "up_to_date": ("✓ Atualizado", "success"),
    "update_available": ("↑ Atualização disponível", "accent"),
    "checking": ("⟳ Verificando...", "neutral"),
    "reading": ("⟳ Verificando...", "neutral"),
    "downloading": ("↓ Baixando atualização...", "accent"),
    "installing": ("⟳ Instalando...", "accent"),
    "error": ("! Não foi possível atualizar", "error"),
    "check_failed": ("! Falha na verificação", "warning"),
    "not_installed": ("! yt-dlp não instalado", "error"),
    "unknown": ("Ainda não verificado", "neutral"),
}


def friendly_datetime(value: str | None) -> str:
    try:
        when = datetime.fromisoformat(value or "")
    except ValueError:
        return "nunca"
    if when.date() == date.today():
        return f"Hoje às {when:%H:%M}"
    return f"{when:%d/%m/%Y %H:%M}"


def state_key(controller: YtDlpUpdateController) -> str:
    if controller.phase in (Phase.CHECKING, Phase.READING, Phase.DOWNLOADING, Phase.INSTALLING):
        return controller.phase.value
    if controller.error and controller.status != UpdateStatus.CHECK_FAILED:
        return "error"
    status = controller.status
    return status.value if status else "unknown"


class UpdatesPanel(QWidget):
    def __init__(self, context: AppContext, controller: YtDlpUpdateController) -> None:
        super().__init__()
        self.setObjectName("Transparent")
        self._ctx = context
        self.controller = controller

        app_card = Card()
        app_text = ("Verificação automática do aplicativo ainda não disponível nesta versão."
                    if not context.updates.is_configured else "Verifique novas versões em Sobre.")
        app_card.setLayout(vbox(make_label("Luut Video Downloader", "SectionTitle"),
                                hbox(make_label(f"{APP_NAME} — versão {APP_VERSION}"), None,
                                     Chip("Versão instalada", "neutral")),
                                make_label(app_text, "Muted", wrap=True), spacing=8, margins=(22, 18, 22, 18)))

        self.version_button = make_button("", "link")
        self.version_button.setToolTip("Ver detalhes do yt-dlp")
        self.version_button.clicked.connect(self.show_details)
        self.chip = Chip()
        self.message = make_label("", "Muted", wrap=True)
        self.bar = ProgressBar()
        self.bar.hide()
        self.bar_text = make_label("", "Secondary")
        self.bar_text.hide()
        self.check_button = make_button("Verificar atualizações", None, "refresh")
        self.check_button.clicked.connect(lambda: controller.check(manual=True))
        self.install_button = make_button("Atualizar agora", "primary", "download")
        self.install_button.clicked.connect(self._install_clicked)
        self.cancel_button = make_button("Cancelar", "ghost", "x")
        self.cancel_button.clicked.connect(controller.cancel)
        self.last_check = make_label("", "Muted")
        self._toggles = {}
        rows = vbox(spacing=10)
        for index, (key, name, description) in enumerate((
                ("ytdlp_auto_check", "Verificar atualizações automaticamente",
                 "No máximo uma vez a cada 24 horas, em segundo plano. O botão acima sempre verifica na hora."),
                ("ytdlp_ask_before_update", "Perguntar antes de instalar atualizações",
                 "Desligado: novas versões são instaladas sozinhas, só quando nada estiver baixando."),
                ("ytdlp_update_when_idle", "Atualizar automaticamente quando os downloads terminarem",
                 "Se você pedir uma atualização durante um download, ela é instalada assim que a fila terminar."))):
            if index:
                rows.addWidget(Separator())
            switch = ToggleSwitch()
            switch.setAccessibleName(name)
            switch.setChecked(bool(getattr(context.settings.settings, key)))
            switch.toggled.connect(lambda checked, k=key: context.settings.update(**{k: checked}))
            self._toggles[key] = switch
            text = vbox(make_label(name), make_label(description, "Muted", wrap=True), spacing=2)
            row = hbox(text, 16, switch, spacing=0)
            row.setStretch(0, 1)
            rows.addLayout(row)

        self.ytdlp_card = Card()
        self.ytdlp_card.setLayout(vbox(
            make_label("yt-dlp", "SectionTitle"),
            make_label("Mecanismo de download, atualizado separadamente do aplicativo a partir do projeto oficial "
                       "yt-dlp (GitHub). Janela principal e widget usam a mesma instalação.", "Muted", wrap=True),
            hbox(make_label("Versão instalada"), None, self.version_button),
            hbox(make_label("Status"), None, self.chip),
            self.message, self.bar, hbox(self.bar_text, None, self.cancel_button),
            hbox(self.check_button, self.install_button, None, self.last_check, spacing=8),
            Separator(), rows, spacing=10, margins=(22, 18, 22, 18)))
        self.setLayout(vbox(app_card, self.ytdlp_card, spacing=18))
        controller.changed.connect(self.refresh)
        controller.progress.connect(self._on_progress)
        context.settings.add_listener(self._on_settings)
        self.refresh()

    # ---------------------------------------------------------------- view
    def refresh(self) -> None:
        c = self.controller
        key = state_key(c)
        text, tone = STATE_LABELS[key]
        self.chip.setText(text)
        self.chip.set_tone(tone)
        installed = c.installed
        self.version_button.setText(installed or ("Verificando..." if c.phase == Phase.READING else "Não instalado"))
        latest = c.latest
        if key == "update_available" and latest:
            self.message.setText(f"Nova versão disponível\n\nInstalada: {installed}\nNova: {latest}")
        elif key == "up_to_date":
            self.message.setText("✓ Você está usando a versão mais recente.")
        elif key == "not_installed":
            self.message.setText(c.error or "O Luut Video Downloader precisa do yt-dlp para realizar downloads.")
        elif key in ("error", "check_failed"):
            self.message.setText(c.error or "")
        elif key in ("checking", "reading"):
            self.message.setText("Verificando...")
        else:
            self.message.setText("")
        self.message.setVisible(bool(self.message.text()))
        working = c.busy
        downloading = c.phase == Phase.DOWNLOADING
        self.bar.setVisible(c.phase in (Phase.DOWNLOADING, Phase.INSTALLING))
        self.bar_text.setVisible(self.bar.isVisible())
        if c.phase == Phase.INSTALLING:
            self.bar.set_fraction(None)
            self.bar_text.setText("Validando e instalando...")
        self.cancel_button.setVisible(downloading)
        self.check_button.setEnabled(not working)
        self.install_button.setVisible(key in ("update_available", "not_installed", "error") and not working)
        self.install_button.setText("Instalar yt-dlp" if installed is None else "Atualizar agora")
        self.last_check.setText(f"Última verificação: {friendly_datetime(self._ctx.settings.settings.ytdlp_last_check)}")

    def _on_progress(self, done: int, total: object) -> None:
        if isinstance(total, int) and total > 0:
            self.bar.set_fraction(done / total)
            self.bar_text.setText(f"Baixando yt-dlp... {done * 100 // total}%  ({format_bytes(done)} de "
                                  f"{format_bytes(total)})")
        else:
            self.bar.set_fraction(None)
            self.bar_text.setText(f"Baixando yt-dlp... {format_bytes(done) if done else ''}")

    def _on_settings(self, settings, changed: set[str]) -> None:
        for key, switch in self._toggles.items():
            if key in changed and switch.isChecked() != getattr(settings, key):
                switch.blockSignals(True)
                switch.setChecked(getattr(settings, key))
                switch.blockSignals(False)
        if "ytdlp_last_check" in changed:
            self.refresh()

    def _install_clicked(self) -> None:
        self.controller.install(manual=True)  # the click itself is the confirmation

    def show_details(self) -> None:
        c = self.controller
        text, _tone = STATE_LABELS[state_key(c)]
        path = c.path or self._ctx.ytdlp.managed_path
        ask(self.window(), "yt-dlp",
            f"Versão instalada: {c.installed or 'não instalado'}\n"
            f"Última versão conhecida: {c.latest or '—'}\n"
            f"Última verificação: {friendly_datetime(self._ctx.settings.settings.ytdlp_last_check)}\n"
            f"Status: {text}\n"
            f"Fonte: projeto oficial yt-dlp (github.com/yt-dlp/yt-dlp)\n"
            f"Local: {path}", [("ok", "OK", "primary")])
