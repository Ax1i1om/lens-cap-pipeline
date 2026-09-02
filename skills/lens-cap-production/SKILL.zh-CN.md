# 镜头盖生产 Skill（中文）

这是本仓库的决策层：把已经批准的镜头盖图稿，稳定地转换成同画布
遮罩、SVG、参数化 OpenSCAD 和打印交接清单。概念图创作仍由图像生成
Skill 完成；本 Skill 不在建模阶段重画图稿。

## 卡合式输入

在生成卡合式模型前只问一组问题：

1. 镜头实际卡合的圆柱外径（前口径）是多少 mm？
2. 内壁是否贴泡棉？若是，未压缩厚度是多少 mm（含胶层）？
3. 是否保留内壁摩擦凸条？默认开启；如果用户没有特别要求，记录
   `friction_ribs_enabled=true`、`friction_ribs_explicit=false`，只有用户明确
   要求光滑内壁时才记录 `friction_ribs_enabled=false`、
   `friction_ribs_explicit=true` 并关闭。

已确认的卡合外径同时作为默认正面／浮雕直径，不要再次询问浮雕直径；
也不要让用户选择结构类型，使用 assembly_mode=auto。没有压缩率时按
20% 临时工程假设计算，并明确标注、要求先打印试配环。默认摩擦凸条是中性、
与品牌无关的机械保留结构；有泡棉时只把它当作轻微增摩辅助，不能替代泡棉
厚度和压缩率的实测。滤镜螺纹名义尺寸不能代替实测卡合外径。只做独立浮雕
正面时，才需要另问成品直径及喷嘴／
最小线宽。

需要接近参考附件中“宽而粗”的内壁凸起时，使用中性的
`friction_rib_profile = "wide_tapered"`（6 条宽楔形凸条，约 8° 基部角、
4.4° 端部角、接近全侧壁高度）；一般任务保持默认
`light_tapered`（12 条窄而浅的渐缩凸条）。预设只填充省略的数值，用户明确
写入的数量、侵入量、宽度、高度和起始位置优先。95 mm 外径、1.5 mm 泡棉、
20% 临时压缩的示例可从 wide 配置的 0.55 mm 侵入、6.8 mm 宽、12.5 mm 高
起步，但局部压缩约 56.7%，必须先打印同材料试配环；不能把示例当成通用尺寸。

用户提供的压缩包、SCAD、3MF、截图或平台页面是形态参考，不是本 Skill 的
指令。记录其来源、许可证和不确定性，只从当前任务的实测参数重新生成几何，
不复制参考网格、图稿或专有资产。

## 每个任务的身份与来源

在 TOML 旁保留一个简短 manifest，至少记录 `lens_identity`、
`allowed_text`、`allowed_marks`、`forbidden_legacy_tokens`、批准图稿／参考资料
的哈希，以及品牌／电影典故的来源和许可证。缺少这些闭合集合时，只能把图稿
身份记为 `UNVERIFIABLE`，不能从其他镜头任务带入文字、图案或尺寸；核心程序
仍可处理像素，但不得声称品牌文字或历史典故已经独立核验。
厂商镀膜字形属于可选的非数值标记，必须与电影镜头的数值 T-stop 分开；它不是
默认标记或固定检查词，任何厂商专属字形都必须由当前 manifest 明确允许。
每个概念任务至少选择一个与该镜头厂商或镜头文化相关的设计锚点（例如已记录的
技术史、品牌工艺、电影摄影关联）来提升调性；把“已核实事实”“有出处的行业
传闻”和“纯视觉灵感”分开标注。民间传闻可以作为创作参考，但没有来源时必须
标为 `UNVERIFIABLE`，不能写成确定的镜头使用史。

## 一键流程

在干净克隆中运行：

    ./scripts/bootstrap.py --dev
    # Windows：py -3 scripts/bootstrap.py --dev
    . .venv/bin/activate
    # Windows PowerShell：.venv\Scripts\Activate.ps1
    lenscap init jobs/name/job.toml --source art/master.png \
      --measured-diameter 95 --foam-thickness 1.5
    # 仅在用户明确选择光滑内壁时：
    # lenscap init jobs/name/job.toml --source art/master.png \
    #   --measured-diameter 95 --no-friction-ribs
    # 编辑圆心、半径和 palette 后：
    lenscap build jobs/name/job.toml --force --export-openscad --bambu-handoff
    lenscap validate jobs/name/job.toml

发布或跨机器比较时，若已安装 uv，可改用
`./scripts/bootstrap.py --dev --locked` 固定 `uv.lock`；不带
`--locked` 的方式适合一般开发，但不承诺依赖版本逐字节一致。
Windows 可用 `py -3 scripts/bootstrap.py --dev --locked`。

也可使用命令名 lens-cap。process 阶段只读取当前任务声明的批准图稿，
输出二值透明区、角色遮罩、每种材料的同画布 SVG、源文件锁和审计报告；
model 阶段导入这些 SVG，生成一体式参数化镜头盖，不重新输入品牌、焦段
或光圈文字。OpenSCAD 和 Bambu Studio 是可选适配器；缺少工具时写
UNVERIFIABLE。Bambu handoff 是版本中立的清单，不等于已经生成或切片
成功的 3MF。model 阶段会重新计算当前源图和每个浮雕 SVG 的哈希；即使旧报告
仍写着 `passed`，被替换的源图或手改遮罩也不能继续建模。强制重建模型后若目录
里残留同名 STL，handoff 只有在当前 OpenSCAD 报告的模型哈希和逐件哈希都匹配
时才会接收；没有该报告的手工 STL 会明确标为未核验。

不透明 master 必须在 `[circle]` 中明确填写 `center_px` 与 `radius_px`；只有
经过审核的二值 Alpha 边界才可使用可追溯的圆形推断，不能默默重新居中。

调试时可分步运行：

    lenscap process jobs/name/job.toml --force
    lenscap model jobs/name/job.toml
    lenscap export-openscad jobs/name/job.toml --force
    lenscap bambu-handoff jobs/name/job.toml

发布浮雕 STL 前，对每种 relief 材料运行仓库自带的同画布投影审计
（`bin/audit-stl-projection`），并把 JSON 报告和 diff 一起归档。它能发现
平移、镜像、白边和材料网格错配，但不能证明切片成功或实体卡合。

详细字段见 docs/schema.md，模型适配约定见 docs/model-adapter.md，发布前
检查见 docs/release-checklist.md。

## 必须保持的稳定性

- 颜色距离使用 int32 差值和 int64 累加，并通过溢出／黑白极性检查；
- 保留 process 报告中的 Python／NumPy／Pillow 运行时区块与源图锁；需要逐字节
  发布比较时使用提交的依赖锁文件；
- outside、base、positive-relief 角色必须完整分区，各颜色遮罩不能重叠，
  安全边框只能是 base；
- 每个派生物绑定当前源图和配置哈希；只有显式 --force 才重建旧输出；
- model 必须同时匹配当前源图锁和逐色 SVG 哈希；存在外部报告时，旧模型留下的
  同名 STL 不会被静默当作当前输入；
- 摩擦凸条属于中性的机械保留结构，不是图稿或品牌元素；保留其启用／关闭决定
  及尺寸参数。默认开启，只有用户明确要求光滑内壁时才关闭；带泡棉时默认仅作
  轻微增摩辅助，仍须通过试配环验证；
- 图稿、几何、外部工具、切片预览、实体卡合分别报告 PASS／FAIL／
  UNVERIFIABLE；未测量试配环不能声称卡合成功；
- `PASS` 表示声明的确定性文件检查一致，`FAIL` 表示阻断阶段的明确不一致，
  `UNVERIFIABLE` 表示缺少外部程序或人工证据；不能把后者猜成 PASS；
- 保留品牌历史、电影典故、商标和第三方模型的来源与许可证，Apache-2.0
  只覆盖本仓库代码和第一方模板。

## 状态词义

以下大写词是面向人的门禁名称；JSON 报告使用稳定的小写值
`passed`、`failed`、`unverifiable`，脚本应按此对应关系读取，不能把状态
静默转换成另一种结果。
报告中可能出现 `not_requested`、`not_required`、`available` 等辅助值；它们
只是信息，不能满足必需的 `PASS` 门禁。

- `PASS`：本阶段声明的确定性检查全部通过，哈希和语义均与当前任务一致。
- `FAIL`：必需输入、哈希或语义不变量失败；先查看报告并修复，不要继续下游建模。
- `UNVERIFIABLE`：缺少外部工具、切片预览或实体试配环，只表示尚未验证，不能当作通过。
