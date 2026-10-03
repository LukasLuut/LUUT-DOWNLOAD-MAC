"""Diagnostics screen: runs environment checks in the background."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGridLayout, QWidget

from app.core.services.diagnostics import DiagnosticItem, Level, run_diagnostics
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.dialogs.base import ThemedDialog
from app.ui.widgets.animated import Spinner
from app.ui.widgets.common import Card, hbox, make_button, make_label, vbox
from app.ui.workers import run_in_background

_SYMBOLS = {Level.OK: "✓ OK", Level.WARNING: "⚠ Aviso", Level.ERROR: "✕ Erro"}


class DiagnosticsDialog(ThemedDialog):
    def __init__(self, parent: QWidget | None, context: AppContext) -> None:
        super().__init__(parent, "Diagnóstico")
        self._ctx = context
        self._items: list[DiagnosticItem] = []
        self.resize(720, 600)
        self.grid_holder = Card()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(18, 14, 18, 14)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.spinner = Spinner(20)
        self.spinner.hide()
        self.status = make_label("Clique em “Executar diagnóstico” para verificar o ambiente.", "Secondary")
        self.run_button = make_button("Executar diagnóstico", "primary", "activity")
        self.run_button.clicked.connect(self.run)
        self.copy_button = make_button("Copiar relatório", None, "copy")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        close = make_button("Fechar")
        close.clicked.connect(self.accept)
        self.setLayout(vbox(make_label("Diagnóstico", "SectionTitle"),
                            hbox(self.spinner, self.status, None), self.grid_holder, None,
                            hbox(self.copy_button, None, close, self.run_button),
                            spacing=12, margins=(22, 20, 22, 18)))

    def run(self) -> None:
        self.run_button.setEnabled(False)
        self.spinner.show()
        self.status.setText("Executando diagnóstico…")
        settings = self._ctx.settings.settings
        run_in_background(lambda: run_diagnostics(settings, self._ctx.provider, self._ctx.db),
                          self._show, self._failed)

    def _show(self, items: list[DiagnosticItem]) -> None:
        self._items = items
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        palette = theme.palette()
        colors = {Level.OK: palette.success, Level.WARNING: palette.warning, Level.ERROR: palette.error}
        names = {Level.OK: "SuccessText", Level.WARNING: "WarningText", Level.ERROR: "ErrorText"}
        glyphs = {Level.OK: "check-circle", Level.WARNING: "alert-triangle", Level.ERROR: "x-circle"}
        for row, item in enumerate(items):
            icon = make_label()
            icon.setPixmap(icons.pixmap(glyphs[item.level], colors[item.level], 16))
            self.grid.addWidget(make_label(item.label), row, 0)
            value = make_label(item.value, "Secondary", wrap=True, selectable=True)
            self.grid.addWidget(value, row, 1)
            self.grid.addLayout(hbox(icon, make_label(_SYMBOLS[item.level].split(" ", 1)[1], names[item.level])), row, 2)
        errors = sum(1 for i in items if i.level == Level.ERROR)
        warnings = sum(1 for i in items if i.level == Level.WARNING)
        self.status.setText(f"Concluído: {errors} erro(s), {warnings} aviso(s).")
        self._done()

    def _failed(self, _: BaseException) -> None:
        self.status.setText("Não foi possível concluir o diagnóstico. Consulte os logs para mais detalhes.")
        self._done()

    def _done(self) -> None:
        self.spinner.hide()
        self.run_button.setEnabled(True)
        self.copy_button.setEnabled(bool(self._items))

    def _copy(self) -> None:
        report = "\n".join(f"[{_SYMBOLS[i.level]}] {i.label}: {i.value}" for i in self._items)
        QGuiApplication.clipboard().setText(report)
        self.status.setText("Relatório copiado para a área de transferência.")
