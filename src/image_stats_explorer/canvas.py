"""Scrollable image canvas with an editable pixel-coordinate selection."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class ImageCanvas(QWidget):
    """Render an image, one ROI overlay, and an editable eight-handle ROI."""

    roi_changed = Signal(tuple)
    zoom_requested = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._overlay = QImage()
        self._overlay_opacity = 0.58
        self._roi = (0, 0, 0, 0)
        self._scale = 1.0
        self._drag_mode: str | None = None
        self._drag_origin = QPointF()
        self._start_roi = self._roi
        self.setMouseTracking(True)

    @property
    def image_size(self) -> tuple[int, int]:
        return self._pixmap.width(), self._pixmap.height()

    @property
    def roi(self) -> tuple[int, int, int, int]:
        return self._roi

    def set_image(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self._overlay = QImage()
        self._overlay_opacity = 0.58
        self._roi = (0, 0, image.width(), image.height())
        self._update_canvas_size()
        self.update()

    def set_roi(self, roi: tuple[int, int, int, int], emit: bool = False) -> None:
        width, height = self.image_size
        x, y, roi_width, roi_height = roi
        x = max(0, min(width - 1, int(x))) if width else 0
        y = max(0, min(height - 1, int(y))) if height else 0
        roi_width = max(1, min(width - x, int(roi_width))) if width else 0
        roi_height = max(1, min(height - y, int(roi_height))) if height else 0
        updated = (x, y, roi_width, roi_height)
        if updated == self._roi:
            return
        self._roi = updated
        self.update()
        if emit:
            self.roi_changed.emit(updated)

    def set_overlay_image(
        self,
        overlay: QImage | None,
        opacity: float = 1.0,
    ) -> None:
        """Set one image whose pixel size must match the current ROI."""

        candidate = overlay if overlay is not None else QImage()
        candidate_size = (candidate.width(), candidate.height())
        if not candidate.isNull() and candidate_size != self._roi[2:]:
            raise ValueError("overlay dimensions must match the ROI")
        self._overlay = candidate
        self._overlay_opacity = opacity
        self.update()

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.05, min(16.0, scale))
        self._update_canvas_size()
        self.update()

    def _update_canvas_size(self) -> None:
        self.setFixedSize(
            round(self._pixmap.width() * self._scale),
            round(self._pixmap.height() * self._scale),
        )

    def _display_rect(self) -> QRectF:
        x, y, width, height = self._roi
        return QRectF(
            x * self._scale,
            y * self._scale,
            width * self._scale,
            height * self._scale,
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self._pixmap)
        roi_rect = self._display_rect()
        if not self._overlay.isNull():
            painter.setOpacity(self._overlay_opacity)
            painter.drawImage(roi_rect, self._overlay)
            painter.setOpacity(1.0)
        painter.setPen(QPen(QColor(255, 80, 35), 2))
        painter.drawRect(roi_rect)
        painter.setBrush(QColor(255, 255, 255))
        for point in self._handle_points(roi_rect).values():
            painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))

    @staticmethod
    def _handle_points(rect: QRectF) -> dict[str, QPointF]:
        return {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def _hit_mode(self, position: QPointF) -> str:
        for name, point in self._handle_points(self._display_rect()).items():
            if (
                abs(position.x() - point.x()) <= 8
                and abs(position.y() - point.y()) <= 8
            ):
                return name
        if self._display_rect().contains(position):
            return "move"
        return "new"

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap.isNull():
            return
        self._drag_mode = (
            "new"
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            else self._hit_mode(event.position())
        )
        self._drag_origin = event.position() / self._scale
        self._start_roi = self._roi
        if self._drag_mode == "new":
            x = round(self._drag_origin.x())
            y = round(self._drag_origin.y())
            self.set_roi((x, y, 1, 1), emit=True)
            self._start_roi = self._roi

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_mode is None:
            return
        current = event.position() / self._scale
        sx, sy, sw, sh = self._start_roi
        dx = round(current.x() - self._drag_origin.x())
        dy = round(current.y() - self._drag_origin.y())
        if self._drag_mode == "new":
            left = min(sx, round(current.x()))
            top = min(sy, round(current.y()))
            right = max(sx + 1, round(current.x()))
            bottom = max(sy + 1, round(current.y()))
        elif self._drag_mode == "move":
            width, height = self.image_size
            left = max(0, min(width - sw, sx + dx))
            top = max(0, min(height - sh, sy + dy))
            right, bottom = left + sw, top + sh
        else:
            left, top, right, bottom = sx, sy, sx + sw, sy + sh
            if "w" in self._drag_mode:
                left = min(right - 1, sx + dx)
            if "e" in self._drag_mode:
                right = max(left + 1, sx + sw + dx)
            if "n" in self._drag_mode:
                top = min(bottom - 1, sy + dy)
            if "s" in self._drag_mode:
                bottom = max(top + 1, sy + sh + dy)
        width, height = self.image_size
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        self.set_roi((left, top, right - left, bottom - top), emit=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        del event
        self._drag_mode = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_requested.emit(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)
