"""Shows the metadata obtained from an analysed URL."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from app.core.models.video import VideoInfo
from app.ui.widgets.common import ElidedLabel, Thumbnail, make_label
from app.utils.formatters import NOT_AVAILABLE, format_bytes, format_duration, or_not_available

THUMB_WIDTH, THUMB_HEIGHT = 256, 144


class VideoInfoView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.thumbnail = Thumbnail(THUMB_WIDTH, THUMB_HEIGHT)
        self.title = make_label("", "CardTitle", wrap=True, selectable=True)
        self._values: dict[str, QLabel] = {}

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)
        grid.addWidget(self.thumbnail, 0, 0, 7, 1)
        grid.addWidget(self.title, 0, 1, 1, 2)
        fields = (("uploader", "Autor"), ("duration", "Duração"), ("quality", "Qualidade máxima"),
                  ("size", "Tamanho estimado"), ("formats", "Formatos disponíveis"), ("source", "Origem"))
        for row, (key, caption) in enumerate(fields, start=1):
            grid.addWidget(make_label(caption, "Muted"), row, 1)
            value = ElidedLabel("", "Secondary")
            self._values[key] = value
            grid.addWidget(value, row, 2)
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(7, 1)

    def show_info(self, info: VideoInfo, thumbnail: bytes | None) -> None:
        self.thumbnail.set_image(thumbnail)
        self.title.setText(info.title or NOT_AVAILABLE)
        values = {
            "uploader": or_not_available(info.uploader),
            "duration": format_duration(info.duration),
            "quality": f"{info.max_height}p" if info.max_height else NOT_AVAILABLE,
            "size": format_bytes(info.estimated_size),
            "formats": ", ".join(ext.upper() for ext in info.source_formats) or NOT_AVAILABLE,
            "source": or_not_available(info.extractor),
        }
        for key, text in values.items():
            self._values[key].set_full_text(text)
