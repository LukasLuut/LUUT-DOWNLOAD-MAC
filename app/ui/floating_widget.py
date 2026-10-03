"""Floating download widget.

A second, compact window of the same process. It owns no download logic: analysis goes through the shared Analyzer,
downloads through the shared DownloadManager (one queue for the whole app), actions through TaskActions, keys through
the ShortcutManager and preferences through the SettingsManager. It reacts to the bridge's signals — nothing polls.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from app.core.errors import FRIENDLY_MESSAGES, ErrorKind, to_app_error
from app.core.models.download import DownloadStatus, DownloadTask, request_from_info
from app.core.models.settings import AppSettings
from app.core.models.video import KIND_AUDIO, KIND_LABELS, format_label
from app.core.services.analyzer import AnalysisResult
from app.infrastructure.logger import get_logger
from app.system.clipboard_monitor import read_clipboard_url
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.icons import to_qcolor
from app.ui.pages.downloads_page import ordered
from app.ui.shortcuts import ShortcutBinder
from app.ui.task_actions import TaskActions
from app.ui.widgets.animated import ProgressBar, Spinner
from app.ui.widgets.common import (
    ElidedLabel,
    Thumbnail,
    hbox,
    icon_button,
    make_button,
    make_label,
    refresh_button_icons,
    repolish,
    vbox,
)
from app.ui.workers import run_in_background
from app.utils.formatters import format_bytes, format_duration, format_eta, format_speed
from app.utils.urls import MAX_URL_LENGTH, normalize_url

log = get_logger("ui.widget")

WIDGET_WIDTH = 400
SHADOW = 14          # transparent margin around the glass panel, used for the soft shadow
EDGE = 16            # distance from the screen edges for the default corners
RADIUS = 14
QUEUE_RENDER_LIMIT = 60  # rows drawn in the compact list (the queue itself has no limit)

_FORM, _PROGRESS = range(2)


# ----------------------------------------------------------------------- positioning
def available_screens() -> list[QRect]:
    return [screen.availableGeometry() for screen in QGuiApplication.screens()]


def corner_position(corner: str, size: QSize, area: QRect) -> QPoint:
    left = area.left() + EDGE
    right = area.right() - size.width() - EDGE + 1
    top = area.top() + EDGE
    bottom = area.bottom() - size.height() - EDGE + 1
    return {"bottom_left": QPoint(left, bottom), "top_right": QPoint(right, top),
            "top_left": QPoint(left, top)}.get(corner, QPoint(right, bottom))


def is_reachable(rect: QRect, screens: list[QRect]) -> bool:
    """The header strip (used to drag the widget) must be mostly on some monitor."""
    header = QRect(rect.left(), rect.top(), rect.width(), min(44, rect.height()))
    visible = sum(header.intersected(s).width() * header.intersected(s).height() for s in screens)
    return visible >= header.width() * header.height() * 0.6


def clamp_to_screens(rect: QRect, screens: list[QRect]) -> QPoint:
    if not screens:
        return rect.topLeft()

    def overlap(screen: QRect) -> int:
        part = rect.intersected(screen)
        return part.width() * part.height() if part.isValid() else 0

    def distance(screen: QRect) -> int:
        return (screen.center() - rect.center()).manhattanLength()

    screen = max(screens, key=overlap) if any(overlap(s) for s in screens) else min(screens, key=distance)
    x = min(max(rect.left(), screen.left()), max(screen.left(), screen.right() - rect.width() + 1))
    y = min(max(rect.top(), screen.top()), max(screen.top(), screen.bottom() - rect.height() + 1))
    return QPoint(x, y)


def resolve_position(settings: AppSettings, size: QSize, screens: list[QRect], primary: QRect) -> QPoint:
    """Where to show the widget: the saved position when chosen and still on a monitor, else the chosen corner."""
    if settings.widget_position == "last" and settings.widget_has_position:
        rect = QRect(QPoint(settings.widget_x, settings.widget_y), size)
        if is_reachable(rect, screens):
            return clamp_to_screens(rect, screens)
        log.info("Saved widget position is off-screen; using a safe position")
    corner = settings.widget_position if settings.widget_position != "last" else "bottom_right"
    return corner_position(corner, size, primary)


# ---------------------------------------------------------------------------- pieces
def paint_glass(widget: QWidget, panel: QRectF, radius: float = RADIUS) -> None:
    """Smoked glass panel with a soft shadow, painted from the theme tokens."""
    palette = theme.palette()
    dark = palette.name == "dark"
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for step in range(SHADOW, 0, -2):  # soft layered shadow
        shadow = QColor(to_qcolor(palette.shadow))
        shadow.setAlpha(int((70 if dark else 34) * (1 - step / SHADOW) ** 2) + 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(panel.adjusted(-step, -step + 3, step, step + 3), radius + step, radius + step)
    path = QPainterPath()
    path.addRoundedRect(panel, radius, radius)
    base = QColor(to_qcolor(palette.surface))
    base.setAlpha(240 if dark else 246)
    painter.fillPath(path, base)
    gloss = QLinearGradient(panel.topLeft(), panel.bottomLeft())
    gloss.setColorAt(0.0, QColor(255, 255, 255, 20 if dark else 90))
    gloss.setColorAt(0.28, QColor(255, 255, 255, 0))
    painter.fillPath(path, gloss)
    tint = QColor(to_qcolor(palette.accent))
    tint.setAlpha(14 if dark else 8)
    painter.fillPath(path, tint)
    border = QColor(255, 255, 255, 30) if dark else QColor(to_qcolor(palette.border_strong))
    painter.setPen(QPen(border, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()


class _DragMixin:
    """Drag by any non-interactive area. Uses the native system move when available (multi-monitor/DPI aware)."""

    moved_by_user: Callable[[], None]

    def _drag_init(self) -> None:
        self._drag_offset: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._dragged = False
        self._save_timer = QTimer(self)  # type: ignore[arg-type]
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._drag_finished)
        self._user_moving = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()  # type: ignore[attr-defined]
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)  # type: ignore[misc]

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is None or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)  # type: ignore[misc]
            return
        position = event.globalPosition().toPoint()
        if not self._dragged and (position - (self._press_pos or position)).manhattanLength() < 5:
            return
        self._dragged = True
        self._user_moving = True
        handle = self.windowHandle()  # type: ignore[attr-defined]
        if handle is not None and handle.startSystemMove():
            self._drag_offset = None
            self._save_timer.start()
            return
        self.move(position - self._drag_offset)  # type: ignore[attr-defined]
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        was_drag = self._dragged
        self._drag_offset = None
        self._press_pos = None
        if was_drag:
            self._save_timer.start()
            event.accept()
            return
        self.clicked_without_drag()
        super().mouseReleaseEvent(event)  # type: ignore[misc]

    def moveEvent(self, event) -> None:  # noqa: N802
        if self._user_moving:
            self._save_timer.start()
        super().moveEvent(event)  # type: ignore[misc]

    def _drag_finished(self) -> None:
        if self._user_moving:
            self._user_moving = False
            self.moved_by_user()

    def clicked_without_drag(self) -> None:
        """Hook for a plain click."""


class QueueRow(QFrame):
    action_requested = Signal(str, str)  # action, task id
    selected = Signal(str)

    _GLYPHS = {DownloadStatus.DOWNLOADING: ("●", "accent"), DownloadStatus.PROCESSING: ("●", "accent"),
               DownloadStatus.QUEUED: ("○", "text_muted"), DownloadStatus.PAUSED: ("‖", "warning"),
               DownloadStatus.INTERRUPTED: ("‖", "warning"), DownloadStatus.COMPLETED: ("✓", "success"),
               DownloadStatus.FAILED: ("!", "error"), DownloadStatus.CANCELLED: ("×", "text_muted")}
    _BUTTONS = {
        "pause": ("pause", "Pausar"), "resume": ("play", "Retomar"), "cancel": ("x", "Cancelar"),
        "open_folder": ("folder-open", "Abrir pasta"), "open_file": ("external", "Abrir arquivo"),
        "copy_path": ("copy", "Copiar caminho"), "redownload": ("refresh", "Baixar novamente"),
        "forget": ("trash", "Remover do histórico"), "retry": ("rotate-ccw", "Tentar novamente"),
        "remove": ("trash", "Remover"), "restart": ("refresh", "Baixar novamente"),
    }

    def __init__(self, task: DownloadTask) -> None:
        super().__init__()
        self.setObjectName("QueueRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.task_id = task.id
        self.glyph = make_label()
        self.glyph.setFixedWidth(14)
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = ElidedLabel("", "WidgetTitleText")
        self.status = make_label("", "Muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._buttons: dict[str, QAbstractButton] = {}
        actions = hbox(spacing=2)
        actions.addSpacing(18)
        for action, (icon_name, tip) in self._BUTTONS.items():
            button = icon_button(icon_name, tip)
            button.setFixedSize(26, 26)
            button.setIconSize(QSize(15, 15))
            button.clicked.connect(lambda _=False, a=action: self.action_requested.emit(a, self.task_id))
            button.hide()
            self._buttons[action] = button
            actions.addWidget(button)
        actions.addStretch(1)
        top = hbox(self.glyph, self.title, self.status, spacing=6)
        top.setStretch(1, 1)
        self.setLayout(vbox(top, actions, spacing=2, margins=(8, 6, 8, 6)))
        self.update_task(task)

    @staticmethod
    def actions_for(task: DownloadTask) -> list[str]:
        status = task.status
        if task.stopping:
            return []
        if status == DownloadStatus.DOWNLOADING:
            return ["pause", "cancel", "open_folder"]
        if status == DownloadStatus.PROCESSING:
            return ["cancel", "open_folder"]
        if status == DownloadStatus.QUEUED:
            return ["pause", "cancel"]
        if status in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED):
            return ["resume", "cancel", "open_folder"]
        if status == DownloadStatus.COMPLETED:
            return ["open_file", "open_folder", "copy_path", "redownload", "forget"]
        if status == DownloadStatus.FAILED:
            return ["retry", "open_folder", "remove"]
        return ["restart", "remove"]

    @staticmethod
    def status_text(task: DownloadTask) -> str:
        progress = task.progress
        if task.stopping:
            return "Interrompendo…"
        if task.status == DownloadStatus.DOWNLOADING and progress.fraction is not None:
            return f"{progress.fraction * 100:.0f}%  {format_speed(progress.speed)}"
        if task.status == DownloadStatus.DOWNLOADING:
            return "Conectando…" if not progress.downloaded_bytes else format_bytes(progress.downloaded_bytes)
        return task.status.label

    def update_task(self, task: DownloadTask) -> None:
        glyph, tone = self._GLYPHS.get(task.status, ("○", "text_muted"))
        self.glyph.setText(glyph)
        self.glyph.setStyleSheet(f"color: {getattr(theme.palette(), tone)}; font-weight: 800;")
        self.title.set_full_text(Path(task.file_path).name if task.file_path else task.display_title)
        self.status.setText(self.status_text(task))
        visible = set(self.actions_for(task))
        for action, button in self._buttons.items():
            button.setVisible(action in visible)

    def update_progress(self, task: DownloadTask) -> None:
        self.status.setText(self.status_text(task))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.selected.emit(self.task_id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------- widget
class FloatingWidget(_DragMixin, QWidget):
    compact_requested = Signal()
    hide_requested = Signal()
    open_app_requested = Signal(str)  # page key
    geometry_saved = Signal(QPoint)

    def __init__(self, context: AppContext, actions: TaskActions) -> None:
        super().__init__(None)
        self._ctx = context
        self._actions = actions
        self._drag_init()
        self._result: AnalysisResult | None = None
        self._analyzed_url: str | None = None
        self._request_id = 0
        self._mode = _FORM
        self._tracked: str | None = None
        self._rows: dict[str, QueueRow] = {}
        self._selected: str | None = None
        self._banner_url = ""
        self._anchor_bottom = True
        self.setObjectName("FloatingWidget")
        self.setWindowTitle("Luut Download")
        self.setWindowIcon(icons.app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setFixedWidth(WIDGET_WIDTH + 2 * SHADOW)
        self.apply_window_flags(context.settings.settings.widget_always_on_top)
        self.setWindowOpacity(context.settings.settings.widget_opacity / 100)

        root = QVBoxLayout(self)
        root.setContentsMargins(SHADOW + 14, SHADOW + 10, SHADOW + 14, SHADOW + 10)
        root.setSpacing(10)
        root.addLayout(self._build_header())
        root.addWidget(self._build_banner())
        self.form = self._build_form()
        self.progress_view = self._build_progress()
        self.queue_view = self._build_queue()
        root.addWidget(self.form)
        root.addWidget(self.progress_view)
        root.addWidget(self.queue_view)
        powered = make_label("Powered by Luut", "WidgetPowered")
        powered.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(powered)
        self.progress_view.hide()
        self.queue_view.hide()

        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.setInterval(120)
        self._queue_timer.timeout.connect(self._refresh_queue)
        bridge = context.bridge
        for signal in (bridge.task_added, bridge.task_updated, bridge.task_finished, bridge.task_removed):
            signal.connect(self._on_task_changed)
        bridge.task_progress.connect(self._on_progress)

        self.binder = ShortcutBinder(context.shortcuts, "widget", self)
        self._bind_shortcuts()
        self.binder.apply()
        self._refresh_counts()

    # -------------------------------------------------------------- building
    def _build_header(self):
        logo = make_label()
        logo.setPixmap(icons.logo_pixmap(18))
        title = make_label("LUUT DOWNLOAD", "WidgetTitle")
        self.queue_button = make_button("Fila 0", None, "arrow-down")
        self.queue_button.setObjectName("QueueButton")
        self.queue_button.setCheckable(True)
        self.queue_button.setAccessibleName("Abrir fila de downloads")
        self.queue_button.toggled.connect(self.set_queue_open)
        self.open_app_button = icon_button("app-window", "Abrir aplicativo principal")
        self.open_app_button.clicked.connect(lambda: self.open_app_requested.emit("home"))
        self.compact_button = icon_button("minus", "Minimizar widget")
        self.compact_button.clicked.connect(self.compact_requested.emit)
        self.close_button = icon_button("x", "Ocultar widget")
        self.close_button.clicked.connect(self.hide_requested.emit)
        for button in (self.open_app_button, self.compact_button, self.close_button):
            button.setFixedSize(28, 28)
            button.setIconSize(QSize(16, 16))
        return hbox(logo, title, None, self.queue_button, self.open_app_button, self.compact_button,
                    self.close_button, spacing=6)

    def _build_banner(self) -> QFrame:
        self.banner = QFrame()
        self.banner.setObjectName("WidgetBanner")
        self.banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.banner_url = ElidedLabel("", "Secondary")
        analyze = make_button("Analisar", "primary", "search")
        analyze.clicked.connect(self._use_banner_url)
        ignore = make_button("Ignorar", "ghost")
        ignore.clicked.connect(self.dismiss_banner)
        self.banner.setLayout(vbox(make_label("Nova URL detectada", "WidgetSection"), self.banner_url,
                                   hbox(None, ignore, analyze, spacing=6), spacing=4, margins=(10, 8, 10, 8)))
        self.banner.hide()
        return self.banner

    def _build_form(self) -> QWidget:
        form = QWidget()
        form.setObjectName("Transparent")
        self.url_input = QLineEdit()
        self.url_input.setObjectName("UrlInput")
        self.url_input.setPlaceholderText("https://…")
        self.url_input.setMaxLength(MAX_URL_LENGTH)
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setAccessibleName("URL do vídeo")
        self.url_input.returnPressed.connect(self.confirm)
        self.url_input.textChanged.connect(self._on_url_changed)
        self.paste_button = make_button("Colar", None, "clipboard")
        self.paste_button.clicked.connect(self.paste)
        self.analyze_button = make_button("ANALISAR", "primary", "search")
        self.analyze_button.clicked.connect(self.analyze)
        self.analyze_caption = make_label("", "WidgetCaption")
        self.analyze_caption.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.status_row = QWidget()
        self.status_row.setObjectName("Transparent")
        self.spinner = Spinner(18)
        self.status_text = make_label("", "Secondary", wrap=True)
        self.status_row.setLayout(hbox(self.spinner, self.status_text, spacing=8))
        self.status_row.layout().setStretch(1, 1)
        self.status_row.hide()

        self.result = self._build_result()
        self.result.hide()
        form.setLayout(vbox(make_label("URL", "WidgetSection"), self.url_input,
                            hbox(self.paste_button, None, self.analyze_button, spacing=6),
                            self.analyze_caption, self.status_row, self.result, spacing=6))
        return form

    def _build_result(self) -> QWidget:
        box = QWidget()
        box.setObjectName("Transparent")
        self.thumbnail = Thumbnail(112, 63)
        self.title = make_label("", "WidgetTitleText", wrap=True)
        self.title.setMaximumHeight(40)
        self.uploader = ElidedLabel("", "Muted")
        self.duration = make_label("", "Muted")
        info = hbox(self.thumbnail, vbox(self.title, self.uploader, self.duration, None, spacing=2), spacing=10)
        info.setStretch(1, 1)

        self.quality = QComboBox()
        self.quality.setAccessibleName("Qualidade")
        self.kind = QComboBox()
        self.kind.setAccessibleName("Tipo")
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        self.container = QComboBox()
        self.container.setAccessibleName("Formato")
        for combo in (self.quality, self.kind, self.container):
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        grid.addWidget(make_label("QUALIDADE", "WidgetSection"), 0, 0, 1, 2)
        grid.addWidget(self.quality, 1, 0, 1, 2)
        grid.addWidget(make_label("TIPO", "WidgetSection"), 2, 0)
        grid.addWidget(make_label("FORMATO", "WidgetSection"), 2, 1)
        grid.addWidget(self.kind, 3, 0)
        grid.addWidget(self.container, 3, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.format_hint = make_label("", "WidgetCaption", wrap=True)

        self.destination = ElidedLabel("", "Secondary")
        change = make_button("Alterar", "ghost", "folder")
        change.clicked.connect(self.choose_destination)
        self.start_button = make_button("INICIAR DOWNLOAD", "primary", "download")
        self.start_button.setMinimumHeight(38)
        self.start_button.clicked.connect(self.start_download)
        self.start_caption = make_label("", "WidgetCaption")
        self.start_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        destination_row = hbox(self.destination, change, spacing=6)
        destination_row.setStretch(0, 1)
        box.setLayout(vbox(6, info, grid, self.format_hint, make_label("DESTINO", "WidgetSection"), destination_row,
                           4, self.start_button, self.start_caption, spacing=6))
        return box

    def _build_progress(self) -> QWidget:
        view = QWidget()
        view.setObjectName("Transparent")
        self.p_title = ElidedLabel("", "WidgetTitleText")
        self.p_state = make_label("", "Muted")
        self.p_bar = ProgressBar()
        self.p_percent = make_label("", "Secondary")
        self.p_size = make_label("", "Secondary")
        self.p_speed = make_label("", "Muted")
        self.p_eta = make_label("", "Muted")
        self.p_message = make_label("", "Muted", wrap=True)
        self._p_buttons: dict[str, QAbstractButton] = {}
        row = hbox(spacing=6)
        for action, label, icon_name, variant in (
                ("pause", "PAUSAR", "pause", None), ("resume", "RETOMAR", "play", "primary"),
                ("retry", "TENTAR NOVAMENTE", "rotate-ccw", "primary"), ("open_file", "ABRIR ARQUIVO", "external",
                                                                          "primary"),
                ("open_folder", "ABRIR PASTA", "folder-open", None), ("cancel", "CANCELAR", "x", "danger")):
            button = make_button(label, variant, icon_name)
            button.clicked.connect(lambda _=False, a=action: self._progress_action(a))
            button.hide()
            self._p_buttons[action] = button
            row.addWidget(button)
        new = make_button("Novo download", "link", "link")
        new.clicked.connect(self.show_form)
        self.new_button = new
        title_row = hbox(self.p_title, self.p_state, spacing=8)
        title_row.setStretch(0, 1)
        view.setLayout(vbox(title_row, self.p_bar,
                            hbox(self.p_percent, None, self.p_size, spacing=8),
                            hbox(self.p_speed, None, self.p_eta, spacing=8), self.p_message, 4, row,
                            hbox(None, new), spacing=6))
        self.p_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return view

    def _build_queue(self) -> QWidget:
        view = QWidget()
        view.setObjectName("Transparent")
        self.queue_list = QVBoxLayout()
        self.queue_list.setContentsMargins(0, 0, 4, 0)
        self.queue_list.setSpacing(2)
        holder = QWidget()
        holder.setObjectName("Transparent")
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self.queue_list)
        column.addStretch(1)
        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.queue_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_scroll.setWidget(holder)
        self.queue_scroll.setFixedHeight(250)
        self.queue_empty = make_label("Nenhum download na fila", "Muted")
        self.queue_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_more = make_button("", "link")
        self.queue_more.clicked.connect(lambda: self.open_app_requested.emit("downloads"))
        self.queue_more.hide()
        view.setLayout(vbox(make_label("FILA DE DOWNLOADS", "WidgetSection"), self.queue_empty, self.queue_scroll,
                            hbox(None, self.queue_more), spacing=6))
        return view

    # ------------------------------------------------------------- shortcuts
    def _bind_shortcuts(self) -> None:
        bind = self.binder.bind
        bind("widget.minimize", self.compact_requested.emit)
        bind("widget.focus_url", self.focus_url)
        bind("widget.paste_url", self.paste)
        bind("widget.clear_url", self.clear_url)
        bind("widget.analyze", self._shortcut_analyze)
        bind("widget.start_download", self._shortcut_start)
        bind("widget.pause_download", lambda: self._act_on_current("pause"))
        bind("widget.resume_download", lambda: self._act_on_current("resume"))
        bind("widget.cancel_download", lambda: self._act_on_current("cancel"))
        bind("widget.open_queue", lambda: self.queue_button.setChecked(not self.queue_button.isChecked()))
        bind("widget.next_item", lambda: self.move_selection(1))
        bind("widget.prev_item", lambda: self.move_selection(-1))
        bind("widget.pause_all", self._ctx.downloads.pause_all)
        bind("widget.resume_all", self._ctx.downloads.resume_all)
        bind("widget.open_app", lambda: self.open_app_requested.emit("home"))
        bind("widget.open_history", lambda: self.open_app_requested.emit("history"))
        bind("widget.open_folder", self._actions.open_download_folder)
        self.binder.hint(self.analyze_button, "widget.analyze", "Analisar URL", self.analyze_caption)
        self.binder.hint(self.start_button, "widget.start_download", "Iniciar download", self.start_caption)
        self.binder.hint(self.paste_button, "widget.paste_url", "Colar URL da área de transferência")
        self.binder.hint(self.queue_button, "widget.open_queue", "Abrir fila de downloads")
        self.binder.hint(self.compact_button, "widget.minimize", "Minimizar widget")
        self.binder.hint(self.open_app_button, "widget.open_app", "Abrir aplicativo principal")
        self.binder.hint(self.close_button, "widget.toggle", "Ocultar widget")

    def _shortcut_analyze(self) -> bool:
        """Shares Ctrl+Shift+Enter with "Iniciar download": analyses unless the current URL is already analysed."""
        if self._mode != _FORM:
            self.show_form()
        if self.analysis_ready():
            return False
        self.analyze()
        return True

    def _shortcut_start(self) -> bool:
        if not self.analysis_ready():
            return False
        self.start_download()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        focus = QApplication.focusWidget()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and isinstance(focus, QAbstractButton) \
                and self.isAncestorOf(focus):
            focus.click()  # Enter confirms the focused action
            return
        if event.key() == Qt.Key.Key_Escape:
            if self.banner.isVisible():
                self.dismiss_banner()
            elif self.queue_button.isChecked():
                self.queue_button.setChecked(False)
            else:
                self.hide_requested.emit()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ painting
    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW))

    def showEvent(self, event) -> None:  # noqa: N802
        # Progress is not redrawn while hidden (no work when idle): catch up now.
        self._refresh_counts()
        if self._tracked:
            task = self._ctx.downloads.get(self._tracked)
            if task:
                self._update_progress_view(task)
        if self.queue_view.isVisible():
            self._queue_timer.start()
        super().showEvent(event)

    def refresh_theme(self) -> None:
        refresh_button_icons(self)
        self._refresh_queue()
        self.update()

    def apply_window_flags(self, on_top: bool) -> None:
        visible = self.isVisible()
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if visible:
            self.show()

    # ------------------------------------------------------------ geometry
    def moved_by_user(self) -> None:
        self.geometry_saved.emit(self.pos())

    def _relayout(self) -> None:
        """Grow/shrink with the content, keeping the edge nearest to the screen border in place."""
        old = self.geometry()
        self.adjustSize()
        if not self.isVisible():
            return
        new = QRect(old.topLeft(), self.size())
        if self._anchor_bottom:
            new.moveBottom(old.bottom())
        self.move(clamp_to_screens(new, available_screens()))

    def update_anchor(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            self._anchor_bottom = self.geometry().center().y() > screen.availableGeometry().center().y()

    # --------------------------------------------------------------- form
    def focus_url(self) -> None:
        self.show_form()
        self.url_input.setFocus()
        self.url_input.selectAll()

    def clear_url(self) -> None:
        self.url_input.clear()
        self.url_input.setFocus()

    def paste(self) -> None:
        """Reads the current clipboard text only; never starts anything."""
        text, url = read_clipboard_url()
        self.show_form()
        if text:
            self.url_input.setText(url or text[:MAX_URL_LENGTH])
        self.url_input.setFocus()

    def set_url(self, url: str) -> None:
        self.show_form()
        self.url_input.setText(url)

    def analysis_ready(self) -> bool:
        return self._result is not None and self._analyzed_url == normalize_url(self.url_input.text())

    def _on_url_changed(self, _text: str) -> None:
        self.url_input.setProperty("invalid", False)
        repolish(self.url_input)
        if _text.strip() and self.status_row.isVisible() and not self.spinner.isVisible():
            self.status_row.hide()  # the "added to the queue" note makes way for the next link
            self._relayout()
        if self._result is not None and not self.analysis_ready():
            self._result = None
            self.result.hide()
            self._relayout()

    def confirm(self) -> None:
        """Enter in the URL field: analyse, or start the download when this URL is already analysed."""
        if self.analysis_ready():
            self.start_download()
        else:
            self.analyze()

    def analyze(self) -> None:
        url = normalize_url(self.url_input.text())
        if url is None:
            empty = not self.url_input.text().strip()
            self.url_input.setProperty("invalid", True)
            repolish(self.url_input)
            self._show_status("Cole um link para analisar." if empty else FRIENDLY_MESSAGES[ErrorKind.INVALID_URL],
                              error=True)
            return
        self._request_id += 1
        request_id = self._request_id
        self._result = None
        self.result.hide()
        self.analyze_button.setEnabled(False)
        self._show_status("Analisando…", busy=True)
        run_in_background(lambda: self._ctx.analyzer.analyze(url, source="widget"),
                          lambda result: self._analysis_done(request_id, url, result),
                          lambda exc: self._analysis_failed(request_id, exc))

    def _analysis_done(self, request_id: int, url: str, result: AnalysisResult) -> None:
        if request_id != self._request_id or not isValid(self):
            return
        self.analyze_button.setEnabled(True)
        self.status_row.hide()
        self._result = result
        self._analyzed_url = url
        self._populate(result)
        self.result.show()
        self._relayout()
        self.start_button.setFocus()

    def _analysis_failed(self, request_id: int, exc: BaseException) -> None:
        if request_id != self._request_id or not isValid(self):
            return
        self.analyze_button.setEnabled(True)
        self._show_status(to_app_error(exc).user_message, error=True)

    def _show_status(self, text: str, busy: bool = False, error: bool = False) -> None:
        self.spinner.setVisible(busy)
        self.status_text.setText(text)
        self.status_text.setObjectName("ErrorText" if error else "Secondary")
        repolish(self.status_text)
        self.status_row.show()
        self._relayout()

    def _populate(self, result: AnalysisResult) -> None:
        info = result.info
        self.thumbnail.set_image(result.thumbnail)
        self.title.setText(info.title or "Título não disponível")
        self.title.setToolTip(info.title or "")
        self.uploader.set_full_text(info.uploader or "Canal não informado")
        self.duration.setText(f"Duração: {format_duration(info.duration)}" if info.duration else "Duração: —")
        self.kind.blockSignals(True)
        self.kind.clear()
        for kind in info.kinds():
            self.kind.addItem(KIND_LABELS[kind], kind)
        self.kind.blockSignals(False)
        self.kind.setEnabled(self.kind.count() > 1)
        self.destination.set_full_text(self._ctx.settings.settings.effective_download_dir)
        self._on_kind_changed()

    def _on_kind_changed(self, _index: int = 0) -> None:
        if self._result is None:
            return
        info = self._result.info
        kind = self.kind.currentData()
        self.quality.clear()
        options = info.qualities_for(kind)
        if kind == KIND_AUDIO or not options:
            self.quality.addItem("Melhor áudio disponível" if kind == KIND_AUDIO else "Melhor disponível", 0)
            self.quality.setEnabled(False)
        else:
            for option in options:
                size = f"  ~{format_bytes(option.estimated_size)}" if option.estimated_size else ""
                self.quality.addItem(f"{option.label}{size}", option.height)
            self.quality.setEnabled(len(options) > 1)
        self.container.clear()
        for key in info.containers_for(kind):
            self.container.addItem(format_label(key, info), key)
        self.container.setEnabled(self.container.count() > 1)
        needs_ffmpeg = kind != KIND_AUDIO and info.containers_for(kind) and "mp4" not in info.source_formats
        self.format_hint.setText("MP4 é gerado com ffmpeg a partir do formato original." if needs_ffmpeg else "")
        self.format_hint.setVisible(bool(needs_ffmpeg))

    def choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Escolher pasta de destino", self.destination.full_text())
        if folder:
            self.destination.set_full_text(str(Path(folder)))

    def start_download(self) -> None:
        if self._result is None or not self.analysis_ready():
            if self.url_input.text().strip():
                self.analyze()
            return
        destination = self.destination.full_text().strip()
        if not destination or not Path(destination).is_absolute():
            self._show_status("Escolha uma pasta de destino válida.", error=True)
            return
        container = self.container.currentData()
        if not container:
            self._show_status("Nenhum formato disponível para este tipo.", error=True)
            return
        info = self._result.info
        request = request_from_info(info, destination, str(container), int(self.quality.currentData() or 0))
        task = self._ctx.downloads.add(request, self._result.thumbnail)
        self._ctx.settings.update(last_dir=destination)
        log.info("Download created from widget: %s", task.id)
        if self._ctx.settings.settings.widget_notifications:
            self._ctx.notify("Adicionado à fila", info.title or "Download adicionado", "success")
        self._result = None
        self._analyzed_url = None
        self.url_input.clear()
        self.result.hide()
        # Straight back to "new download": the next link can be pasted right away (progress lives in the queue).
        self._tracked = task.id  # pause/resume/cancel shortcuts still act on the download just added
        self.show_form()
        self._show_status(f"✓ Adicionado à fila: {info.title or 'download'}. Cole o próximo link.")
        self.url_input.setFocus()
        if self._ctx.settings.settings.widget_minimize_after_start:
            self.compact_requested.emit()

    # ----------------------------------------------------------- progress
    def track(self, task_id: str) -> None:
        self._tracked = task_id
        self._mode = _PROGRESS
        self.form.hide()
        self.progress_view.show()
        task = self._ctx.downloads.get(task_id)
        if task:
            self._update_progress_view(task)
        self._relayout()

    def show_form(self) -> None:
        if self._mode == _FORM:
            return
        self._mode = _FORM
        self.progress_view.hide()
        self.form.show()
        self._relayout()

    @property
    def tracked_task_id(self) -> str | None:
        return self._tracked

    @property
    def mode(self) -> str:
        return "progress" if self._mode == _PROGRESS else "form"

    def _update_progress_view(self, task: DownloadTask) -> None:
        progress = task.progress
        self.p_title.set_full_text(Path(task.file_path).name if task.file_path else task.display_title)
        self.p_state.setText(task.status.label)
        fraction = progress.fraction
        active = task.status.is_active
        self.p_bar.setVisible(task.status != DownloadStatus.CANCELLED)
        if task.status == DownloadStatus.PROCESSING or (active and fraction is None):
            self.p_bar.set_fraction(None)
        else:
            self.p_bar.set_fraction(1.0 if task.status == DownloadStatus.COMPLETED else (fraction or 0.0))
        self.p_bar.set_tone({DownloadStatus.PAUSED: "warning", DownloadStatus.INTERRUPTED: "warning",
                             DownloadStatus.FAILED: "error", DownloadStatus.QUEUED: "muted",
                             DownloadStatus.COMPLETED: "success"}.get(task.status, "accent"))
        self.p_percent.setText(f"{fraction * 100:.0f}%" if fraction is not None else "—")
        total = format_bytes(progress.total_bytes) if progress.total_bytes else "—"
        done = format_bytes(progress.downloaded_bytes) if progress.downloaded_bytes else "—"
        self.p_size.setText(f"{done} / {total}")
        downloading = task.status == DownloadStatus.DOWNLOADING
        self.p_speed.setText(f"Velocidade: {format_speed(progress.speed) if downloading else '—'}")
        self.p_eta.setText(f"ETA: {format_eta(progress.eta) if downloading else '—'}")
        message = ""
        if task.status == DownloadStatus.QUEUED:
            position = self._ctx.downloads.queue_position(task.id)
            message = f"Aguardando na fila — posição {position}" if position else "Aguardando na fila"
        elif task.status == DownloadStatus.FAILED:
            message = task.error_message or "O download falhou."
        elif task.status == DownloadStatus.COMPLETED:
            message = "Download concluído."
        elif task.stopping:
            message = "Interrompendo com segurança…"
        self.p_message.setText(message)
        self.p_message.setVisible(bool(message))
        visible = {
            DownloadStatus.DOWNLOADING: {"pause", "cancel"}, DownloadStatus.QUEUED: {"pause", "cancel"},
            DownloadStatus.PROCESSING: {"cancel"}, DownloadStatus.PAUSED: {"resume", "cancel"},
            DownloadStatus.INTERRUPTED: {"resume", "cancel"}, DownloadStatus.FAILED: {"retry", "open_folder"},
            DownloadStatus.COMPLETED: {"open_file", "open_folder"}, DownloadStatus.CANCELLED: {"retry"},
        }.get(task.status, set())
        if task.stopping:
            visible = set()
        for action, button in self._p_buttons.items():
            button.setVisible(action in visible)

    def _progress_action(self, action: str) -> None:
        if self._tracked:
            self._actions.run(action, self._tracked)

    def _act_on_current(self, action: str) -> bool:
        task_id = self._current_target()
        if task_id is None:
            return False
        self._actions.run(action, task_id)
        return True

    def _current_target(self) -> str | None:
        if self.queue_button.isChecked() and self._selected and self._ctx.downloads.get(self._selected):
            return self._selected
        if self._tracked and self._ctx.downloads.get(self._tracked):
            return self._tracked
        tasks = self._ctx.downloads.tasks()
        active = next((t for t in tasks if t.status.is_active), None)
        return active.id if active else None

    # -------------------------------------------------------------- queue
    def set_queue_open(self, opened: bool) -> None:
        if self.queue_button.isChecked() != opened:
            self.queue_button.setChecked(opened)
            return
        self.queue_view.setVisible(opened)
        if opened:
            self._refresh_queue()
        self._relayout()

    @property
    def queue_open(self) -> bool:
        return self.queue_view.isVisible()

    def _on_task_changed(self, task: DownloadTask) -> None:
        self._refresh_counts()
        if task.id == self._tracked:
            current = self._ctx.downloads.get(task.id)
            if current is None:
                self._tracked = None
                self.show_form()
            else:
                self._update_progress_view(current)
        if self.queue_view.isVisible():
            self._queue_timer.start()

    def _on_progress(self, task: DownloadTask) -> None:
        if not self.isVisible():
            return
        if task.id == self._tracked and self._mode == _PROGRESS:
            self._update_progress_view(task)
        row = self._rows.get(task.id)
        if row is not None:
            row.update_progress(task)

    def _refresh_counts(self) -> None:
        pending = sum(1 for t in self._ctx.downloads.tasks() if t.is_pending)
        self.queue_button.setText(f"Fila {pending}")

    def _refresh_queue(self) -> None:
        if not self.queue_view.isVisible():
            return
        tasks = ordered(self._ctx.downloads.tasks())
        shown = tasks[:QUEUE_RENDER_LIMIT]
        keep = {t.id for t in shown}
        for task_id in [i for i in self._rows if i not in keep]:
            self._rows.pop(task_id).deleteLater()
        while self.queue_list.count():
            self.queue_list.takeAt(0)
        for task in shown:
            row = self._rows.get(task.id)
            if row is None:
                row = QueueRow(task)
                row.action_requested.connect(self._actions.run)
                row.selected.connect(self.select)
                self._rows[task.id] = row
            else:
                row.update_task(task)
            row.set_selected(task.id == self._selected)
            self.queue_list.addWidget(row)
            row.show()
        self.queue_empty.setVisible(not tasks)
        self.queue_scroll.setVisible(bool(tasks))
        hidden = len(tasks) - len(shown)
        self.queue_more.setText(f"+{hidden} na fila — ver todos no aplicativo")
        self.queue_more.setVisible(hidden > 0)
        if self._selected not in self._rows:
            self._selected = None
        self._relayout()

    def select(self, task_id: str | None) -> None:
        if self._selected in self._rows:
            self._rows[self._selected].set_selected(False)
        self._selected = task_id
        if task_id in self._rows:
            self._rows[task_id].set_selected(True)
            self.queue_scroll.ensureWidgetVisible(self._rows[task_id])

    @property
    def selected_task_id(self) -> str | None:
        return self._selected

    def move_selection(self, step: int) -> bool:
        if not self.queue_view.isVisible():
            self.set_queue_open(True)
        ids = [self.queue_list.itemAt(i).widget().task_id for i in range(self.queue_list.count())]
        if not ids:
            return False
        index = ids.index(self._selected) + step if self._selected in ids else (0 if step > 0 else len(ids) - 1)
        self.select(ids[max(0, min(len(ids) - 1, index))])
        return True

    def row(self, task_id: str) -> QueueRow | None:
        return self._rows.get(task_id)

    # ------------------------------------------------------------- banner
    def offer_url(self, url: str) -> None:
        self._banner_url = url
        self.banner_url.set_full_text(url)
        self.banner.show()
        self._relayout()

    def dismiss_banner(self) -> None:
        self.banner.hide()
        self._banner_url = ""
        self._relayout()

    def _use_banner_url(self) -> None:
        url = self._banner_url
        self.dismiss_banner()
        if url:
            self.set_url(url)
            self.analyze()

    # ---------------------------------------------------------------- tour
    def tour_targets(self) -> dict[str, QWidget]:
        return {"panel": self, "url": self.url_input, "analyze": self.analyze_button, "queue": self.queue_button}


class CompactBubble(_DragMixin, QWidget):
    """The widget minimized to a small floating button: download icon + pending downloads."""

    expand_requested = Signal()
    geometry_saved = Signal(QPoint)

    def __init__(self, context: AppContext) -> None:
        super().__init__(None)
        self._ctx = context
        self._drag_init()
        self.setWindowTitle("Luut Download")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Expandir widget do Luut")
        self.apply_window_flags(context.settings.settings.widget_always_on_top)
        self.setWindowOpacity(context.settings.settings.widget_opacity / 100)
        self.icon = QLabel()
        self.count = make_label("0", "BubbleCount")
        layout = hbox(self.icon, self.count, spacing=6, margins=(SHADOW + 14, SHADOW + 8, SHADOW + 16, SHADOW + 8))
        self.setLayout(layout)
        self.refresh_theme()
        bridge = context.bridge
        for signal in (bridge.task_added, bridge.task_updated, bridge.task_finished, bridge.task_removed):
            signal.connect(self.refresh_count)
        self.refresh_count()
        self.binder = ShortcutBinder(context.shortcuts, "widget", self)
        self.binder.bind("widget.expand", self.expand_requested.emit)
        self.binder.apply()
        self.binder.hint(self, "widget.expand", "Expandir widget")

    def apply_window_flags(self, on_top: bool) -> None:
        visible = self.isVisible()
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if visible:
            self.show()

    def refresh_theme(self) -> None:
        self.icon.setPixmap(icons.pixmap("arrow-down", theme.palette().accent_hover, 20))
        self.update()

    def refresh_count(self, *_: object) -> None:
        count = self._ctx.downloads.pending_count()
        self.count.setText(str(count))
        self.adjustSize()

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW), radius=18)

    def clicked_without_drag(self) -> None:
        self.expand_requested.emit()

    def moved_by_user(self) -> None:
        self.geometry_saved.emit(self.pos())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.expand_requested.emit()
            return
        super().keyPressEvent(event)
