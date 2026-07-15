"""PySide6 application for Image Stats Explorer."""

from __future__ import annotations

import os

from image_stats_protocol import (
    AnalysisParameters,
    AnalysisResult,
    NormalizedBBox,
    analyze_bbox,
)
from PIL import Image, ImageOps
from PySide6.QtCore import (
    QObject,
    QPoint,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from image_stats_explorer.canvas import ImageCanvas
from image_stats_explorer.rendering import (
    colorize,
    export_view,
    render_density_components,
    render_envelopes,
)

VIEW_TITLES = {
    "density": "边缘密度",
    "components": "连通域",
    "envelopes": "包络",
    "comparison": "左右对比",
}
VIEW_FILENAMES = {
    "density": "edge_density.png",
    "components": "edge_components.png",
    "envelopes": "envelopes.png",
    "comparison": "comparison.png",
}
MIN_SCALE = 0.05
MAX_SCALE = 16.0
PARAMETER_SPIN_WIDTH = 100

PARAMETER_INFO: dict[str, tuple[str, str, str, str]] = {
    "resize_size": (
        "缩放画布边长",
        "概念：方形 letterbox 画布的请求边长。",
        "计算：上下文边长至少为 resize_size；中心密度窗口按它计算。",
        "调整：增大会提高开销，并可能纳入更多上下文或保留更多细节。",
    ),
    "context_scale": (
        "上下文倍率",
        "概念：bbox 周围上下文相对 bbox 长边的放大倍数。",
        "计算：请求边长为 max(resize_size, ceil(context_scale × bbox 长边))。",
        "调整：增大可观察更远结构，但可能增加降采样；减小则更聚焦 bbox。",
    ),
    "center_fraction": (
        "中心密度窗口比例",
        "概念：bbox 中心附近 point_edge_density 的统计窗口比例。",
        "计算：窗口为 max(1, round(resize_size × center_fraction))。",
        "调整：增大更平滑宽泛；减小更局部但更敏感。",
    ),
    "gradient_threshold": (
        "密度边缘阈值",
        "概念：密度路径用于判定灰度边缘的阈值。",
        "计算：按水平、垂直前向灰度差的最大值判定边缘。",
        "调整：增大减少弱边缘；减小会引入更多纹理与噪声。",
    ),
    "density_low_threshold": (
        "密度低阈值",
        "概念：滞后连通域中的弱像素下限。",
        "计算：生成弱像素集合，再由高阈值提供强像素种子。",
        "调整：增大使区域收缩或分裂；减小使区域扩张或合并。",
    ),
    "density_high_threshold": (
        "密度高阈值",
        "概念：滞后连通域中用于确认区域的强像素阈值。",
        "计算：弱连通域必须包含强像素才能保留。",
        "调整：增大减少通过区域；减小放宽强种子要求。",
    ),
    "min_component_area": (
        "最小连通域面积",
        "概念：密度连通域保留所需的最小真实像素数。",
        "计算：按连通域中的有效连通像素数过滤。",
        "调整：增大排除小区域；减小保留更多小结构和噪声。",
    ),
    "min_grad": (
        "包络梯度阈值",
        "概念：包络路径生成候选梯度掩码的阈值。",
        "计算：生成独立于密度路径的包络梯度掩码。",
        "调整：增大减少候选；减小可能产生更多或更易合并的候选。",
    ),
    "min_ele_area": (
        "最小包络矩形面积",
        "概念：包络候选外接矩形的最小面积。",
        "计算：按外接矩形的宽乘高过滤候选。",
        "调整：增大排除小包络；减小保留更多小结构。",
    ),
    "envelope_max_side_ratio": (
        "包络最大边比例",
        "概念：包络候选最长边相对有效内容最长边的上限。",
        "计算：限制候选最长边与有效内容最长边的比例。",
        "调整：增大允许更大包络；减小过滤长条或页面级结构。",
    ),
}


def _qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(
        rgba.tobytes(),
        rgba.width,
        rgba.height,
        rgba.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()


class ParameterInfoIcon(QLabel):
    """Show a parameter explanation immediately when the pointer enters."""

    def __init__(self, parameter_name: str, tooltip: str) -> None:
        super().__init__()
        self._tooltip = tooltip
        self.setObjectName(f"{parameter_name}_info")
        self.setAccessibleName(f"{parameter_name} 参数说明")
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        )
        self.setPixmap(icon.pixmap(QSize(16, 16)))

    def enterEvent(self, event) -> None:  # noqa: N802
        del event
        QToolTip.showText(
            self.mapToGlobal(QPoint(self.width() + 6, 0)),
            self._tooltip,
            self,
            self.rect(),
        )

    def leaveEvent(self, event) -> None:  # noqa: N802
        del event
        QToolTip.hideText()


class WorkerSignals(QObject):
    finished = Signal(object, int)
    failed = Signal(str, int)


class AnalysisWorker(QRunnable):
    def __init__(
        self,
        image: Image.Image,
        bbox: NormalizedBBox,
        parameters: AnalysisParameters,
        generation: int,
    ) -> None:
        super().__init__()
        self.image = image
        self.bbox = bbox
        self.parameters = parameters
        self.generation = generation
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = analyze_bbox(self.image, self.bbox, self.parameters)
        # GUI workers report failures back to the main thread instead of raising.
        except Exception as error:
            self.signals.failed.emit(str(error), self.generation)
            return
        self.signals.finished.emit(result, self.generation)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Image Stats Explorer")
        self.resize(1180, 760)
        self._image: Image.Image | None = None
        self._bbox: tuple[int, int, int, int] | None = None
        self._result: AnalysisResult | None = None
        self._busy = False
        self._generation = 0
        self._syncing_bbox = False
        self._syncing_scroll = False
        self._updating_parameters = False
        self._scale = 1.0
        self._thread_pool = QThreadPool.globalInstance()
        self._building_ui = True
        self._build_ui()
        self._building_ui = False
        self._sync_bbox_inputs(None)
        self._show_single()
        self._refresh_actions()

    def _build_ui(self) -> None:
        page = QWidget()
        layout = QHBoxLayout(page)

        self.left_canvas = ImageCanvas()
        self.right_canvas = ImageCanvas()
        self.left_scroll = self._canvas_scroll(self.left_canvas)
        self.right_scroll = self._canvas_scroll(self.right_canvas)
        layout.addWidget(self.left_scroll, 1)
        layout.addWidget(self.right_scroll, 1)
        self._connect_scroll_sync()

        panel = QWidget()
        controls = QVBoxLayout(panel)
        self._build_controls(controls)
        panel_scroll = QScrollArea()
        panel_scroll.setFixedWidth(330)
        panel_scroll.setWidget(panel)
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(panel_scroll)
        self.setCentralWidget(page)

    def _canvas_scroll(self, canvas: ImageCanvas) -> QScrollArea:
        canvas.bbox_changed.connect(self._bbox_from_canvas)
        canvas.zoom_requested.connect(self._zoom_by)
        scroll = QScrollArea()
        scroll.setWidget(canvas)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        canvas.pan_requested.connect(
            lambda dx, dy, target=scroll: self._pan_scroll(target, dx, dy)
        )
        return scroll

    @staticmethod
    def _pan_scroll(scroll: QScrollArea, dx: float, dy: float) -> None:
        horizontal = scroll.horizontalScrollBar()
        vertical = scroll.verticalScrollBar()
        horizontal.setValue(horizontal.value() - round(dx))
        vertical.setValue(vertical.value() - round(dy))

    def _connect_scroll_sync(self) -> None:
        left_h = self.left_scroll.horizontalScrollBar()
        left_v = self.left_scroll.verticalScrollBar()
        right_h = self.right_scroll.horizontalScrollBar()
        right_v = self.right_scroll.verticalScrollBar()
        left_h.valueChanged.connect(lambda value: self._sync_scroll(right_h, value))
        left_v.valueChanged.connect(lambda value: self._sync_scroll(right_v, value))
        right_h.valueChanged.connect(lambda value: self._sync_scroll(left_h, value))
        right_v.valueChanged.connect(lambda value: self._sync_scroll(left_v, value))

    def _sync_scroll(self, target_bar: QScrollBar, value: int) -> None:
        if self._syncing_scroll or self.right_scroll.isHidden():
            return
        self._syncing_scroll = True
        target_bar.setValue(value)
        self._syncing_scroll = False

    def _build_controls(self, controls: QVBoxLayout) -> None:
        open_button = QPushButton("打开图片")
        open_button.clicked.connect(self._open_image)
        controls.addWidget(open_button)
        hint = QLabel("左键框选/移动/缩放 bbox；右键拖拽平移；滚轮缩放")
        hint.setWordWrap(True)
        controls.addWidget(hint)

        self.bbox_inputs = self._bbox_controls(controls)
        defaults = AnalysisParameters()
        common_form = self._parameter_group(controls, "通用 / 上下文")
        self.resize_size_spin = self._int_parameter(
            common_form, "resize_size", 1, 4096, defaults.resize_size
        )
        self.context_scale_spin = self._float_parameter(
            common_form,
            "context_scale",
            1.001,
            100.0,
            defaults.context_scale,
            0.1,
        )

        density_form = self._parameter_group(controls, "边缘密度")
        self.center_fraction_spin = self._float_parameter(
            density_form,
            "center_fraction",
            0.001,
            1.0,
            defaults.center_fraction,
            0.001,
        )
        self.gradient_threshold_spin = self._float_parameter(
            density_form,
            "gradient_threshold",
            0.0,
            255.0,
            defaults.gradient_threshold,
            1.0,
        )

        components_form = self._parameter_group(controls, "连通域")
        self.density_low_spin = self._float_parameter(
            components_form,
            "density_low_threshold",
            0.0,
            1.0,
            defaults.density_low_threshold,
            0.01,
        )
        self.density_high_spin = self._float_parameter(
            components_form,
            "density_high_threshold",
            0.0,
            1.0,
            defaults.density_high_threshold,
            0.01,
        )
        self.min_component_area_spin = self._int_parameter(
            components_form,
            "min_component_area",
            1,
            1_000_000,
            defaults.min_component_area,
        )

        envelopes_form = self._parameter_group(controls, "包络")
        self.min_grad_spin = self._float_parameter(
            envelopes_form,
            "min_grad",
            0.0,
            255.0,
            defaults.min_grad,
            1.0,
        )
        self.min_ele_area_spin = self._int_parameter(
            envelopes_form,
            "min_ele_area",
            1,
            1_000_000,
            defaults.min_ele_area,
        )
        self.envelope_max_side_ratio_spin = self._float_parameter(
            envelopes_form,
            "envelope_max_side_ratio",
            0.001,
            1.0,
            defaults.envelope_max_side_ratio,
            0.01,
        )

        defaults_button = QPushButton("恢复默认值")
        defaults_button.clicked.connect(self._restore_defaults)
        controls.addWidget(defaults_button)
        self.compute_button = QPushButton("计算")
        self.compute_button.clicked.connect(self._calculate)
        controls.addWidget(self.compute_button)

        view_form = QFormLayout()
        self.view_combo = QComboBox()
        for view in ("density", "components", "envelopes", "comparison"):
            self.view_combo.addItem(VIEW_TITLES[view], view)
        self.view_combo.currentIndexChanged.connect(self._view_changed)
        view_form.addRow("视图", self.view_combo)
        controls.addLayout(view_form)

        self.save_button = QPushButton("保存 PNG")
        self.save_button.clicked.connect(self._save_view)
        controls.addWidget(self.save_button)
        self.status = QLabel("请先打开图片")
        self.status.setWordWrap(True)
        controls.addWidget(self.status)
        controls.addStretch(1)

    @staticmethod
    def _parameter_group(controls: QVBoxLayout, title: str) -> QFormLayout:
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        controls.addWidget(group)
        return form

    def _bbox_controls(self, controls: QVBoxLayout) -> list[QSpinBox]:
        form = QFormLayout()
        inputs: list[QSpinBox] = []
        for label in ("x", "y", "width", "height"):
            spin = QSpinBox()
            spin.setRange(0 if label in ("x", "y") else 1, 1_000_000)
            spin.valueChanged.connect(
                lambda _value, current=inputs: self._bbox_from_inputs(current)
            )
            inputs.append(spin)
            form.addRow(label, spin)
        controls.addLayout(form)
        return inputs

    def _int_parameter(
        self,
        form: QFormLayout,
        label: str,
        low: int,
        high: int,
        value: int,
    ) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setFixedWidth(PARAMETER_SPIN_WIDTH)
        spin.setValue(value)
        spin.valueChanged.connect(lambda _value: self._mark_stale())
        form.addRow(self._parameter_label(label), spin)
        return spin

    def _float_parameter(
        self,
        form: QFormLayout,
        label: str,
        low: float,
        high: float,
        value: float,
        step: float,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(3)
        spin.setFixedWidth(PARAMETER_SPIN_WIDTH)
        spin.setValue(value)
        spin.valueChanged.connect(lambda _value: self._mark_stale())
        form.addRow(self._parameter_label(label), spin)
        return spin

    @staticmethod
    def _parameter_label(name: str) -> QWidget:
        label_container = QWidget()
        label_layout = QHBoxLayout(label_container)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(4)
        name_label = QLabel(name)
        name_label.setWordWrap(False)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        label_layout.addWidget(name_label)
        label_layout.addWidget(ParameterInfoIcon(name, "\n".join(PARAMETER_INFO[name])))
        label_container.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        return label_container

    def _current_parameters(self) -> AnalysisParameters:
        return AnalysisParameters(
            resize_size=self.resize_size_spin.value(),
            context_scale=self.context_scale_spin.value(),
            center_fraction=self.center_fraction_spin.value(),
            gradient_threshold=self.gradient_threshold_spin.value(),
            density_low_threshold=self.density_low_spin.value(),
            density_high_threshold=self.density_high_spin.value(),
            min_component_area=self.min_component_area_spin.value(),
            min_grad=self.min_grad_spin.value(),
            min_ele_area=self.min_ele_area_spin.value(),
            envelope_max_side_ratio=self.envelope_max_side_ratio_spin.value(),
        )

    def _parameter_error(self) -> str | None:
        try:
            self._current_parameters()
        except ValueError as error:
            return str(error)
        return None

    def _open_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "打开图片",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)",
        )
        if not filename:
            return
        try:
            with Image.open(filename) as source:
                self._image = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "无法打开图片", str(error))
            return
        qimage = _qimage(self._image)
        self.left_canvas.set_image(qimage)
        self.right_canvas.set_image(qimage)
        self._bbox = None
        self._sync_bbox_inputs(None)
        self._invalidate("请左键拖拽框选 bbox")
        self._fit_loaded_image()

    def _fit_loaded_image(self) -> None:
        width, height = self.left_canvas.image_size
        if not width or not height:
            return
        viewport = self.left_scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        scale = min(viewport.width() / width, viewport.height() / height)
        self._set_scale(scale)

    def _set_scale(self, scale: float) -> None:
        self._scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        self.left_canvas.set_scale(self._scale)
        self.right_canvas.set_scale(self._scale)

    def _zoom_by(self, factor: float) -> None:
        self._set_scale(self._scale * factor)

    def _sync_bbox_inputs(self, bbox: tuple[int, int, int, int] | None) -> None:
        self._syncing_bbox = True
        for spin, value in zip(self.bbox_inputs, bbox or (0, 0, 1, 1), strict=True):
            spin.setValue(value)
            spin.setEnabled(bbox is not None)
        self._syncing_bbox = False

    def _bbox_from_canvas(self, bbox: tuple[int, int, int, int]) -> None:
        self._set_shared_bbox(bbox)

    def _bbox_from_inputs(self, inputs: list[QSpinBox]) -> None:
        if self._syncing_bbox or self._image is None or len(inputs) != 4:
            return
        self._set_shared_bbox(tuple(spin.value() for spin in inputs))

    def _set_shared_bbox(self, bbox: tuple[int, int, int, int]) -> None:
        if self._image is None:
            return
        self.left_canvas.set_bbox(bbox, emit=False)
        updated = self.left_canvas.bbox
        self.right_canvas.set_bbox(updated, emit=False)
        self._sync_bbox_inputs(updated)
        if updated == self._bbox:
            return
        self._bbox = updated
        self._invalidate("bbox 已改变；结果已过期，请重新计算")

    def _restore_defaults(self) -> None:
        defaults = AnalysisParameters()
        self._updating_parameters = True
        self.resize_size_spin.setValue(defaults.resize_size)
        self.context_scale_spin.setValue(defaults.context_scale)
        self.center_fraction_spin.setValue(defaults.center_fraction)
        self.gradient_threshold_spin.setValue(defaults.gradient_threshold)
        self.density_low_spin.setValue(defaults.density_low_threshold)
        self.density_high_spin.setValue(defaults.density_high_threshold)
        self.min_component_area_spin.setValue(defaults.min_component_area)
        self.min_grad_spin.setValue(defaults.min_grad)
        self.min_ele_area_spin.setValue(defaults.min_ele_area)
        self.envelope_max_side_ratio_spin.setValue(defaults.envelope_max_side_ratio)
        self._updating_parameters = False
        self._mark_stale()

    def _mark_stale(self) -> None:
        if self._building_ui or self._updating_parameters:
            return
        error = self._parameter_error()
        if error is not None:
            self._invalidate(f"参数无效：{error}")
        else:
            self._invalidate("参数已改变；结果已过期，请重新计算")

    def _invalidate(self, message: str) -> None:
        self._generation += 1
        self._result = None
        self.left_canvas.set_overlay_image(None)
        self.right_canvas.set_overlay_image(None)
        self.left_canvas.set_context_bounds(None)
        self.right_canvas.set_context_bounds(None)
        self.status.setText(message)
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        has_bbox = self._image is not None and self._bbox is not None
        self.compute_button.setEnabled(
            has_bbox and self._parameter_error() is None and not self._busy
        )
        self.save_button.setEnabled(self._result is not None and not self._busy)

    def _calculate(self) -> None:
        if self._image is None or self._bbox is None or self._busy:
            return
        try:
            parameters = self._current_parameters()
            bbox = NormalizedBBox.from_pixel_xywh(
                *self._bbox,
                self._image.width,
                self._image.height,
            )
        except ValueError as error:
            self.status.setText(f"参数或选区无效：{error}")
            self._refresh_actions()
            return
        self._busy = True
        generation = self._generation
        self.status.setText("正在计算…")
        self._refresh_actions()
        worker = AnalysisWorker(
            self._image,
            bbox,
            parameters,
            generation,
        )
        worker.signals.finished.connect(self._calculation_finished)
        worker.signals.failed.connect(self._calculation_failed)
        self._thread_pool.start(worker)

    def _calculation_finished(self, result: AnalysisResult, generation: int) -> None:
        self._busy = False
        if generation != self._generation:
            self._refresh_actions()
            return
        self._result = result
        context = result.context_bounds
        bounds = (context.left, context.top, context.width, context.height)
        self.left_canvas.set_context_bounds(bounds)
        self.right_canvas.set_context_bounds(bounds)
        self._render_view()
        self.status.setText(self._result_status())
        self._refresh_actions()

    def _calculation_failed(self, message: str, generation: int) -> None:
        self._busy = False
        if generation != self._generation:
            self._refresh_actions()
            return
        self.status.setText(f"计算失败：{message}")
        self._refresh_actions()

    def _result_status(self) -> str:
        if self._result is None:
            return ""
        result = self._result
        density = float(
            result.density_map[result.center_point.y, result.center_point.x]
        )
        component_hit = "是" if result.component_hit else "否"
        envelope_hit = "是" if result.envelope_hit else "否"
        return (
            f"point_edge_density={density:.3f}；component_hit={component_hit}；"
            f"envelope_hit={envelope_hit}；上下文区域：连通域 "
            f"{len(result.component_regions)}、包络 {len(result.envelope_regions)}；"
            f"协议 {result.protocol_version}"
        )

    def _view_changed(self) -> None:
        if self._building_ui:
            return
        self._render_view()
        self._refresh_actions()

    def _show_single(self) -> None:
        self.right_scroll.hide()

    def _show_comparison(self) -> None:
        self.right_scroll.show()

    def _render_view(self) -> None:
        view = self.view_combo.currentData()
        if view == "comparison":
            self._show_comparison()
        else:
            self._show_single()
        if self._image is None or self._result is None:
            self.left_canvas.set_overlay_image(None)
            self.right_canvas.set_overlay_image(None)
            return
        if view == "density":
            self.left_canvas.set_overlay_image(_qimage(colorize(self._result)), 0.58)
        elif view == "components":
            self.left_canvas.set_overlay_image(
                _qimage(render_density_components(self._result))
            )
        elif view == "envelopes":
            self.left_canvas.set_overlay_image(_qimage(render_envelopes(self._result)))
        else:
            self.left_canvas.set_overlay_image(
                _qimage(render_density_components(self._result))
            )
            self.right_canvas.set_overlay_image(_qimage(render_envelopes(self._result)))

    def _save_view(self) -> None:
        if self._image is None or self._result is None:
            return
        view = self.view_combo.currentData()
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存 PNG", VIEW_FILENAMES[view], "PNG (*.png)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".png"):
            filename += ".png"
        try:
            export_view(self._image, self._result, view, filename)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self.status.setText(f"已保存：{filename}；当前视图：{VIEW_TITLES[view]}")


def run_application() -> None:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    application = QApplication([])
    window = MainWindow()
    window.show()
    application.exec()


if __name__ == "__main__":
    run_application()
