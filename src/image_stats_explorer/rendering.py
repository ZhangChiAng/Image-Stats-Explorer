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
CONTEXT_COLOR = (0, 190, 255, 255)
BBOX_COLOR = (255, 80, 35, 255)


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


def _restore_context(
    result: AnalysisResult,
    content_image: Image.Image,
    resampling: Image.Resampling,
) -> Image.Image:
    """Restore a content-sized image to the original-resolution context."""

    content = result.content_bounds
    expected_size = (content.width, content.height)
    if content_image.size != expected_size:
        raise ValueError("content image dimensions must match content_bounds")
    context_size = result.transform.source_size
    if content_image.size != context_size:
        return content_image.resize(context_size, resampling)
    return content_image


def colorize(result: AnalysisResult) -> Image.Image:
    """Render valid density values onto the complete original-resolution context."""

    low, high = value_range()
    scale = high - low
    densities = _content_values(result, result.density_map)
    rgba = np.asarray(
        [_color((float(value) - low) / scale) for value in densities.flat],
        dtype=np.uint8,
    ).reshape(*densities.shape, 4)
    content_image = Image.fromarray(rgba, mode="RGBA")
    return _restore_context(result, content_image, Image.Resampling.BILINEAR)


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


def _region_context_bounds(
    result: AnalysisResult,
    bounds: PixelBounds,
) -> tuple[int, int, int, int]:
    """Map a canvas region to local coordinates in the complete context."""

    content = result.content_bounds
    if (
        bounds.left < content.left
        or bounds.top < content.top
        or bounds.right > content.right
        or bounds.bottom > content.bottom
    ):
        raise ValueError("protocol region must stay inside content_bounds")

    source_width, source_height = result.transform.source_size
    left = math.floor((bounds.left - content.left) * source_width / content.width)
    top = math.floor((bounds.top - content.top) * source_height / content.height)
    right = math.ceil((bounds.right - content.left) * source_width / content.width)
    bottom = math.ceil((bounds.bottom - content.top) * source_height / content.height)
    return (
        left,
        top,
        right,
        bottom,
    )


def render_envelopes(result: AnalysisResult) -> Image.Image:
    """Render envelope regions across the complete original-resolution context."""

    overlay = Image.new("RGBA", result.transform.source_size, (0, 0, 0, 0))
    fill = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    fill_draw = ImageDraw.Draw(fill)
    visible_regions: list[tuple[int, tuple[int, int, int, int]]] = []
    for index, region in enumerate(result.envelope_regions):
        bounds = _region_context_bounds(result, region.bounds)
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
    """Render component pixels and boxes across the complete context."""

    labels = _content_values(result, result.component_labels)
    rgba = np.zeros((*labels.shape, 4), dtype=np.uint8)
    for region in result.component_regions:
        color = _region_color(region.label - 1)
        rgba[labels == region.label] = (*color[:3], 96)
    content_image = Image.fromarray(rgba, mode="RGBA")
    overlay = _restore_context(result, content_image, Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(overlay)
    for region in result.component_regions:
        bounds = _region_context_bounds(result, region.bounds)
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
    """Composite a complete-context overlay onto the full source image."""

    _validate_image_result(image, result)
    if overlay.size != result.transform.source_size:
        raise ValueError("overlay dimensions must match transform.source_size")
    base = image.convert("RGBA")
    base.alpha_composite(
        overlay,
        (result.context_bounds.left, result.context_bounds.top),
    )
    _draw_semantic_bounds(base, result)
    return base


def _draw_dashed_rectangle(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    fill: tuple[int, int, int, int],
    width: int = 2,
    dash: int = 6,
    gap: int = 4,
) -> None:
    left, top, right, bottom = bounds
    right -= 1
    bottom -= 1
    for start in range(left, right + 1, dash + gap):
        end = min(start + dash - 1, right)
        draw.line((start, top, end, top), fill=fill, width=width)
        draw.line((start, bottom, end, bottom), fill=fill, width=width)
    for start in range(top, bottom + 1, dash + gap):
        end = min(start + dash - 1, bottom)
        draw.line((left, start, left, end), fill=fill, width=width)
        draw.line((right, start, right, end), fill=fill, width=width)


def _draw_semantic_bounds(image: Image.Image, result: AnalysisResult) -> None:
    """Draw protocol context and bbox frames on a full-size rendered image."""

    draw = ImageDraw.Draw(image)
    context = result.context_bounds
    _draw_dashed_rectangle(
        draw,
        (context.left, context.top, context.right, context.bottom),
        CONTEXT_COLOR,
    )
    bbox = result.pixel_bbox
    draw.rectangle(
        (bbox.left, bbox.top, bbox.right - 1, bbox.bottom - 1),
        outline=BBOX_COLOR,
        width=2,
    )


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
