# Image Stats Explorer

一个用于交互式查看 `image-stats-protocol` 结果的轻量桌面工具。它面向没有 DOM 或
Accessibility Tree 的截图式 computer-use 场景：研究者可以框选截图区域、调整协议
参数，并把边缘密度、连通域和梯度包络映射回原图。应用只负责交互、渲染和 PNG
导出；算法、几何、参数和结果契约只在 `image-stats-protocol` 中维护。

局部视觉 validator 的职责是保守拦截明显错误，不证明点击正确，也不替代 grounding
模型。

## 功能

- 打开 PNG、JPEG、BMP、WebP 和 TIFF 图像
- 通过左键拖拽或像素坐标创建、移动和缩放 bbox
- 在可滚动面板中按四组调整协议 1.0.0 的全部十项参数，并通过信息图标查看即时中文说明
- 一次后台分析生成密度、连通域和包络结果
- 在密度、连通域、包络和左右对比四种视图间切换，不重复分析
- 导出当前视图 PNG；密度图使用固定 `[0, 1]` 色标

## 使用方法

1. 点击“打开图片”。图片打开后没有预设 bbox，需先用左键拖拽框选。
2. 在 bbox 内左键拖拽可移动，拖拽八个控制点可缩放；在框外左键拖拽会新建 bbox。也可输入 `x`、`y`、`width`、`height` 微调。
3. 调整参数后点击“计算”。计算成功后显示青色虚线 context 框；修改图片、bbox 或任一参数会立即清除旧结果和 context 框。
4. 图片打开后会自动按当前画布适配，保持宽高比且小图也会放大；之后可右键拖拽平移画布，直接使用滚轮缩放。切换视图或保存 PNG 不会重置缩放。

任意时刻只运行一个后台分析。分析期间发生输入变化时，旧任务的成功或失败结果都会被
丢弃。左右对比视图共享 bbox、缩放和滚动位置。

## 协议与坐标契约

Explorer 使用 `NormalizedBBox.from_pixel_xywh()` 把整数 bbox 转为规范化 bbox，随后
直接调用 `analyze_bbox()`。协议围绕 bbox 生成居中的自适应 `context_bounds`，其边长
至少为 `resize_size`，也至少为 bbox 最大边乘以 `context_scale`；靠近图片边缘时，上下文
会在图内平移并裁剪。

上下文以 downscale-only 方式映射到 `resize_size × resize_size` 的正方形 letterbox
画布：大图会双线性缩小，小图不放大。所有数组使用画布坐标，padding 在
`valid_mask` 中为 false，也不参与梯度、密度、连通域或包络计算。

GUI 和导出的分析 overlay 都显示整个 `context_bounds` 内的证据，不再裁剪到
`pixel_bbox`。`pixel_bbox` 仍是协议的分析目标，用于中心命中判断和 GUI 交互编辑。
计算成功后，青色虚线显示完整 context，橙色实线显示 bbox；PNG 也包含这两个语义框。
渲染先按 `content_bounds` 和 `valid_mask` 取出有效内容，再按
`transform.source_size` 恢复到原始 context，并把 overlay 放回
`context_bounds.left/top`。区域边界通过 floor/ceil 映回 context 局部坐标；letterbox
padding 不会被当作数据。

## 参数

默认值全部直接读取 `AnalysisParameters()`。参数面板分为“通用 / 上下文”“边缘密度”“连通域”“包络”四组；每个英文参数名右侧的信息图标都会立即显示中文译名、概念、计算方式和调整影响：

| 分组 | 参数 | 默认值 | GUI 范围 | 作用 |
| --- | --- | ---: | ---: | --- |
| 通用 / 上下文 | `resize_size` | `512` | `1..4096` | letterbox 方形画布边长，也是自适应上下文的最小边长。 |
| 通用 / 上下文 | `context_scale` | `1.5` | `1.001..100` | bbox 最大边到请求上下文边长的倍率。 |
| 边缘密度 | `center_fraction` | `0.016` | `0.001..1` | 中心密度统计窗口相对有效内容短边的比例。 |
| 边缘密度 | `gradient_threshold` | `30` | `0..255` | 密度路径的灰度前向差分阈值。 |
| 连通域 | `density_low_threshold` | `0.30` | `0..1` | 滞后连通域的低阈值。 |
| 连通域 | `density_high_threshold` | `0.40` | `0..1` | 滞后连通域的高阈值，不得低于低阈值。 |
| 连通域 | `min_component_area` | `64` | `1..1000000` | 连通域最小真实像素数。 |
| 包络 | `min_grad` | `20` | `0..255` | 包络路径的梯度阈值。 |
| 包络 | `min_ele_area` | `64` | `1..1000000` | 包络候选最小外接框面积。 |
| 包络 | `envelope_max_side_ratio` | `0.8` | `0.001..1` | 包络最长边相对有效内容最长边的上限。 |

比例控件最多保留三位小数。所有组合由协议构造器统一校验；无效组合会禁用“计算”并在
状态区显示原因。“恢复默认值”会一次性恢复全部十项参数。

分析完成后，状态区显示 bbox 中心的 `point_edge_density`、`component_hit`、
`envelope_hit`、上下文中的连通域和包络数量，以及协议版本。

## 开发检查

依赖固定到私有 Git 仓库的 `v1.0.0` tag；算法变更应先在协议仓库发布新版本，再更新
本消费者。

```bash
uv lock --check
uv sync --locked --dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m compileall src
```

## Windows x64 构建

本应用只面向 64 位 Windows x64，以单文件 GUI 可执行文件运行。安装 uv 后，在项目
根目录执行：

```powershell
.\build.ps1
```

脚本按锁文件安装依赖并调用 PyInstaller。产物位于
`dist\ImageStatsExplorer.exe`；仓库不提交 `build/` 或 `dist/`。

## 项目结构

```text
.
├── src/image_stats_explorer/
│   ├── app.py             # PySide6 界面、bbox 到协议调用、单后台任务
│   ├── canvas.py          # 原图、context overlay、bbox 编辑、平移与缩放
│   └── rendering.py       # 协议坐标逆变换、四种视图和 PNG 导出
├── ImageStatsExplorer.spec # PyInstaller 配置
├── build.ps1              # Windows x64 构建脚本
├── pyproject.toml         # 项目元数据和协议 Git tag
└── uv.lock                # 锁定依赖及 precise commit
```
