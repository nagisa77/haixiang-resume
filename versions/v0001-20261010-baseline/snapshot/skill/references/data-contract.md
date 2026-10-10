# 数据与追溯约定

以项目根目录为所有相对路径的基准；先跟随技能安装符号链接，解析到本项目的真实位置。JSON 使用 UTF-8，时间使用带时区的 ISO 8601；人员任职月份使用 `YYYY-MM`。稳定 ID 在创建后不重用，引用先验证存在。下列约定对应本项目现有脚本；若实现变化，读 `--help` 和现有有效样例后同步约定。

## 数据层

| 位置 | 用途与写入规则 |
| --- | --- |
| `sources/index.json` | 外层为 `schema_version`、`sources`；每条至少有唯一 `id`、`type`、项目内相对 `path`、`received_at` 和文件的 `sha256`。URL 只作元数据：保存可读取的原文/截图；无法访问时保存含 URL、时间和失败原因的本地记录，不建立仅含 URL 的来源，不把失败记录当作已读正文。 |
| `interviews/*.json` | 每轮不可变的用户原话。沿用 `id`、`recorded_at`、`kind`、`messages: [{role, text}]`；可加 `summary`、`extracted_fact_ids`、`unanswered_question`。若精确原话不在上下文中，只存明确标注的摘要，不伪称逐字稿。 |
| `profile/facts.json` | 外层为 `schema_version`、`updated_at`、`facts`。每条含 `id`、`category`、`statement`、`status`、`source_ids`、`notes`；可加结构化日期、项目、角色与替代关联。 |
| `profile/state.json` | 沿用 `current_focus`、`last_interview_id`、`pending_questions`、`question_backlog`、`next_session`、`preferences`、`unverified_items`、`active_job_id`、`latest_version_id`；更新 `updated_at`。问题记录用 `id`、`question`、`asked_at`、`status`。 |
| `jobs/<job_id>/source/` | 岗位原图、原文与必要的截取资料。截图 OCR 文本与原图并存；缺失内容不补造。 |
| `jobs/<job_id>/job.json` | 至少有 `id`、`source_ids`、`requirements`、`uncertainties`，可加公司、岗位与职责；每项要求含 `text`、匹配的 `fact_ids`、`match`。`match` 用 `confirmed` 或 `missing`，备注说明直接/可迁移证据及待确认材料。雇主要求与候选人事实分栏保存。 |
| `drafts/<draft_id>/resume.json` | 可迭代的结构化稿，保留 `status` 与事实引用；目标岗位和未决项另记在状态中。可修改草稿，不把草稿当作已归档交付。 |
| `versions/<version_id>/` | 不可覆盖的版本包：`resume.json`、`resume.pdf`、`resume.html`、`build-report.json`、`claim-review.md`、`manifest.json`、`changes.md` 及 `snapshot/`。 |
| `versions/index.json` | 外层为 `schema_version`、`versions`；每条含 `id`、`created_at`、`status`、`job_id`、`path`、`manifest_sha256`，由归档脚本追加。父版本在 manifest 中。 |

不要将仅在聊天中存在的回答留待下次再存。先保存原话，再提炼事实与状态；中途出错时，可以从原话重建派生内容。访谈文件整理完成后计算最终字节的 SHA-256，在 `sources/index.json` 追加 `type: "user_message"` 的记录，通常沿用访谈 ID 作为来源 ID，路径指向该访谈文件。事实的 `source_ids` 引用此 ID。后续补充建新访谈，不再修改已登记文件，否则旧摘要将失效；摘要不一致时先查明原因，不通过重新计算摘要掩盖未知改动。

## 事实状态

| 状态 | 含义 | 简历使用 |
| --- | --- | --- |
| `confirmed` | 用户明确亲述或明确认可该事实，且无未解决冲突 | 可用于正式稿；仍须保留原范围和归因 |
| `imported` | 从旧简历、附件或外部资料提取，尚未由本轮/后续采访核实 | 可放入 `draft` 草稿并列入待核实项；不能进入 `ready` 版本 |
| `proposed` | 助手的解释、候选措辞或待确认推断 | 不可作为正式事实使用；单纯润色可直接引用原有已确认事实 |
| `superseded` | 已被用户更正或新事实取代 | 保留历史与替代关联，不进入新简历正文 |

`confirmed` 不代表第三方审计通过。不要把用户自述说成外部核验结果。扫描件/OCR 错读、同名公司和不完整时间，记在 `notes` 与待核实项中。

更正一般新增事实并建立 `supersedes`/`superseded_by` 或等效关联，同时把旧事实设为 `superseded`。用户只是改措辞且含义不变时，不必制造新的事实。已归档版本中的旧快照不随现行档案变化。

## 岗位匹配与简历主张

每项要求至少记录：来源、要求文本、匹配事实 ID、匹配类型（直接证据/可迁移经历/无证据）与缺口。任何从 JD 得到的技能或责任都只属于岗位数据，直到用户提供自己的经历证据。

结构化简历使用以下字段，事实再通过 `profile/facts.json` 中的 `source_ids` 指向来源，不需要每个条目重复 `source_ids`。若额外填写显式 `source_ids`，它们也必须引用已登记来源，归档器会校验：

- 顶层：`name`、`headline`、`contact`（字符串数组）、`status`（`draft` 或 `ready`）、`version_label`；分别用 `name_fact_ids`、`headline_fact_ids`、`contact_fact_ids` 关联证据。
- 可选 `summary`：`{text, fact_ids}`。
- `sections`：每节 `{title, items}`；每个 item 用 `heading`、可选 `subheading`、`period`、`fact_ids`、`bullets`。公司、岗位、学历、证书、任职期间等标题/元数据的证据放在 item 的 `fact_ids`。
- 每个 bullet 用 `{text, fact_ids}`。复合句关联所有支持事实；不能只给一个总事实 ID 来遮盖缺少依据的子句。

生成器会读取并规范化显示字段，但不会改写或复制输入 JSON；必须把原始 `resume.json` 保留在草稿目录，归档脚本由此检查所有事实引用。`draft` 引用仅可为 `confirmed`/`imported`；`ready` 的所有引用必须为 `confirmed`。姓名、联系方式等也受此约束。主张粒度要足够细，能回答“这句话为什么可以这样写”。

对于“提升”“推动”“主导”等词，检查证据是否支持因果或权限。对结果保留主体：例如项目通过验收与个人负责验收资料可以分别成立，但不能自动改成个人独立完成验收。

## 归档完整性

一个版本包包含：

- 三种简历表现形式：机器可读 JSON、可预览 HTML、可投递或已明确标为草稿的 PDF。
- `build-report.json`：生成器标准输出的成功报告，包含状态、页数、字体与生成器 SHA-256，以及输入 JSON、输出 PDF/HTML 的 SHA-256；归档必需。
- `manifest.json`：脚本生成 `schema_version`、`id`、`created_at`、`status`、`job_id`、`parent_version`、`pages`、`visual_reviewed`、`fact_ids`、`source_ids`、`fact_statuses`、`files`（相对路径与 SHA-256）。`parent_version` 默认取同一 `job_id` 的最新版本；无岗位的基线单独延续。需要从另一已存在版本派生时用 `--parent-version <version_id>` 显式指定。
- `snapshot/`：当时的全部 `profile/`、`interviews/`、`sources/index.json`、来源原件及选定岗位目录；同时保存生成器、归档器和关键技能约定。未决项随 `profile/state.json` 的 `unverified_items` 一起快照，不要求向 manifest 添加脚本未提供的字段。
- `changes.md`：与前版相比增加/删除/改写了什么、为何适配该岗位、事实修正及仍需核实的内容。无前版时写明是初始基线。
- `claim-review.md`：归档必需的语义检查记录，简要列出重要表述与事实依据、职责和结果归因是否准确、哪些待核实内容已删除或保留为草稿。不要将文件哈希检查写成经历真实性验证。

## 生成、检查、归档

使用已安装 `reportlab`、`pypdf` 的 Python。下例 `ROOT`、`DRAFT`、`VERSION` 是参数占位，运行时替换为真实路径与新版本 ID；脚本路径相对技能目录。

```sh
python scripts/build_resume.py DRAFT/resume.json --output-dir DRAFT > DRAFT/build-report.json
python scripts/archive.py seal --project ROOT --draft DRAFT --version VERSION --changes-file DRAFT/changes.md --visual-reviewed
python scripts/archive.py verify --project ROOT --version VERSION
```

版本 ID 格式如 `v0001-20261010-baseline`，从版本目录分配下一个编号。岗位版本的 `seal` 加 `--job <job_id>`。`verify` 省略 `--version` 会检查全部版本。

确保草稿目录和 `resume.json` 已存在后执行生成命令，将标准输出保存为 `build-report.json`，确认退出成功及报告 `ok: true`。它默认最多两页，超限会报错且不会缩小字号。归档器固定只接收 1–2 页，不能靠生成器的 `--max-pages 0` 绕过归档页数要求。先修内容和分页，再重新生成。输入 JSON、PDF、HTML 或生成器变更后都需重新构建，旧报告不能混用。

归档前由代理逐页渲染并目视检查，实际看过后才传 `--visual-reviewed`；它是代理已完成检查的声明，不是让用户重复批准的流程。归档器校验简历事实引用、事实状态、全部已登记来源文件的 SHA-256、构建报告与当前 JSON/PDF/HTML/生成器的一致性、可提取的 PDF 文字及必需文件。哈希只证明文件一致性，不证明语义正确；归档前须逐项判断表述是否由引用事实支持，将结果写入非空的 `claim-review.md`，再执行 `seal`。

归档目标已存在就创建新 ID，禁止覆盖。归档完成后运行 `verify`，更新状态中的 `latest_version_id` 和 `active_job_id`。仅改版式也可建立新版本，注明版式变化。

归档后的修错采用新版本。若用户要求删除个人数据或历史，按其明确范围处理，并告知因此失去的追溯范围；不可变约定不凌驾于用户的删除要求。
