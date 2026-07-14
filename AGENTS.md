# Repository Guidelines

## Product & Research Context

This desktop tool helps researchers inspect deterministic local visual evidence
for computer-use agents that only have screenshots, not DOM or accessibility-tree
data. A validator should conservatively reject obvious mistakes after a proposed
click; it does not prove correctness or replace the grounding model.

`point_edge_density` is the first cheap indicator for visual structure around
text, icons, and buttons. The GUI exists to inspect and export that evidence,
not to become a routing-policy implementation.

## Architecture and Ownership

Application code lives in `src/image_stats_explorer/`. `app.py` owns the PySide6
window, ROI editing flow, and single background worker; `canvas.py` owns canvas
interaction; `rendering.py` maps protocol arrays and regions back to the original
pixel bbox, renders the views, and exports PNGs. `ImageStatsExplorer.spec` and
`build.ps1` define Windows packaging.

`image-stats-protocol` is the only home for bbox normalization, resize/letterbox
rules, density, connected components, envelopes, parameters, and result types.
Do not add local algorithm copies, local result wrappers, private geometry imports,
or compatibility analysis entry points here. Convert the UI's integer ROI only
with `NormalizedBBox.from_pixel_xywh()` and call `analyze_bbox()` directly.

Protocol arrays are square letterbox-canvas outputs. Rendering must use
`content_bounds`, `valid_mask`, `transform`, and `pixel_bbox`; never stretch the
whole canvas over the ROI or treat padding as data. GUI defaults must come from
`AnalysisParameters()` rather than copied constants.

## Build and Verification

- `uv lock --check`: verify the private protocol tag and commit are locked.
- `uv sync --locked --dev`: create the Python 3.11 environment.
- `uv run --locked ruff check .`: run static style checks.
- `uv run --locked ruff format --check .`: verify formatting.
- `uv run --locked python -m compileall src`: compile the application.
- `.\build.ps1` on Windows PowerShell: build `dist\ImageStatsExplorer.exe` with
  PyInstaller. Keep generated `build/` and `dist/` artifacts out of commits.

## Style

Use four-space indentation, type annotations, focused module-level helpers, and
Ruff's 88-character line length. Keep Qt-independent rendering and coordinate
mapping in `rendering.py`. Algorithm changes begin in `image-stats-protocol` and
then flow into this consumer through a versioned dependency update.
