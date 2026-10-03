"""Settings, organized in tabs (saved automatically on change)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QSlider,
    QWidget,
)

from app import APP_NAME, APP_VERSION, SYSTEM_NAME
from app.core.models.settings import (
    CONCURRENCY_OPTIONS,
    EXISTING_POLICIES,
    HISTORY_LIMITS,
    ORGANIZE_PATTERNS,
    THEMES,
    WIDGET_OPACITY_MAX,
    WIDGET_OPACITY_MIN,
    WIDGET_POSITIONS,
    AppSettings,
)
from app.ui.context import AppContext
from app.ui.dialogs.confirm_dialog import ask
from app.ui.pages.base import Page
from app.ui.pages.shortcuts_panel import ShortcutKeyButton, ShortcutsPanel
from app.ui.pages.updates_panel import UpdatesPanel
from app.ui.ytdlp_updates import YtDlpUpdateController
from app.ui.widgets.animated import ToggleSwitch
from app.ui.widgets.common import (
    Card,
    Separator,
    hbox,
    icon_button,
    make_button,
    make_label,
    vbox,
)
from app.utils.paths import logs_dir

TABS = (
    ("general", "Geral"), ("downloads", "Downloads"), ("widget", "Widget"), ("shortcuts", "Atalhos"),
    ("notifications", "Notificações"), ("history", "Histórico"), ("appearance", "Aparência"),
    ("tutorial", "Tutorial"), ("diagnostics", "Diagnóstico"), ("updates", "Atualizações"), ("about", "Sobre"),
)
_WIDGET_DEPENDENT = ("widget_start_with_windows", "widget_always_on_top", "widget_detect_clipboard",
                     "widget_minimize_after_start", "widget_notifications")


class FlowLayout(QLayout):
    """Wraps its items onto new lines (the tab bar on narrow windows)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + self._spacing, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y()


class PathPicker(QWidget):
    changed = Signal(str)

    def __init__(self, value: str, dialog_title: str) -> None:
        super().__init__()
        self.setObjectName("Transparent")
        self._title = dialog_title
        self.edit = QLineEdit(value)
        self.edit.setAccessibleName(dialog_title)
        self.edit.setMinimumWidth(280)
        self.edit.editingFinished.connect(self._commit)
        browse = icon_button("folder", dialog_title)
        browse.clicked.connect(self._browse)
        self.setLayout(hbox(self.edit, browse, spacing=6))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._title, self.edit.text())
        if folder:
            self.edit.setText(str(Path(folder)))
            self._commit()

    def _commit(self) -> None:
        text = self.edit.text().strip()
        if text and Path(text).is_absolute():
            self.changed.emit(text)

    def set_value(self, value: str) -> None:
        if self.edit.text() != value:
            self.edit.setText(value)


def _combo(options: dict, current: object, on_change: Callable[[object], None], name: str) -> QComboBox:
    combo = QComboBox()
    combo.setAccessibleName(name)
    for value, label in options.items():
        combo.addItem(label, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.currentIndexChanged.connect(lambda _: on_change(combo.currentData()))
    combo.setMinimumWidth(200)
    return combo


class SettingsPage(Page):
    show_diagnostics = Signal()
    start_tutorial = Signal()
    start_widget_tour = Signal()
    reset_requested = Signal()
    open_about = Signal()

    def __init__(self, context: AppContext, updates: YtDlpUpdateController) -> None:
        super().__init__("Configurações", "As alterações são salvas automaticamente.")
        self._ctx = context
        self._toggles: dict[str, ToggleSwitch] = {}
        self._rows: dict[str, QWidget] = {}
        self.tabs: dict[str, QWidget] = {}
        self._tab_buttons = {}
        self.current_tab = "general"

        bar = QWidget()
        bar.setObjectName("Transparent")
        flow = FlowLayout(bar, spacing=8)
        for key, label in TABS:
            button = make_button(label, "filter")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.select_tab(k))
            flow.addWidget(button)
            self._tab_buttons[key] = button
        self.tab_bar = bar
        self.content.addWidget(bar)
        for key, _label in TABS:
            holder = QWidget()
            holder.setObjectName("Transparent")
            holder.setLayout(vbox(spacing=18))
            holder.hide()
            self.tabs[key] = holder
            self.content.addWidget(holder)

        self._build_general()
        self._build_downloads()
        self._build_widget()
        self.shortcuts_panel = ShortcutsPanel(context)
        self.tabs["shortcuts"].layout().addWidget(self.shortcuts_panel)
        self._build_notifications()
        self._build_history()
        self._build_appearance()
        self._build_tutorial()
        self._build_diagnostics()
        self.updates_panel = UpdatesPanel(context, updates)
        self.tabs["updates"].layout().addWidget(self.updates_panel)
        self._build_about()
        self.finish()
        self.select_tab("general")
        context.settings.add_listener(self._on_settings)
        self._sync_widget_rows(context.settings.settings)

    # ------------------------------------------------------------------ tabs
    def select_tab(self, key: str) -> None:
        if key not in self.tabs:
            return
        self.current_tab = key
        for name, holder in self.tabs.items():
            holder.setVisible(name == key)
            self._tab_buttons[name].setChecked(name == key)
        self.scroll.verticalScrollBar().setValue(0)

    def tab_button(self, key: str):
        return self._tab_buttons[key]

    # ------------------------------------------------------------ sections
    def _build_general(self) -> None:
        self.first_section = self._section("general", "Inicialização e janela", [
            (f"Iniciar Luut com o {SYSTEM_NAME}",
             f"Abre automaticamente ao entrar no {SYSTEM_NAME}, direto na bandeja do sistema.",
             self._toggle("start_with_windows")),
            ("Iniciar minimizado", "Abre o aplicativo direto na bandeja do sistema.", self._toggle("start_minimized")),
            ("Minimizar para a bandeja ao fechar", "O botão fechar mantém o aplicativo na bandeja do sistema.",
             self._toggle("minimize_to_tray")),
            ("Sugerir link copiado ao abrir", "Ao abrir, oferece usar um link encontrado na área de transferência.",
             self._toggle("suggest_clipboard")),
        ])

    def _build_downloads(self) -> None:
        s = self._ctx.settings.settings
        self.default_dir = PathPicker(s.default_dir, "Escolher pasta padrão")
        self.default_dir.changed.connect(lambda v: self._save(default_dir=v, last_dir=""))
        self.temp_dir = PathPicker(s.temp_dir, "Escolher diretório temporário")
        self.temp_dir.changed.connect(self._change_temp_dir)
        self.organize_pattern = _combo(ORGANIZE_PATTERNS, s.organize_pattern,
                                       lambda v: self._save(organize_pattern=v), "Estrutura de pastas")
        self.organize_pattern.setEnabled(s.organize_enabled)
        self.concurrency = _combo({v: str(v) for v in CONCURRENCY_OPTIONS}, s.max_concurrent,
                                  lambda v: self._save(max_concurrent=int(v)), "Downloads simultâneos")
        self._section("downloads", "Downloads", [
            ("Pasta padrão", "Onde os vídeos são salvos por padrão (também usada pelo widget).", self.default_dir),
            ("Downloads simultâneos", "Quantos downloads acontecem ao mesmo tempo. A fila em si não tem limite: "
             "adicione quantos quiser.", self.concurrency),
            ("Arquivos existentes", "O que fazer quando já existir um arquivo com o mesmo nome.",
             _combo(EXISTING_POLICIES, s.existing_file_policy, lambda v: self._save(existing_file_policy=v),
                    "Arquivos existentes")),
            ("Organizar downloads automaticamente", "Cria subpastas, por exemplo Vídeos/2026/Setembro.",
             self._toggle("organize_enabled")),
            ("Estrutura de pastas", "Como as subpastas são organizadas.", self.organize_pattern),
            ("Diretório temporário", "Arquivos parciais (.part) ficam aqui até o download terminar.", self.temp_dir),
        ])

    def _build_widget(self) -> None:
        s = self._ctx.settings.settings
        self.widget_position = _combo(WIDGET_POSITIONS, s.widget_position,
                                      lambda v: self._save(widget_position=v), "Posição padrão do widget")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(WIDGET_OPACITY_MIN, WIDGET_OPACITY_MAX)
        self.opacity.setValue(s.widget_opacity)
        self.opacity.setMinimumWidth(180)
        self.opacity.setAccessibleName("Opacidade do widget")
        self.opacity_label = make_label(f"{s.widget_opacity}%", "Secondary")
        self.opacity_label.setMinimumWidth(40)
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.opacity.sliderReleased.connect(lambda: self._save(widget_opacity=self.opacity.value()))
        self.opacity.valueChanged.connect(
            lambda v: None if self.opacity.isSliderDown() else self._save(widget_opacity=int(v)))
        opacity_box = QWidget()
        opacity_box.setObjectName("Transparent")
        opacity_box.setLayout(hbox(self.opacity, self.opacity_label, spacing=8))
        self.widget_toggle_key = ShortcutKeyButton(self._ctx, "widget.toggle")
        tour = make_button("Conhecer o widget", None, "graduation-cap")
        tour.clicked.connect(self.start_widget_tour.emit)
        self.widget_tour_button = tour
        self.widget_section = self._section("widget", "Widget flutuante", [
            ("Ativar Widget Flutuante", "Uma janela compacta para colar um link e baixar sem abrir a janela "
             "principal. Usa a mesma fila, histórico e configurações.", self._toggle("widget_enabled")),
            (f"Iniciar Widget com o {SYSTEM_NAME}", f"Ao entrar no {SYSTEM_NAME}, o Luut abre na bandeja e mostra o widget.",
             self._toggle("widget_start_with_windows")),
            ("Sempre no topo", "Mantém o widget acima das outras janelas.", self._toggle("widget_always_on_top")),
            ("Detectar URLs copiadas", "Oferece analisar um link quando você o copia. Lê apenas o texto atual da "
             "área de transferência, não guarda histórico e nunca inicia um download sozinho.",
             self._toggle("widget_detect_clipboard")),
            ("Minimizar após iniciar download", "Transforma o widget no botão compacto depois de iniciar.",
             self._toggle("widget_minimize_after_start")),
            ("Notificações do Widget", "Avisa quando um download é adicionado pelo widget.",
             self._toggle("widget_notifications")),
            ("Posição padrão", "Onde o widget aparece. “Última posição” lembra onde você o deixou.",
             self.widget_position),
            ("Opacidade", "Transparência do widget (70% a 100%).", opacity_box),
            ("Atalho para mostrar/ocultar", "Funciona mesmo com o Luut em segundo plano. Também editável em "
             "Atalhos.", self.widget_toggle_key),
            ("Tour do widget", "Mostra rapidamente como usar o widget.", tour),
        ], keys=("widget_enabled", *_WIDGET_DEPENDENT, "widget_position", "widget_opacity", "widget_shortcut",
                 "widget_tour"))

    def _build_notifications(self) -> None:
        self._section("notifications", "Notificações", [
            ("Download iniciado", "Avisa quando um download começa.", self._toggle("notify_started")),
            ("Download concluído", "Avisa quando um download termina, com o botão Abrir arquivo.",
             self._toggle("notify_completed")),
            ("Erros", "Avisa quando um download falha, com o botão Tentar novamente.", self._toggle("notify_errors")),
        ])

    def _build_history(self) -> None:
        s = self._ctx.settings.settings
        clear = make_button("Limpar histórico", "danger", "trash")
        clear.clicked.connect(self._clear_history)
        self._section("history", "Histórico", [
            ("Registrar histórico", "Mantém um registro dos downloads realizados.", self._toggle("history_enabled")),
            ("Limite de registros", "Registros mais antigos são removidos automaticamente.",
             _combo({v: (str(v) if v else "Sem limite") for v in HISTORY_LIMITS}, s.history_limit,
                    lambda v: self._save(history_limit=int(v)), "Limite de registros")),
            ("Limpar histórico", "Apaga todos os registros (os arquivos baixados não são excluídos).", clear),
        ])

    def _build_appearance(self) -> None:
        self._section("appearance", "Aparência", [
            ("Tema", "Aparência do aplicativo e do widget.",
             _combo(THEMES, self._ctx.settings.settings.theme, lambda v: self._save(theme=v), "Tema")),
        ])

    def _build_tutorial(self) -> None:
        tutorial = make_button("Iniciar tutorial novamente", "primary", "graduation-cap")
        tutorial.clicked.connect(self.start_tutorial.emit)
        widget_tour = make_button("Conhecer o widget", None, "window")
        widget_tour.clicked.connect(self.start_widget_tour.emit)
        self.tutorial_toggle = ToggleSwitch()
        self.tutorial_toggle.setAccessibleName("Mostrar tutorial ao abrir")
        self.tutorial_toggle.setChecked(self._ctx.tutorial.should_autostart())
        self.tutorial_toggle.toggled.connect(self._toggle_tutorial_autostart)
        self._section("tutorial", "Ajuda / Tutorial", [
            ("Tutorial", "Percorre a interface explicando cada área, botão e ícone.", tutorial),
            ("Tour do widget", "Explica o widget flutuante (é preciso ativá-lo antes).", widget_tour),
            ("Mostrar tutorial na próxima abertura", "Desligado quando você marca “Não mostrar este tutorial "
             "novamente” ou conclui o tutorial.", self.tutorial_toggle),
        ])

    def _build_diagnostics(self) -> None:
        open_logs = make_button("Abrir pasta de logs", None, "file-text")
        open_logs.clicked.connect(lambda: os.startfile(str(logs_dir())))  # type: ignore[attr-defined]
        diagnostics = make_button("Diagnóstico", None, "activity")
        diagnostics.clicked.connect(self.show_diagnostics.emit)
        reset = make_button("Restaurar aplicativo", "danger", "rotate-ccw")
        reset.clicked.connect(self.reset_requested.emit)
        self.reset_button = reset
        self._section("diagnostics", "Diagnóstico", [
            ("Verificar sistema", "Verifica dependências, pastas, espaço em disco e conexão.", diagnostics),
            ("Logs detalhados", "Registra informações extras para suporte técnico.", self._toggle("verbose_logging")),
            ("Logs", "app.log, downloads.log e errors.log (sem cookies, tokens ou conteúdo da área de "
             "transferência).", open_logs),
            ("Restaurar aplicativo", "Apaga histórico, fila, configurações, atalhos personalizados, preferências "
             "do tutorial, dados temporários e logs. Os vídeos já baixados nas suas pastas não são apagados.",
             reset),
        ])

    def _build_about(self) -> None:
        details = make_button("Ver detalhes", None, "info")
        details.clicked.connect(self.open_about.emit)
        self._section("about", "Sobre", [
            (APP_NAME, f"Versão {APP_VERSION}", details),
        ])

    def _section(self, tab: str, title: str, rows: list[tuple[str, str, QWidget]],
                 keys: tuple[str, ...] | None = None) -> Card:
        card = Card()
        layout = vbox(make_label(title, "SectionTitle"), 4, spacing=12, margins=(22, 18, 22, 18))
        for index, (name, description, control) in enumerate(rows):
            if index:
                layout.addWidget(Separator())
            row_widget = QWidget()
            row_widget.setObjectName("Transparent")
            text = vbox(make_label(name), make_label(description, "Muted", wrap=True), spacing=2)
            row = hbox(text, 16, control, spacing=0, margins=(0, 0, 0, 0))
            row.setStretch(0, 1)
            row_widget.setLayout(row)
            layout.addWidget(row_widget)
            if keys:
                self._rows[keys[index]] = row_widget
        card.setLayout(layout)
        self.tabs[tab].layout().addWidget(card)
        return card

    def _toggle(self, key: str) -> ToggleSwitch:
        switch = ToggleSwitch()
        switch.setChecked(bool(getattr(self._ctx.settings.settings, key)))
        switch.setAccessibleName(key)
        switch.toggled.connect(lambda checked: self._save(**{key: checked}))
        self._toggles[key] = switch
        return switch

    def toggle(self, key: str) -> ToggleSwitch:
        return self._toggles[key]

    # --------------------------------------------------------------- helpers
    def _toggle_tutorial_autostart(self, enabled: bool) -> None:
        repo = self._ctx.tutorial
        if enabled:
            repo.reactivate()
        else:
            repo.set_dont_show(True)

    def refresh_tutorial_toggle(self) -> None:
        self.tutorial_toggle.blockSignals(True)
        self.tutorial_toggle.setChecked(self._ctx.tutorial.should_autostart())
        self.tutorial_toggle.blockSignals(False)

    def on_shown(self) -> None:
        self.refresh_tutorial_toggle()

    def _save(self, **changes: object) -> None:
        self._ctx.settings.update(**changes)

    def _sync_widget_rows(self, settings: AppSettings) -> None:
        for key, row in self._rows.items():
            if key != "widget_enabled":
                row.setEnabled(settings.widget_enabled)

    def _on_settings(self, settings: AppSettings, changed: set[str]) -> None:
        for key, switch in self._toggles.items():
            if key in changed and switch.isChecked() != getattr(settings, key):
                switch.blockSignals(True)
                switch.setChecked(getattr(settings, key))
                switch.blockSignals(False)
        self.organize_pattern.setEnabled(settings.organize_enabled)
        self.default_dir.set_value(settings.default_dir)
        self.temp_dir.set_value(settings.temp_dir)
        if "max_concurrent" in changed:
            self.concurrency.blockSignals(True)
            self.concurrency.setCurrentIndex(self.concurrency.findData(settings.max_concurrent))
            self.concurrency.blockSignals(False)
        if "widget_position" in changed:
            self.widget_position.blockSignals(True)
            self.widget_position.setCurrentIndex(self.widget_position.findData(settings.widget_position))
            self.widget_position.blockSignals(False)
        if "widget_opacity" in changed and self.opacity.value() != settings.widget_opacity:
            self.opacity.blockSignals(True)
            self.opacity.setValue(settings.widget_opacity)
            self.opacity_label.setText(f"{settings.widget_opacity}%")
            self.opacity.blockSignals(False)
        if "widget_enabled" in changed:
            self._sync_widget_rows(settings)

    def _change_temp_dir(self, value: str) -> None:
        if self._ctx.downloads.pending_count():
            self._ctx.notify("Downloads em andamento", "Aguarde os downloads terminarem para trocar o diretório "
                             "temporário.", "warning")
            self.temp_dir.set_value(self._ctx.settings.settings.temp_dir)
            return
        self._save(temp_dir=value)

    def _clear_history(self) -> None:
        if ask(self, "Limpar histórico", "Todos os registros do histórico serão apagados. Deseja continuar?",
               [("cancel", "Voltar", None), ("clear", "Limpar histórico", "danger")]) != "clear":
            return
        for task in self._ctx.history.clear():
            if self._ctx.downloads.get(task.id) is None:
                self._ctx.thumbnails.delete(task.thumbnail_path)
        self._ctx.notify("Histórico limpo", "Os registros foram removidos.", "success")
