# lens-cap-pipeline

一个可审计、可复现的镜头盖图稿 → 浮雕遮罩／SVG → 后续建模与打印准备流程。

> **ALPHA · v0.1.0-alpha.2**：这是首个公开预览版的路由修订版。配置 schema、模型适配器和
> CLI 仍可能发生不兼容变化；文件检查不等于实体卡合或 Bambu 3MF 切片验证。

本项目的核心原则是：**批准的图稿只读，模型化不重新绘图**。图像阶段的“高质量”验收是镜头规格、文字层级和视觉风格等价，而不是要求生成像素一致；用户批准的栅格及其哈希才是后续确定性生产的精确边界。当前稳定核心负责在同一坐标画布上按声明的 palette 生成索引处理稿、材料遮罩和 SVG，并输出可复核 JSON 报告；OpenSCAD／3MF 是显式的后续适配层，不会隐藏在图像处理里。SVG 使用保留字体直角的有向像素并集轮廓，不再把像素／行程矩形原样挤出；曲线与斜线只在报告记录的亚喷嘴偏差内简化。

## 快速开始

需要 Python 3.11+。安装：

```sh
cd lens-cap-pipeline
./scripts/bootstrap.py --dev
# macOS/Linux alternative: python3 scripts/bootstrap.py --dev
# Windows alternative: py -3 scripts/bootstrap.py --dev
# macOS/Linux:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
```

`bootstrap.py` 只在仓库内创建 `.venv`，不会安装全局包。若不使用脚本，也可
手动运行 `python3 -m venv .venv`、激活它，再执行
`python3 -m pip install -e '.[test]'`（Windows 先用 `py -3 -m venv .venv`，激活后
用 `python -m pip ...`，确保安装在项目环境中）。
在 macOS/Linux 上也可使用 `make bootstrap`；Windows 无 GNU Make 时直接运行
Python 脚本即可。

需要发布级的逐字节复现时，可使用仓库提交的锁文件（先安装
[uv](https://docs.astral.sh/uv/)）：

```sh
./scripts/bootstrap.py --dev --locked
# 等价于：uv sync --locked --extra dev
# Windows：py -3 scripts/bootstrap.py --dev --locked
```

不带 `--locked` 的脚本是兼容性优先的便捷安装，会使用 `pyproject.toml` 的
受支持版本范围；跨机器比较发布物时请固定锁文件，并保留报告中的版本信息。

仓库内的 companion Skill 以 `skills/manifest.json` 为唯一来源。可先做只读的
版本／哈希检查：

```sh
./scripts/install_skills.py check --json
```

同步默认只是预览；指定目标并明确 `--apply` 后才会写入：

```sh
./scripts/install_skills.py sync --dest .agents/skills
./scripts/install_skills.py sync --dest .agents/skills --apply
```

Windows 可将同一命令写成 `py -3 scripts/install_skills.py ...`，或使用
`bin/lens-cap-skills.cmd`。

目标目录会保存 `.lens-cap-skills.json` 回执（项目版本、manifest 哈希和逐文件
SHA-256），重复执行不改动未变化文件。检测到用户改动时不会静默覆盖，需明确传入
`--force`；删除源中已不存在的文件还需 `--force --prune`。也可用
`--environment codex|claude` 选择宿主默认目录；写入推断出的全局目录必须再传
`--allow-global`，否则只检查／预览。`bin/lens-cap-skills` 和
`scripts/sync_skills.py` 是同一入口的便携别名。

如果宿主目录还留有早期原型 `lens-cap-front-image`，先做只读迁移预览，再用
显式 `--apply --force` 把它**移动**到可恢复的 `.lens-cap-legacy/` 隔离目录：

```sh
python3 scripts/install_skills.py migrate --environment codex --allow-global --json
python3 scripts/install_skills.py migrate --environment codex --allow-global \
  --apply --force --json
python3 scripts/install_skills.py sync --environment codex --allow-global \
  --apply --json
```

旧版也可能占用当前的 `lens-cap-production` 名称；这属于有歧义的冲突，只有
确认它确实是旧文件时才额外加 `--include-canonical`。迁移不会删除内容，回执会
记录原路径和备份路径；不想动全局目录时，优先使用项目内 `--dest .agents/skills`。

如果宿主只会扫描静态 metadata，或你想先独立检查一条自然语言请求是否命中本路由，
可运行同一份 manifest 驱动的无依赖 resolver：

```sh
python3 scripts/resolve_skill_route.py \
  "为适马 28–70mm F2.8 设计圆形镜头盖正面并输出 3MF"
# 只有已建立镜头盖上下文时，才允许省略“镜头盖／正面”：
python3 scripts/resolve_skill_route.py "试试康泰时28F2" \
  --lens-cap-context
```

它只判断 `lens-cap-imagegen → lens-cap-production` 的顺序和排他性，不生成图或 CAD；
明确的镜头盖／正面名词短语无需额外动作词，紧凑的品牌+型号写法（如 `适马2870`）也
可命中；若镜头身份来自附图／目录而不在文字中，可加 `--named-lens`。这给旧版 Codex/Claude
宿主一个明确的适配入口，但不能替代宿主最终加载 Skill。干净任务里的
“试试某镜头”默认不命中；只有调用方确认当前已经是镜头盖任务时才传
`--lens-cap-context`，避免把普通镜头测试误路由为镜头盖设计。

> **分发边界（Alpha）**：只用 `pip install` 安装 CLI wheel 不会带上两个
> companion Skills、`bin/lens-cap-3mf` 或 `tools/3mf_adapter`。要走完整的
> “图稿审批 → 3MF”链路，请使用 Git checkout，或先**解压 source distribution
> 并从解压目录运行** Skill 同步器和 3MF bridge；仅执行 `pip install` 仍不会
> 安装这些 companion 文件。wheel 只覆盖核心 Python 处理 API。

Codex 通常在任务启动时缓存 Skill 目录；完成同步后请新建任务或刷新宿主，
再测试自动路由。仓库的 clean-room 脚本能验证文件生产链，但不能替代某一
宿主的 Skill 发现器；若目录尚未刷新，可在任务中明确调用
`$lens-cap-imagegen`，再按顺序调用 `$lens-cap-production`。
本仓库 frontmatter 按当前 Codex 校验器把触发词放在 `metadata.triggers`；只会
扫描顶层 `triggers` 的旧路由器需要显式调用这两个 Skill 或使用宿主适配器，不能
因此把非法字段加回正式 Skill。

创建一个新任务（`init` 的第一个参数是配置文件，而不是目录）：

```sh
lens-cap init jobs/my-lens/job.toml \
  --source jobs/my-lens/art/master.png \
  --measured-diameter 85 \
  --adapter-nominal-ring 80 \
  --adapter-radial-wall 2.5 \
  --foam-thickness 1.5 \
  --job-slug mamiya-sekor-c-80-f1-9-cap \
  --lens-identity "Mamiya-Sekor C 80mm F1.9" \
  --display-text 80 F1.9 "MAMIYA-SEKOR C" 645 "REHOUSED MEDIUM FORMAT"
# 编辑 job.toml 的圆心、半径和 palette；然后：
lens-cap build jobs/my-lens/job.toml --force
```

`--adapter-nominal-ring` 与 `--adapter-radial-wall` 是可选的来源分解，必须成对
提供；CLI 会校验 `80 + 2 × 2.5 = 85 mm`（容差 0.05 mm）并写入
`[metadata]`。真正驱动卡合几何、且唯一必需的机械尺寸仍是实测
`--measured-diameter`；已经直接量到实际卡合外径时，无需再回答适配环分解。

`lenscap` 是同一 CLI 的兼容别名。先查看本机可选外部工具（不会生成或上传任何文件）：

```sh
lens-cap doctor --json
```

如果目标是直接得到可交给切片器的 3MF，不要停在 `build` 的 handoff：使用仓库
提供的一键桥接器。它会重新调用公开 CLI、检查同画布浮雕、导出一体原生 3MF，
并用无第三方依赖的校验器读取包；凸条开启时还会直接在最终 3MF 网格的起点、内部
截面和终点核对每个预期角位置，并要求连续的全高轴向接触列与足宽端面；同时要求
所有必需色都有非空三角面分配、且没有当前 palette 之外
的已用颜色。`OpenSCAD` 是生成一体 3MF 的唯一外部依赖。
用户明确要求 3MF 时，只有目标 `.3mf` 已存在且该验证通过才算完成；PNG、SCAD、
STL、handoff JSON、命令说明或预检通过都不是 3MF 交付成功。

```sh
./bin/lens-cap-3mf jobs/my-lens/job.toml --force --json
# 可选：在明确提供本机 Bambu 配置后同时生成含 G-code 的切片 3MF
./bin/lens-cap-3mf jobs/my-lens/job.toml --force --bambu slice \
  --machine-profile /path/to/machine.json \
  --process-profile /path/to/process.json \
  --filament-profile /path/to/filament.json --json
```

`lens-cap-3mf` 是发布级端点，默认会在生成任何派生物前验证当前任务的
schema-v2 `design-brief.json`：必须明确标记 `generation.approved=true`，锁定当前
`source_art` 的 SHA-256，包含焦段／光圈顺序、来源锚点、已通过的 `design_review`
反通用化与完成度检查和许可记录。审核还必须绑定同一候选图哈希、用稳定 ID 选定
结构性主锚点、把至少两个不同系统的后果绑定回同一锚点，并逐项比较所有本地哈希
质量参考；参考角色统一使用 `roles` 数组。桥接器会先看
任务 `[metadata].design_brief`，再查找任务目录向上的最近 `design-brief.json`；也可用
`--brief PATH` 显式指定。缺失、未批准或与当前任务不一致的 brief 返回 `FAILED` 并退出非零，不能把
裸 TOML/PNG 误报为完成的 3MF。已有的私有 `process`／`model` 实验仍可直接运行核心 CLI。

从刚保存的图稿和任务配置开始，可用 provider-neutral 的交接脚手架自动写入候选哈希、
机械参数和透明图的圆形建议；它不会调用图像服务，也不会替用户批准：

```sh
lens-cap handoff-init jobs/my-lens/job.toml \
  --brand Mamiya --model "Mamiya-Sekor C 80mm F1.9" \
  --focal-length 80 --maximum-aperture F1.9 \
  --provider "OpenAI built-in image_gen" \
  --anchor-source https://www.suaudeau.eu/memo/Manuels/Mamiya_M645_Service_Manual.pdf
# 审阅来源后让 anchor.evidence_state 以 sourced／verified 等正向状态开头；
# 补齐全部 REPLACE、三项 provenance 许可字段和 notes；不适用时写明原因（例如
# “not applicable — no third-party mark rendered”），再核对完整文字闭集与圆形构图；
# 全分辨率审核后填写候选哈希、结构性 hero_anchor_index／hero_anchor_id、两个带相同
# anchor_id 且属于不同系统的结构后果、
# 隐藏文字／最近邻替换／构图／造型语法／完成度／收敛检查及具体 thesis/notes；
# 所有质量参考也逐项通过后，才把 generation.approved 改为 true。
lens-cap handoff-check jobs/my-lens/job.toml --json
./bin/lens-cap-3mf jobs/my-lens/job.toml --force --json
```

`handoff-init` 属于可打印图稿桥，因此任何含有效正面直径的 job 都会标为
`printable_front`；只有 `face_diameter_mm` 的独立浮雕正面可以完成图稿交接，但卡合盖
仍会在取得 `measured_diameter_mm` 前被机械阶段阻止，二者不能混为同一个尺寸。
`handoff-init` 默认拒绝覆盖已有 brief；`--force` 只应在确认目标路径后使用。
`init` 的 `--lens-identity` 与 `--display-text` 必须成对提供；后者的前两项必须与
`handoff-init` 的焦段／光圈一致，后续型号、系统等二级文字也会原样进入
`display_text`／`allowed_text`，严格门禁要求三者保持完整、有序且完全相同。
门禁还要求 job 的规范化身份文字包含 brief 的品牌、型号、焦段和 F 值，不能靠同时
修改两处自由文本把任务悄悄换成另一颗镜头。
可变光圈变焦可直接传 `--maximum-aperture F3.5-5.6`，也可用首端锚点配合
`--maximum-aperture-display F3.5-5.6`；en dash 会被规范化，但完整范围必须始终
保留在 `display_text[1]`。当前 brief schema 暂不支持 T-stop，会显式拒绝而非
静默当成 F 值。
`handoff-init --provider` 是必填的实际来源记录；命令输出的 `next` 会列出仍需
人工核实的证据状态、许可、文字闭集、圆形构图和批准项。
脚手架还会把当前 `[circle]`、完整 palette（含角色、RGB、浮雕高度等）以及
grid、safe border、prefilter、cleanup、assembly mode 冻结到 `job_binding`；
审批后任一 job 值漂移都会失败。不透明图稿应先人工确定 TOML 圆形；
若在脚手架生成后才修改这些字段，先用相同参数重新执行
`handoff-init --force` 刷新快照，再填写人工审核字段。
每个 anchor 都必须有可复核的 http(s) 来源或显式 `archive:` 标识，以及完整的
摘要、上下文、母题和识别线索；裸 `x` 不能通过。`handoff-check` 与桥接器使用同一严格校验。brief 是一次图稿的
语义、来源与人工批准快照；多尺寸任务中的实际直径、泡棉、凸条和打印参数以各自的
`job.toml` 为机械权威，brief 中的机械摘要不能替代任务配置。

Windows 使用 `py -3 scripts/build_3mf.py jobs/my-lens/job.toml --force --json`
或 `bin/lens-cap-3mf.cmd`；其余参数相同。

缺少 OpenSCAD 时该命令会明确返回 `UNVERIFIABLE` 并以非零状态退出，不会把
SCAD 或 handoff JSON 冒充成 3MF；已有外部 STL 仍可用
`tools/3mf_adapter/three_mf_adapter.py standard` 生成 Core 3MF。

桥接器会优先使用命令行的 `--openscad`／`--bambu-path`；省略时读取任务
`[print]` 中的 `openscad_executable`／`bambu_executable`（含路径的值按
`job.toml` 所在目录解析，裸命令名按宿主机 PATH 查找），最后才查找默认
PATH 和 macOS 应用包。这样
不同 CODEX 主机可以把工具位置写进任务而不改图稿或模型语义。

`validate` 是对已经生成的 process/model 派生物的完整审计，不是仅配置
lint；它需要先有一次成功的 `process`（`build` 会自动完成）。配置解析错误
会由所有阶段命令直接报告。

要在没有当前对话历史的情况下复演“新用户”链路，可运行仓库自带的
干净环境测试。默认 fixture 是本轮实际 ImageGen 生成并人工批准的
Helios-44-2 REHOUSE v3，只包含一个 95 mm 任务，并把它送入 canonical bridge。
v2 保留为 77／82／95 mm 的旧版多直径矩阵；Mamiya-Sekor C 80mm F1.9 fixture
负责跨品牌、适配壁和泡棉组合。脚本重新调用公开 CLI，检查焦段／光圈语义和遮罩
投影；只有本机有兼容 OpenSCAD 时才会真正输出并验证原生 3MF：

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bridge-job jobs/95mm/job.toml --bambu never --json
# 旧版 77／82／95 mm 多直径矩阵：
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v2 \
  --bambu never --json
# 跨品牌、适配壁与泡棉矩阵：
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse \
  --bambu never --json
# 完整主机验证（严格要求 OpenSCAD、Bambu Studio 及三份本机配置）
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bridge-job jobs/95mm/job.toml --bambu auto \
  --require-external --keep-workdir \
  --machine-profile /path/to/machine.json \
  --process-profile /path/to/process.json \
  --filament-profile /path/to/filament.json --json
```

在缺少 OpenSCAD 的便携主机上，烟测仍可退出 0 供 CI 收集诊断，但顶层
`status` 必须是 `unverifiable`；`deterministic_preflight_status=passed` 只说明批准
数据包、确定性图像处理和投影预检通过，`native_3mf_complete=false` 则明确表示没有
完成实际 3MF。加 `--require-external` 才会把这个边界变成硬失败。

若还要验收“真人自然语言 → 一次性询问并持久化卡合参数 → 3MF”的交互边界，
可运行仓库附带的结构化复盘剧本。它不依赖某个 Codex 对话缓存，会先检查专用
Skill 的顺序和三项 intake，再在隔离临时目录调用同一生产 runner：

```sh
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/helios-44-2-imagegen-v3-95mm-clean-room.json \
  --bambu never --json
# 旧版 Helios 多直径交互剧本
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/helios-44-2-rehouse-clean-room.json \
  --bambu never --json
# 独立的 Mamiya-Sekor C + 泡棉／转接壁交互样本
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/mamiya-sekor-c-80-f1-9-rehouse-clean-room.json \
  --bambu never --json
```

想一次跑完路由、三个 fixture 和三份交互剧本，可在仓库根目录执行
`make smoke-all`（或 `make acceptance`）。这个便携门禁允许外部工具缺失时明确
报告 `unverifiable`；要声称 3MF 已完成，仍须使用上面的 `--require-external`
命令并确认产物存在且通过验证。

剧本可复制后替换为其他镜头；`fixture.primary_job` 必须与回答的实际卡合外径、
泡棉厚度和凸条配置一致。需要保留本次输出时再加
`--artifact-dir PATH --force-artifacts`。剧本不会重新调用 ImageGen；它把 fixture
中记录的候选图稿、提示词和人工批准当作生成服务边界，只复演批准数据包之后的
确定性生产链。因此它既不声称像素级复现，也不把交互 `passed` 或实体卡合测量
冒充成已生成 3MF。

烟测还会拒绝 `generation.approved` 未确认、缺少有来源的品牌文化／REHOUSE
锚点，或指向 fixture 外部的提示词／图稿路径；这让“已审批图稿 → 3MF”边界
在没有对话历史时仍可被机器检查。

变焦镜头的 `design-brief.json` 仍保留正数 `lens_identity.focal_length_mm`
机器锚点，并可选填同级 `focal_length_display`（如 `28–70mm`）；该字符串必须
同时作为 `display_text[0]`／`allowed_text` 第一项，且范围从数值锚点开始并递增。
定焦镜头省略它即可保持原有行为。

`--keep-workdir` 会保留隔离输出，`--artifact-dir PATH` 可在明确指定时复制
生成的 3MF 及 sidecar；脚本不会带入示例目录旧的 `out/`、3MF 或对批准图稿
做写回。输出中的 `fit_status` 仍是 `UNVERIFIABLE`，直到同材料试配环被实际
打印并测量。

测试矩阵、干净 Git/sdist 复演结果以及跨宿主已知边界，见
`docs/clean-room-rehearsal.md` 的 **Latest audit snapshot**。

需要直接检查 3MF Core 包时，可使用仓库内不依赖第三方库的适配器：

```sh
python3 tools/3mf_adapter/three_mf_adapter.py verify path/to/model.3mf
python3 tools/3mf_adapter/three_mf_adapter.py verify path/to/sliced.3mf --require-slice
```
它检查 ZIP CRC、Core XML 关系、所有模型部件的网格索引／边界；对切片包还会核对
唯一且有序的 G-code 块、配置／切片元数据、单调 Z 层、逐层挤出，以及与模型包围盒
尺寸相容、具有合理范围与多样性的非退化 XY 挤出路径；它不会把只塞入几行假 G-code 的 ZIP 当成切片成功，
但这也不是对工具路径覆盖整个模型边界的几何证明；
它不能替代 Bambu 预览或实体卡合试配。

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

`process`／`model`／`export-openscad` 不会自动上传文件、发布项目或覆盖批准图稿；只有显式 `--force` 才会替换已有的图稿处理、模型和网格派生物。`bambu-handoff` 会按当前模型重写一个可审计的 JSON 清单，但不会启动 Bambu Studio。OpenSCAD／Bambu Studio 不属于必装依赖；缺失工具会标为 `unverifiable`，不会伪造成功。OpenSCAD 导出默认使用支持多实体彩色浮雕装配的 `Manifold` 后端；若本机 OpenSCAD 不支持该后端，必须升级或改用分色 STL，不应把只含底座的旧 `CGAL` 装配体当成完整一体模型。实际 3MF 切片仍需在 Bambu Studio 中检查并记录版本、配置和预览。

交给 Bambu Studio 时请从 handoff 的 `print_sets` 选择一个镜头盖集合：
`integrated_monochrome`（单色一体 STL）或 `multicolor_components`（按材料分配的
base／relief STL）；`fit_coupon` 只用于先打印试配环。不要把一体 STL 与组件集合同时
导入，旧版兼容字段 `stl_inputs` 会列出全部文件但不代表应全部打印。

`model`、`export-openscad`、`bambu-handoff` 和带建模步骤的 `build` 都需要
`measured_diameter_mm`（实际卡合外径）；只有图稿处理时可以只提供
`face_diameter_mm`。

导出 STL 后，可用公开的投影审计脚本把每个浮雕网格与同画布遮罩逐色比对。下例
的 `1` 适用于未简化轮廓；标准 `lens-cap-3mf` 会从 process 报告中已限定的
矢量偏差自动推导容差（当前抗锯齿轮廓通常为 `2`），不要任意放宽：

~~~sh
./bin/audit-stl-projection \
  --mesh ivory=build/model/mesh/my-lens-cap-ivory_relief.stl \
  --expected-mask ivory=build/masks/ivory.png \
  --canvas-size-mm 95 --tolerance-pixels 1 \
  --output-report build/model/projection-report.json \
  --output-dir build/model/projection-diff
~~~

它能发现平移、镜像、白边和颜色网格错位，但不会把文件检查冒充成实体
卡合或切片成功。

## 卡合参数规则

卡合式任务需要确认：

1. 镜头实际卡合外径（`measured_diameter_mm`，同时默认作为 `face_diameter_mm`）；
2. 是否贴泡棉，以及未压缩厚度（`fit.foam_liner_status`、`fit.liner_thickness_mm`）；
3. 是否保留内壁摩擦凸条（默认开启；明确关闭可使用
   `--no-friction-ribs` 或 `fit.friction_ribs_enabled = false`，需要显式开启时可用
   `--friction-ribs`）。

如果用户同时知道转接环标称直径和单侧径向壁厚，可以额外记录
`--adapter-nominal-ring` 与 `--adapter-radial-wall`；两项必须同时出现，并满足
`标称直径 + 2 × 单侧壁厚 = measured_diameter_mm`（容差 0.05 mm）。它们用于审计
尺寸来源，不增加第四个必答问题；实测卡合外径仍是唯一必需的机械值。

成品正面／浮雕直径从实测卡合外径派生，除非明确写入 `face_diameter_mm` 覆盖。贴泡棉且未提供压缩率时模型暂按 20% 并标为假设；不贴泡棉时默认压缩率为 0。内壁凸条有两个中性预设：`light_tapered`（默认，12 条窄而浅的渐缩凸条，径向侵入 0.10 mm）用于兼容性和轻微增摩，`wide_tapered`（6 条宽楔形、8° 基部角／4.4° 端部角、接近全侧壁高度）用于接近参考图中粗壮凸起的视觉与机械轮廓。两者都不是品牌元素；可用 `--friction-rib-profile wide_tapered` 或在 `[fit]` 写入 `friction_rib_profile` 选择，显式填写的数量、侵入量、宽度和高度优先于预设。带泡棉时凸条仍可能局部增加压缩，必须先打印试配环，不能把文件检查当成实物配合证明。
以 95 mm 卡合外径、1.5 mm 泡棉、20% 临时压缩的参考测试为例，`wide_tapered` **显式覆盖**为约 0.55 mm 径向侵入、6.8 mm 宽度和 12.5 mm 高度（该预设本身默认侵入量为 0.30 mm），局部线性压缩估算约 56.7%；这只是试配起点，不能替代同材料、同喷嘴的 coupon 实测。不贴泡棉时，报告还会给出带符号的裸壁名义干涉量（正值为过盈、负值为余隙）。
旧版未声明凸条的配置在重建时会继承这一新默认；若要复现旧的光滑内壁，请显式写入 `friction_ribs_enabled = false`，并重新跑 process/model，不能继续使用旧模型文件冒充当前配置。

## 图稿保真和安全门

* 颜色距离计算固定使用 `int32` 差值与 `int64` 累加，禁止 `int16` 平方溢出；报告中必须出现 `overflow_guard=true`。
* 只允许最近邻缩放标签、圆形／Alpha 排除、明确声明的安全边框和孤立小岛清理。不会重排、重打文字、重新居中、镜像或内容感知裁切。
* 每种材料使用同一个 SVG `viewBox`。遮罩必须互不重叠，并且并集与 process master 的 Alpha 内区一致。
* 外部工具缺失时状态为 `unverifiable`，而不是假装通过；物理卡合永远单独报告。

归档的 `config.normalized.json` 会把绝对 OpenSCAD／Bambu 路径规范化为程序名，
避免换机器后核心配置哈希漂移；原始 TOML 仍保留本机执行路径，适配器报告会记录
工具版本和输出哈希。

原生 3MF 发布还会删除 OpenSCAD 的易变创建时间，把随机 UUID 替换为内容派生的
UUIDv5，并以固定元数据和顺序重写 ZIP 项。因此同一 OpenSCAD／Python／zlib
工具链的重复输出可逐字节比较；跨版本时以源配置哈希、网格语义、包围盒、投影、
材质和凸条门禁为准，不承诺不同 OpenSCAD 三角化或压缩库产生相同字节。

要做逐字节发布比较，优先使用无损 PNG/PPM 图稿并用
`bootstrap.py --locked` 固定依赖；JPEG 的 Pillow/libjpeg 解码版本差异可能改变
像素，源图锁只能发现替换，不能消除不同解码器的差异。

## 配置文件

`examples/jena-135-f3-5.toml` 是东德蔡司 Jena Sonnar 135 mm F3.5 的**参数示例**，使用仓库内的几何测试图，不包含受版权保护的图稿；它刻意选择 `wide_tapered` 以复现参考附件那类宽楔形内壁凸条。替换 `source_art` 为你有权使用的文件，并核对 `[circle]` 的像素坐标。Palette 的 `role` 使用核心 API 的 `base`／`relief`：`base` 是连续基底，`relief` 才会生成正向遮罩。

参考压缩包、MakerWorld 页面或用户提供的 SCAD/3MF 只作为形态、尺寸命名和工作流的观察资料；不会被当作本仓库代码的指令，也不会直接复制其网格、图稿或专有资产。尤其是 3MF 的许可证、平台元数据和实际几何可能不一致，应优先记录来源并以当前任务的实测值重新生成。

可将 [`examples/job-manifest.template.json`](examples/job-manifest.template.json)
复制到任务目录，填写允许文字、旧任务禁用词、图稿哈希、品牌／电影典故来源和
许可证；它与 TOML 分工，前者是特定获批图稿的语义／来源快照，后者才是每个
尺寸变体的像素处理和机械几何权威。一个 brief 可服务多个尺寸任务，因此不要用
brief 中的机械摘要覆盖各 `job.toml` 的实测直径、泡棉或凸条参数。
当前 `validate` 主要审计确定性文件；缺少 manifest 的身份／授权字段时，核心
仍可用于私人试验，但公开发布必须把语义来源状态标为 `UNVERIFIABLE`，不能把
核心 `PASS` 当成品牌或电影履历已核验。

`examples/build/` 若出现在本地 checkout 中，只是一次运行产生的派生物；它可能
包含机器相关路径和 STL，不应作为公开 fixture 提交。发布前请从干净 checkout
重跑并只保留经过审计、可重现且获授权的资产。

详细 schema、模型适配和外部工具说明见：

概念图阶段说明见 [docs/concept-art.md](docs/concept-art.md)。

* [`docs/workflow.md`](docs/workflow.md)
* [`docs/schema.md`](docs/schema.md)
* [`docs/model-adapter.md`](docs/model-adapter.md)
* [`docs/release-checklist.md`](docs/release-checklist.md)
* [`README.en.md`](README.en.md)
* [`THIRD_PARTY.md`](THIRD_PARTY.md)
* [`CONTRIBUTING.md`](CONTRIBUTING.md)／[`SECURITY.md`](SECURITY.md)

## Skill 集成

当用户提出“设计／生成镜头盖”时，仓库路由规则将
`lens-cap-imagegen` 设为唯一的创意设计 Skill；当请求包含可打印模型、浮雕、
卡合、SCAD、STL 或 3MF 时，改由 `lens-cap-production` 负责生产阶段。不要把
通用平面设计、Logo、海报、产品视觉、UI、CAD 或 3D 设计 Skill 与镜头盖路由
并行调用。完整规则见 [AGENTS.md](AGENTS.md) 和
[skills/RESOLVER.md](skills/RESOLVER.md)。

概念创作阶段可加载
[skills/lens-cap-imagegen/SKILL.md](skills/lens-cap-imagegen/SKILL.md)
（中文：[SKILL.zh-CN.md](skills/lens-cap-imagegen/SKILL.zh-CN.md)），它负责研究、
提示词、品牌文化锚点、传闻限定和人工审批记录。生产阶段加载
[skills/lens-cap-production/SKILL.md](skills/lens-cap-production/SKILL.md)
（中文：[SKILL.zh-CN.md](skills/lens-cap-production/SKILL.zh-CN.md)）。两个 Skill
只负责路由和决策，实际文件生成仍由本仓库的 lens-cap CLI 完成；概念图
生成器本身不承诺逐字节复现。

Skill、docs 和示例是仓库／source distribution 的 companion 文档，不是
安装版 CLI wheel 的运行时依赖。只安装 wheel 时请从同一仓库另取 Skill；
这避免把某一家专有图像 SDK 假装成开源依赖。

## 开源边界

代码和模板采用 [Apache-2.0](LICENSE)。镜头品牌、商标、电影参考、用户图稿和下载的镜头盖模型不自动转移到该许可证；每个任务都应在 manifest 中记录来源、作者、许可证和修改说明。仓库默认忽略私有图稿、STL、3MF 和 G-code。

本地 `jobs/` 工作区也默认被忽略，因为其中通常包含尚未获准再分发的图稿和模型；只有完成 provenance／许可证审查后，才应使用 `git add -f` 发布特定任务。

本仓库不会自动登录 MakerWorld、调用 ChromaCanvas 或上传云端项目。仓库内的
`scripts/build_3mf.py`／`bin/lens-cap-3mf` 会在用户明确安装并指定 OpenSCAD（以及可选
Bambu 配置）时生成并校验 3MF；不会假装拥有缺失的桌面程序，也不会把平台登录或
云端切片当成核心功能。切片后的预览和实体卡合仍必须由用户检查并记录。

## 开发与测试

```sh
python -m pip install -e '.[dev]'
python -m pytest
python -m compileall -q lens_cap_pipeline
```

CI 运行同样的可移植检查，并在测试中验证颜色距离不会发生黑／象牙反转、重复运行字节稳定且不会隐式覆盖。OpenSCAD/Bambu 的集成检查是可选的，因为它们不是跨平台 Python 依赖。
