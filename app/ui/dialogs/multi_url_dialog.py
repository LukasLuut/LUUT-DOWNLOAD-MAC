"""Add several links at once: validate, analyse and queue each one independently."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QPlainTextEdit, QScrollArea, QFrame, QWidget

from app.core.errors import FRIENDLY_MESSAGES, ErrorKind, to_app_error
from app.core.models.download import DownloadRequest
from app.core.models.video import BEST_QUALITY, CONTAINER_AUDIO, CONTAINER_MP4, CONTAINER_ORIGINAL
from app.core.services.analyzer import AnalysisResult
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.dialogs.base import ThemedDialog
from app.ui.widgets.animated import Spinner
from app.ui.widgets.common import ElidedLabel, hbox, icon_button, make_button, make_label, vbox
from app.ui.workers import run_in_background
from app.utils.urls import ParsedLine, parse_url_lines

MAX_PARALLEL_ANALYSES = 3


class _ResultRow(QWidget):
    def __init__(self, line: ParsedLine) -> None:
        super().__init__()
        self.setObjectName("Transparent")
        self.icon = make_label()
        self.icon.setFixedWidth(22)
        self.spinner = Spinner(18)
        self.spinner.hide()
        self.text = ElidedLabel(f"Linha {line.line_number}: {line.raw}", "Secondary")
        self.status = make_label("Aguardando", "Muted")
        self.setLayout(hbox(self.icon, self.spinner, self.text, self.status, spacing=10))
        self.layout().setStretch(2, 1)

    def set_state(self, state: str, message: str) -> None:
        palette = theme.palette()
        self.spinner.setVisible(state == "busy")
        self.icon.setVisible(state != "busy")
        icon, color, name = {
            "ok": ("check-circle", palette.success, "SuccessText"),
            "error": ("x-circle", palette.error, "ErrorText"),
            "wait": ("clock", palette.text_muted, "Muted"),
        }.get(state, ("clock", palette.text_muted, "Muted"))
        self.icon.setPixmap(icons.pixmap(icon, color, 18))
        self.status.setText(message)
        self.status.setObjectName(name)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class MultiUrlDialog(ThemedDialog):
    def __init__(self, parent: QWidget | None, context: AppContext, text: str = "") -> None:
        super().__init__(parent, "Adicionar vários links")
        self._ctx = context
        self.resize(760, 620)
        self._pending: list[tuple[ParsedLine, _ResultRow]] = []
        self._running = 0
        self._added = 0
        self._failed = 0

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("https://...\nhttps://...\nhttps://...\n\nUm link por linha.")
        self.editor.setAccessibleName("Links, um por linha")
        self.editor.setPlainText(text)
        import_button = make_button("Importar .txt", None, "file-import")
        import_button.clicked.connect(self._import)

        self.container = QComboBox()
        self.container.addItem("MP4 (quando disponível)", CONTAINER_MP4)
        self.container.addItem("Formato original", CONTAINER_ORIGINAL)
        self.container.addItem("Somente áudio (MP3)", CONTAINER_AUDIO)
        self.container.setAccessibleName("Formato")
        self.destination = QLineEdit(context.settings.settings.effective_download_dir)
        self.destination.setAccessibleName("Pasta de destino")
        browse = icon_button("folder", "Escolher pasta de destino")
        browse.clicked.connect(self._browse)

        self.results = vbox(spacing=6)
        results_widget = QWidget()
        results_widget.setObjectName("Transparent")
        results_widget.setLayout(vbox(self.results, None))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(results_widget)
        self.scroll.hide()

        self.summary = make_label("", "Secondary")
        self.start_button = make_button("Analisar e adicionar à fila", "primary", "download")
        self.start_button.clicked.connect(self._start)
        self.close_button = make_button("Fechar")
        self.close_button.clicked.connect(self.accept)

        self.setLayout(vbox(
            make_label("Adicionar vários links", "SectionTitle"),
            make_label("Cole um link por linha. Links inválidos são identificados individualmente e não "
                       "interrompem os demais. Cada item é baixado na melhor qualidade disponível.", "Muted", wrap=True),
            self.editor,
            hbox(make_label("FORMATO", "FieldLabel"), self.container, 16, make_label("DESTINO", "FieldLabel"),
                 self.destination, browse, spacing=8),
            self.scroll,
            hbox(import_button, self.summary, None, self.close_button, self.start_button, spacing=10),
            spacing=12, margins=(22, 20, 22, 18)))
        self.layout().setStretch(2, 1)
        self.layout().setStretch(4, 1)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar lista de links", "", "Arquivos de texto (*.txt)")
        if path:
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as handle:
                    self.editor.setPlainText(handle.read(1_000_000))
            except OSError:
                self._ctx.notify("Não foi possível ler o arquivo", path, "error")

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Escolher pasta de destino", self.destination.text())
        if folder:
            self.destination.setText(str(Path(folder)))

    def _start(self) -> None:
        lines = parse_url_lines(self.editor.toPlainText())
        destination = self.destination.text().strip()
        if not lines:
            self.summary.setText("Cole pelo menos um link.")
            return
        if not destination or not Path(destination).is_absolute():
            self.summary.setText("Escolha uma pasta de destino válida.")
            return
        self.start_button.setEnabled(False)
        self.editor.setReadOnly(True)
        self.scroll.show()
        self._ctx.settings.update(last_dir=destination)
        for line in lines:
            row = _ResultRow(line)
            self.results.addWidget(row)
            if line.is_valid:
                row.set_state("wait", "Aguardando")
                self._pending.append((line, row))
            else:
                row.set_state("error", FRIENDLY_MESSAGES[ErrorKind.INVALID_URL])
                self._failed += 1
        self._pump()

    def _pump(self) -> None:
        while self._pending and self._running < MAX_PARALLEL_ANALYSES:
            line, row = self._pending.pop(0)
            self._running += 1
            row.set_state("busy", "Analisando…")
            url = line.url or ""
            run_in_background(lambda u=url: self._ctx.analyzer.analyze(u),
                              lambda result, r=row: self._analysed(r, result),
                              lambda exc, r=row: self._analysis_failed(r, exc))
        self._update_summary()

    def _analysed(self, row: _ResultRow, result: AnalysisResult) -> None:
        self._running -= 1
        info = result.info
        preferred = str(self.container.currentData())
        container = preferred if preferred in info.containers else info.containers[0]
        self._ctx.downloads.add(DownloadRequest(
            url=info.url, output_dir=self.destination.text().strip(), title=info.title,
            quality_height=BEST_QUALITY, container=container, thumbnail_url=info.thumbnail_url,
            uploader=info.uploader, duration=info.duration, extractor=info.extractor,
            estimated_size=info.estimated_size_for(BEST_QUALITY, container)), result.thumbnail)
        self._added += 1
        row.set_state("ok", f"Adicionado: {info.title or info.url}")
        self._pump()

    def _analysis_failed(self, row: _ResultRow, exc: BaseException) -> None:
        self._running -= 1
        self._failed += 1
        row.set_state("error", to_app_error(exc).user_message)
        self._pump()

    def _update_summary(self) -> None:
        busy = self._running + len(self._pending)
        text = f"{self._added} adicionado(s)  •  {self._failed} com problema"
        if busy:
            text += f"  •  {busy} em análise"
        self.summary.setText(text)
        if not busy:
            self.close_button.setText("Concluir")
            self.close_button.setProperty("variant", "primary")
            self.close_button.style().unpolish(self.close_button)
            self.close_button.style().polish(self.close_button)
            self.start_button.hide()
