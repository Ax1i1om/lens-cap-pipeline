# lens-cap-pipeline

一个可审计、可复现的镜头盖图稿 → 浮雕遮罩／SVG → 后续建模与打印准备流程。

本项目的核心原则是：**批准的图稿只读，模型化不重新绘图**。当前稳定核心负责在同一坐标画布上按声明的 palette 生成索引处理稿、材料遮罩和 SVG，并输出可复核 JSON 报告；OpenSCAD／3MF 是显式的后续适配层，不会隐藏在图像处理里。

## 快速开始

需要 Python 3.11+。安装：

```sh
cd lens-cap-pipeline
python scripts/bootstrap.py --dev
# macOS/Linux:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
```

`bootstrap.py` 只在仓库内创建 `.venv`，不会安装全局包。若不使用脚本，也可
手动运行 `python -m venv .venv`、激活它，再执行
`python -m pip install -e '.[test]'`。
在 macOS/Linux 上也可使用 `make bootstrap`；Windows 无 GNU Make 时直接运行
Python 脚本即可。

需要发布级的逐字节复现时，可使用仓库提交的锁文件（先安装
[uv](https://docs.astral.sh/uv/)）：

```sh
python scripts/bootstrap.py --dev --locked
# 等价于：uv sync --locked --extra dev
```

不带 `--locked` 的脚本是兼容性优先的便捷安装，会使用 `pyproject.toml` 的
受支持版本范围；跨机器比较发布物时请固定锁文件，并保留报告中的版本信息。

创建一个新任务（`init` 的第一个参数是配置文件，而不是目录）：

```sh
lens-cap init jobs/my-lens/job.toml \
  --source jobs/my-lens/art/master.png \
  --face-diameter 95 \
  --measured-diameter 95 \
  --foam-thickness 1.5 \
  --job-slug my-lens-cap
# 编辑 job.toml 的圆心、半径和 palette；然后：
lens-cap build jobs/my-lens/job.toml --force
```

`lenscap` 是同一 CLI 的兼容别名。先查看本机可选外部工具（不会生成或上传任何文件）：

```sh
lens-cap doctor --json
```

`validate` 是对已经生成的 process/model 派生物的完整审计，不是仅配置
lint；它需要先有一次成功的 `process`（`build` 会自动完成）。配置解析错误
会由所有阶段命令直接报告。

输出目录包含：

* `process-master.png`：带透明圆外区域的打印处理稿；
* `masks/*.png` 与 `vector/*.svg`：每种材料的同画布遮罩；
* `outside.png`、`base.png`、`relief.png`、`safe-border.png` 和 `role-overlay.png`：几何角色审计输入；
* `process-report.json`、`config.normalized.json`、`source-lock.json`：哈希、尺寸、数值安全、运行时版本和假设。
* `model/<job-slug>.scad`、`model/geometry-report.json`：执行 `model`／`build` 后的参数化模型与几何报告；
* `model/mesh/*.stl`、`model/external-openscad-report.json`、`model/bambu-handoff.json`：仅在明确请求导出或 handoff 时生成。

`build` 是一键流程：复用或生成通过的 process、生成参数化 SCAD、可选导出 STL／写 Bambu handoff，最后写入 validation report。也可以分步执行：

```sh
lens-cap process jobs/my-lens/job.toml --force
lens-cap model jobs/my-lens/job.toml
lens-cap export-openscad jobs/my-lens/job.toml --force
lens-cap bambu-handoff jobs/my-lens/job.toml
lens-cap validate jobs/my-lens/job.toml
./bin/lens-cap process jobs/my-lens/job.toml --force  # 未安装也可从仓库运行
```

`mesh` 和 `handoff` 仍是向后兼容的别名；新脚本建议使用上面的完整命令名。

需要在一条命令中导出网格和生成 handoff 时：

```sh
lens-cap build jobs/my-lens/job.toml --force --export-openscad --bambu-handoff
```

`process`／`model`／`export-openscad` 不会自动上传文件、发布项目或覆盖批准图稿；只有显式 `--force` 才会替换已有的图稿处理、模型和网格派生物。`bambu-handoff` 会按当前模型重写一个可审计的 JSON 清单，但不会启动 Bambu Studio。OpenSCAD／Bambu Studio 不属于必装依赖；缺失工具会标为 `unverifiable`，不会伪造成功。实际 3MF 切片仍需在 Bambu Studio 中检查并记录版本、配置和预览。

交给 Bambu Studio 时请从 handoff 的 `print_sets` 选择一个镜头盖集合：
`integrated_monochrome`（单色一体 STL）或 `multicolor_components`（按材料分配的
base／relief STL）；`fit_coupon` 只用于先打印试配环。不要把一体 STL 与组件集合同时
导入，旧版兼容字段 `stl_inputs` 会列出全部文件但不代表应全部打印。

`model`、`export-openscad`、`bambu-handoff` 和带建模步骤的 `build` 都需要
`measured_diameter_mm`（实际卡合外径）；只有图稿处理时可以只提供
`face_diameter_mm`。

## 卡合参数规则

卡合式任务只需要确认：

1. 镜头实际卡合外径（`measured_diameter_mm`，同时默认作为 `face_diameter_mm`）；
2. 是否贴泡棉，以及未压缩厚度（`fit.foam_liner_status`、`fit.liner_thickness_mm`）。

成品正面／浮雕直径从实测卡合外径派生，除非明确写入 `face_diameter_mm` 覆盖。未提供压缩率时模型暂按 20% 并标为假设；必须先打印试配环，不能把文件检查当成实物配合证明。

## 图稿保真和安全门

* 颜色距离计算固定使用 `int32` 差值与 `int64` 累加，禁止 `int16` 平方溢出；报告中必须出现 `overflow_guard=true`。
* 只允许最近邻缩放标签、圆形／Alpha 排除、明确声明的安全边框和孤立小岛清理。不会重排、重打文字、重新居中、镜像或内容感知裁切。
* 每种材料使用同一个 SVG `viewBox`。遮罩必须互不重叠，并且并集与 process master 的 Alpha 内区一致。
* 外部工具缺失时状态为 `unverifiable`，而不是假装通过；物理卡合永远单独报告。

归档的 `config.normalized.json` 会把绝对 OpenSCAD／Bambu 路径规范化为程序名，
避免换机器后核心配置哈希漂移；原始 TOML 仍保留本机执行路径，适配器报告会记录
工具版本和输出哈希。

要做逐字节发布比较，优先使用无损 PNG/PPM 图稿并用
`bootstrap.py --locked` 固定依赖；JPEG 的 Pillow/libjpeg 解码版本差异可能改变
像素，源图锁只能发现替换，不能消除不同解码器的差异。

## 配置文件

`examples/jena-135-f3-5.toml` 是东德蔡司 Jena Sonnar 135 mm F3.5 的**参数示例**，使用仓库内的几何测试图，不包含受版权保护的图稿。替换 `source_art` 为你有权使用的文件，并核对 `[circle]` 的像素坐标。Palette 的 `role` 使用核心 API 的 `base`／`relief`：`base` 是连续基底，`relief` 才会生成正向遮罩。

可将 [`examples/job-manifest.template.json`](examples/job-manifest.template.json)
复制到任务目录，填写允许文字、旧任务禁用词、图稿哈希、品牌／电影典故来源和
许可证；它与 TOML 分工，前者锁定语义与来源，后者锁定像素处理和几何参数。

`examples/build/` 若出现在本地 checkout 中，只是一次运行产生的派生物；它可能
包含机器相关路径和 STL，不应作为公开 fixture 提交。发布前请从干净 checkout
重跑并只保留经过审计、可重现且获授权的资产。

详细 schema、模型适配和外部工具说明见：

* [`docs/workflow.md`](docs/workflow.md)
* [`docs/schema.md`](docs/schema.md)
* [`docs/model-adapter.md`](docs/model-adapter.md)
* [`docs/release-checklist.md`](docs/release-checklist.md)
* [`README.en.md`](README.en.md)
* [`THIRD_PARTY.md`](THIRD_PARTY.md)
* [`CONTRIBUTING.md`](CONTRIBUTING.md)／[`SECURITY.md`](SECURITY.md)

## Skill 集成

希望由 Codex 按这套门禁协助生产时，可加载仓库内的
[`skills/lens-cap-production/SKILL.md`](skills/lens-cap-production/SKILL.md)；
中文说明在
[`skills/lens-cap-production/SKILL.zh-CN.md`](skills/lens-cap-production/SKILL.zh-CN.md)。
Skill 只负责路由和决策，实际文件生成仍由本仓库的 `lens-cap` CLI 完成。

## 开源边界

代码和模板采用 [Apache-2.0](LICENSE)。镜头品牌、商标、电影参考、用户图稿和下载的镜头盖模型不自动转移到该许可证；每个任务都应在 manifest 中记录来源、作者、许可证和修改说明。仓库默认忽略私有图稿、STL、3MF 和 G-code。

## 开发与测试

```sh
python -m pip install -e '.[dev]'
python -m pytest
python -m compileall -q lens_cap_pipeline
```

CI 运行同样的可移植检查，并在测试中验证颜色距离不会发生黑／象牙反转、重复运行字节稳定且不会隐式覆盖。OpenSCAD/Bambu 的集成检查是可选的，因为它们不是跨平台 Python 依赖。
