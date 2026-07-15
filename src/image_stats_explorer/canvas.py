"""Scrollable image canvas with an editable pixel-coordinate bbox."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget


class ImageCanvas(QWidget):
    """Render an image, a context overlay, and an editable bbox."""

    bbox_changed = Signal(tuple)
    zoom_requested = Signal(float)
    pan_requested = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._overlay = QImage()
        self._overlay_opacity = 0.58
        self._bbox: tuple[int, int, int, int] | None = None
        self._context_bounds: tuple[int, int, int, int] | None = None
        self._scale = 1.0
        self._drag_mode: str | None = None
        self._drag_origin = QPointF()
        self._start_bbox: tuple[int, int, int, int] | None = None
        self._pan_origin = QPointF()
        self.setMouseTracking(True)

    @property
    def image_size(self) -> tuple[int, int]:
        return self._pixmap.width(), self._pixmap.height()

    @property
    def bbox(self) -> tuple[int, int, int, int] | None:
        return self._bbox

    def set_image(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self._overlay = QImage()
        self._overlay_opacity = 0.58
        self._bbox = None
        self._context_bounds = None
        self._drag_mode = None
        self.unsetCursor()
        self._update_canvas_size()
        self.update()

    def set_bbox(
        self, bbox: tuple[int, int, int, int] | None, emit: bool = False
    ) -> None:
        if bbox is None:
            if self._bbox is None:
                return
            self._bbox = None
            self.update()
            return
        width, height = self.image_size
        x, y, bbox_width, bbox_height = bbox
        x = max(0, min(width - 1, int(x))) if width else 0
        y = max(0, min(height - 1, int(y))) if height else 0
        bbox_width = max(1, min(width - x, int(bbox_width))) if width else 0
        bbox_height = max(1, min(height - y, int(bbox_height))) if height else 0
        updated = (x, y, bbox_width, bbox_height)
        if updated == self._bbox:
            return
        self._bbox = updated
        self.update()
        if emit:
            self.bbox_changed.emit(updated)

    def set_context_bounds(self, bounds: tuple[int, int, int, int] | None) -> None:
        self._context_bounds = bounds
        self.update()

    def set_overlay_image(
        self,
        overlay: QImage | None,
        opacity: float = 1.0,
    ) -> None:
        """Set one image whose pixel size must match the current context."""

        candidate = overlay if overlay is not None else QImage()
        candidate_size = (candidate.width(), candidate.height())
        if self._context_bounds is None and not candidate.isNull():
            raise ValueError("an overlay requires context bounds")
        if not candidate.isNull() and candidate_size != self._context_bounds[2:]:
            raise ValueError("overlay dimensions must match the context")
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

    def _display_rect(self, bounds: tuple[int, int, int, int]) -> QRectF:
        x, y, width, height = bounds
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
        context_rect = (
            self._display_rect(self._context_bounds)
            if self._context_bounds is not None
            else None
        )
        bbox_rect = self._display_rect(self._bbox) if self._bbox else None
        if not self._overlay.isNull() and context_rect is not None:
            painter.setOpacity(self._overlay_opacity)
            painter.drawImage(context_rect, self._overlay)
            painter.setOpacity(1.0)
        if self._context_bounds is not None:
            pen = QPen(QColor(0, 190, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._display_rect(self._context_bounds))
        if bbox_rect is None:
            return
        painter.setPen(QPen(QColor(255, 80, 35), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(bbox_rect)
        painter.setBrush(QColor(255, 255, 255))
        for point in self._handle_points(bbox_rect).values():
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
        if self._bbox is None:
            return "new"
        rect = self._display_rect(self._bbox)
        for name, point in self._handle_points(rect).items():
            if (
                abs(position.x() - point.x()) <= 8
                and abs(position.y() - point.y()) <= 8
            ):
                return name
        if rect.contains(position):
            return "move"
        return "new"

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pixmap.isNull():
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._drag_mode = "pan"
            self._pan_origin = event.globalPosition()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_mode = self._hit_mode(event.position())
        self._drag_origin = event.position() / self._scale
        self._start_bbox = self._bbox
        if self._drag_mode == "new":
            x = round(self._drag_origin.x())
            y = round(self._drag_origin.y())
            self.set_bbox((x, y, 1, 1), emit=True)
            self._start_bbox = self._bbox

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_mode is None:
            return
        if self._drag_mode == "pan":
            current = event.globalPosition()
            delta = current - self._pan_origin
            self._pan_origin = current
            self.pan_requested.emit(delta.x(), delta.y())
            event.accept()
            return
        if self._start_bbox is None:
            return
        current = event.position() / self._scale
        sx, sy, sw, sh = self._start_bbox
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
        self.set_bbox((left, top, right - left, bottom - top), emit=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_mode == "pan":
            self.unsetCursor()
            event.accept()
        self._drag_mode = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.zoom_requested.emit(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()
