"""Pixel-exact image viewer (nearest-neighbour zoom on a checkerboard)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    q = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return q.copy()  # detach from the Python buffer


def load_qimage(path: str | Path) -> QImage | None:
    try:
        with Image.open(path) as img:
            img.load()
            return pil_to_qimage(img)
    except (OSError, ValueError):
        return None


class PixelImageView(QWidget):
    clicked = Signal()

    def __init__(self, parent=None, checker: bool = True, min_size: int = 160) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._placeholder = "No image"
        self.checker = checker
        self.fixed_zoom: int | None = None
        self.setMinimumSize(min_size, min_size)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:  # pragma: no cover - layout
        return QSize(256, 256)

    def set_image(self, image: QImage | Image.Image | str | Path | None, placeholder: str = "No image") -> None:
        if isinstance(image, (str, Path)):
            image = load_qimage(image)
        elif isinstance(image, Image.Image):
            image = pil_to_qimage(image)
        self._image = image
        self._placeholder = placeholder
        self.update()

    def image(self) -> QImage | None:
        return self._image

    def zoom(self) -> int:
        if self._image is None or self._image.isNull():
            return 1
        if self.fixed_zoom:
            return self.fixed_zoom
        z = min(self.width() / self._image.width(), self.height() / self._image.height())
        return max(1, int(z)) if z >= 1 else 1

    def mousePressEvent(self, event) -> None:  # pragma: no cover - interaction
        self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # pragma: no cover - painting
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#101018"))
        if self._image is None or self._image.isNull():
            p.setPen(QColor("#6a6a80"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return
        img = self._image
        z = min(self.width() / img.width(), self.height() / img.height())
        if z >= 1:
            z = float(self.fixed_zoom or max(1, int(z)))
        w, h = img.width() * z, img.height() * z
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        target = QRectF(x, y, w, h)
        if self.checker:
            size = 8
            light, dark = QColor("#2a2a36"), QColor("#22222c")
            p.save()
            p.setClipRect(target)
            for yy in range(int(y), int(y + h) + size, size):
                for xx in range(int(x), int(x + w) + size, size):
                    p.fillRect(xx, yy, size, size, light if ((xx - int(x)) // size + (yy - int(y)) // size) % 2 == 0 else dark)
            p.restore()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, z < 1)
        p.drawImage(target, img)
