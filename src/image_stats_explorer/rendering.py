"""Pillow rendering and PNG export for image-stats-protocol results."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from image_stats_protocol import AnalysisResult, PixelBounds

DENSITY_VIEW = "density"
COMPONENTS_VIEW = "components"
ENVELOPES_VIEW = "envelopes"
COMPARISON_VIEW = "comparison"


def value_range() -> tuple[float, float]:
    """Return the fixed point-edge-density range."""

    return 0.0, 1.0


def _color(value: float) -> tuple[int, int, int, int]:
    """Map a normalized value to a blue-cyan-yellow-red palette."""

    stops = (
        (0.00, (32, 48, 160)),
        (0.35, (0, 190, 230)),
        (0.65, (245, 230, 45)),
        (1.00, (190, 25, 25)),
    )
    value = max(0.0, min(1.0, value))
    for (left, first), (right, second) in zip(stops, stops[1:], strict=True):
        if value <= right:
            ratio = (value - left) / (right - left)
            rgb = tuple(round(a + (b - a) * ratio) for a, b in zip(first, second))
            return *rgb, 255
    return *stops[-1][1], 255


def _content_values(result: AnalysisResult, values: np.ndarray) -> np.ndarray:
    """Extract only valid content from a protocol letterbox-canvas array."""

    content = result.content_bounds
    slices = np.s_[content.top : content.bottom, content.left : content.right]
    valid = result.valid_mask[slices]
    if valid.shape != (content.height, content.width) or not np.all(valid):
        raise ValueError("protocol valid_mask must cover content_bounds")
    return values[slices]


def _roi_size(result: AnalysisResult) -> tuple[int, int]:
    return result.pixel_bbox.width, result.pixel_bbox.height


def _context_to_roi(result: AnalysisResult, image: Image.Image) -> Image.Image:
    """Crop a restored original-resolution context to the selected bbox."""

    context_size = result.transform.source_size
    if image.size != context_size:
        raise ValueError("context image dimensions must match transform.source_size")
    context = result.context_bounds
    bbox = result.pixel_bbox
    crop = (
        bbox.left - context.left,
        bbox.top - context.top,
        bbox.right - context.left,
        bbox.bottom - context.top,
    )
    rendered = image.crop(crop)
    if rendered.size != _roi_size(result):
        raise ValueError("rendered overlay dimensions must match pixel_bbox")
    return rendered


def colorize(result: AnalysisResult) -> Image.Image:
    """Render valid density values back onto the selected original-image bbox."""

    low, high = value_range()
    scale = high - low
    densities = _content_values(result, result.density_map)
    rgba = np.asarray(
        [_color((float(value) - low) / scale) for value in densities.flat],
        dtype=np.uint8,
    ).reshape(*densities.shape, 4)
    content_image = Image.fromarray(rgba, mode="RGBA")
    context_size = result.transform.source_size
    if content_image.size != context_size:
        content_image = content_image.resize(context_size, Image.Resampling.BILINEAR)
    return _context_to_roi(result, content_image)


def _region_color(index: int) -> tuple[int, int, int, int]:
    """Return a stable, high-contrast color for a result index."""

    palette = (
        (230, 55, 55, 255),
        (20, 145, 230, 255),
        (35, 175, 95, 255),
        (240, 145, 20, 255),
        (155, 75, 210, 255),
        (15, 175, 175, 255),
    )
    return palette[index % len(palette)]


def _region_roi_bounds(
    result: AnalysisResult,
    bounds: PixelBounds,
) -> tuple[int, int, int, int] | None:
    """Map a canvas region through context coordinates and clip it to the ROI."""

    content = result.content_bounds
    if (
        bounds.left < content.left
        or bounds.top < content.top
        or bounds.right > content.right
        or bounds.bottom > content.bottom
    ):
        raise ValueError("protocol region must stay inside content_bounds")

    source_width, source_height = result.transform.source_size
    context = result.context_bounds
    left = context.left + math.floor(
        (bounds.left - content.left) * source_width / content.width
    )
    top = context.top + math.floor(
        (bounds.top - content.top) * source_height / content.height
    )
    right = context.left + math.ceil(
        (bounds.right - content.left) * source_width / content.width
    )
    bottom = context.top + math.ceil(
        (bounds.bottom - content.top) * source_height / content.height
    )

    bbox = result.pixel_bbox
    left = max(left, bbox.left)
    top = max(top, bbox.top)
    right = min(right, bbox.right)
    bottom = min(bottom, bbox.bottom)
    if right <= left or bottom <= top:
        return None
    return (
        left - bbox.left,
        top - bbox.top,
        right - bbox.left,
        bottom - bbox.top,
    )


def render_envelopes(result: AnalysisResult) -> Image.Image:
    """Render only the parts of context envelope regions inside the bbox."""

    overlay = Image.new("RGBA", _roi_size(result), (0, 0, 0, 0))
    fill = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    fill_draw = ImageDraw.Draw(fill)
    visible_regions: list[tuple[int, tuple[int, int, int, int]]] = []
    for index, region in enumerate(result.envelope_regions):
        bounds = _region_roi_bounds(result, region.bounds)
        if bounds is None:
            continue
        visible_regions.append((index, bounds))
        left, top, right, bottom = bounds
        color = _region_color(index)
        fill_draw.rectangle(
            (left, top, right - 1, bottom - 1),
            fill=(*color[:3], 48),
        )
    overlay.alpha_composite(fill)
    border_draw = ImageDraw.Draw(overlay)
    for index, (left, top, right, bottom) in visible_regions:
        border_draw.rectangle(
            (left, top, right - 1, bottom - 1),
            outline=_region_color(index),
            width=2,
        )
    return overlay


def render_density_components(result: AnalysisResult) -> Image.Image:
    """Render context component pixels and clipped boxes inside the bbox."""

    labels = _content_values(result, result.component_labels)
    rgba = np.zeros((*labels.shape, 4), dtype=np.uint8)
    for region in result.component_regions:
        color = _region_color(region.label - 1)
        rgba[labels == region.label] = (*color[:3], 96)
    content_image = Image.fromarray(rgba, mode="RGBA")
    context_size = result.transform.source_size
    if content_image.size != context_size:
        content_image = content_image.resize(context_size, Image.Resampling.NEAREST)
    overlay = _context_to_roi(result, content_image)
    draw = ImageDraw.Draw(overlay)
    for region in result.component_regions:
        bounds = _region_roi_bounds(result, region.bounds)
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        draw.rectangle(
            (left, top, right - 1, bottom - 1),
            outline=_region_color(region.label - 1),
            width=2,
        )
    return overlay


def _validate_image_result(image: Image.Image, result: AnalysisResult) -> None:
    if image.size != result.original_size:
        raise ValueError("source image dimensions must match result.original_size")


def _composite_full(
    image: Image.Image,
    result: AnalysisResult,
    overlay: Image.Image,
) -> Image.Image:
    """Composite an original-bbox overlay onto the full source image."""

    _validate_image_result(image, result)
    if overlay.size != _roi_size(result):
        raise ValueError("overlay dimensions must match the protocol pixel_bbox")
    base = image.convert("RGBA")
    base.alpha_composite(overlay, (result.pixel_bbox.left, result.pixel_bbox.top))
    return base


def _titled(image: Image.Image, title: str) -> Image.Image:
    """Add a white header strip with a title above an RGBA image."""

    font = ImageFont.load_default()
    header = 22
    titled = Image.new("RGBA", (image.width, image.height + header), "white")
    ImageDraw.Draw(titled).text((6, 4), title, fill="black", font=font)
    titled.alpha_composite(image, (0, header))
    return titled


def _export_density(
    image: Image.Image,
    result: AnalysisResult,
    destination: str | Path,
) -> None:
    """Save an edge-density overlay with its fixed numerical colorbar."""

    heatmap = colorize(result)
    heatmap.putalpha(150)
    base = _composite_full(image, result, heatmap)

    bar_width = max(104, min(160, base.width // 5))
    output = Image.new("RGBA", (base.width + bar_width, base.height), "white")
    output.alpha_composite(base, (0, 0))
    draw = ImageDraw.Draw(output)
    font = ImageFont.load_default()
    low, high = value_range()
    margin = 16
    vertical_margin = min(42, base.height // 10)
    top = vertical_margin
    bottom = max(top, base.height - vertical_margin - 1)
    left = base.width + margin
    color_width = 22
    for row in range(top, bottom):
        ratio = 1.0 - (row - top) / max(1, bottom - top - 1)
        draw.line((left, row, left + color_width, row), fill=_color(ratio), width=1)
    draw.rectangle((left, top, left + color_width, bottom), outline="black", width=1)
    draw.text((left + color_width + 7, top - 5), f"{high:.3f}", fill="black", font=font)
    draw.text(
        (left + color_width + 7, bottom - 7), f"{low:.3f}", fill="black", font=font
    )
    label = Image.new("RGBA", (max(1, bottom - top), 18), (255, 255, 255, 0))
    ImageDraw.Draw(label).text((0, 1), "point_edge_density", fill="black", font=font)
    output.alpha_composite(
        label.rotate(90, expand=True), (base.width + bar_width - 22, top)
    )
    output.convert("RGB").save(destination, format="PNG")


def _export_comparison(
    image: Image.Image,
    result: AnalysisResult,
    destination: str | Path,
) -> None:
    """Save components and envelopes side by side with titles."""

    left = _titled(
        _composite_full(image, result, render_density_components(result)),
        "Components",
    )
    right = _titled(
        _composite_full(image, result, render_envelopes(result)),
        "Envelopes",
    )
    gap = 12
    output = Image.new(
        "RGBA",
        (left.width + gap + right.width, max(left.height, right.height)),
        "white",
    )
    output.alpha_composite(left, (0, 0))
    output.alpha_composite(right, (left.width + gap, 0))
    output.convert("RGB").save(destination, format="PNG")


def export_view(
    image: Image.Image,
    result: AnalysisResult,
    view: str,
    destination: str | Path,
) -> None:
    """Save the PNG for a single view from one protocol analysis result."""

    _validate_image_result(image, result)
    if view == DENSITY_VIEW:
        _export_density(image, result, destination)
    elif view == COMPONENTS_VIEW:
        _composite_full(image, result, render_density_components(result)).convert(
            "RGB"
        ).save(destination, format="PNG")
    elif view == ENVELOPES_VIEW:
        _composite_full(image, result, render_envelopes(result)).convert("RGB").save(
            destination,
            format="PNG",
        )
    elif view == COMPARISON_VIEW:
        _export_comparison(image, result, destination)
    else:
        raise ValueError(f"unknown view: {view}")
