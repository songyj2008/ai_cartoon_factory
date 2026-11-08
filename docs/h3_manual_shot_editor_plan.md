# H3 手动 Shot 时长编辑器修改方案

状态：方案已确定，尚未实施。本文是本次唯一新增文件，业务代码、提示词和项目数据未修改。

项目目录：`F:/work/comfyui/ai_cartoon_factory`。

**1. 本次范围与操作方式**

在 H3 全屏编辑器右侧、最新视频下方的红框位置增加“Shot 时长”面板。继续使用已有视频预览和原提示词编辑器。

- 按当前编辑稿的 Shot 数量显示控制行；每行显示 Shot 编号、说话人/台词或动作摘要、滑块、数字输入框、起止时间及操作按钮。
- 滑块和数字输入控制本 Shot 的持续时长，步长为 0.01 秒，所有起止及合计显示两位小数。数字输入和键盘操作用于精调，拖动用于快速调整。
- 调整 Shot k 后，从头重算全部区间；k 之前不变，k 的结束时间及后续 Shot 的起止顺延，其他 Shot 的持续时长不自动改变。
- 同步预览提示词内的 Shot 时间、At 时间、任务概述时长和已有明确的镜内时间标记，以及本段/受影响段/整片规划合计。
- 所有手动操作本地预览，不调用 LLM；点击现有“保存”时通过一个批量入口提交。
- 保留“重新规划时长”和网页修正入口；这些操作产生新稿后重新解析 Shot，不使用旧列表覆盖新稿。
- 本次不加入自动音轨识别、逐句声学对齐或另一套自动发声规划。

**2. 精度和 H3 整数时长规则**

当前项目的 H3 planner、validator、工作流适配器按 4–15 秒整数提交；Shot 可以使用小数。保留这个已验证的提交合同，不将小数直接传到旧接口。

- 内部使用整数百分秒累计，避免浮点累加导致 2.10 显示为 2.099999 或出现微小空隙。
- 单个 Shot 必须大于 0；0.01 秒是编辑数值精度，不代表生成视频可以做到百分秒精度。
- 编辑草稿可以暂时为 13.70 秒、超过 15 秒或不足 4 秒；界面给出具体差额，保留草稿，正式保存/生成前需合法。
- 正式版本要求 Shot 从 0.00 连续覆盖 4.00–15.00 秒中的一个整数总长，无重叠、无空隙。
- 例如草稿 13.70 秒，显示“合计 13.70 秒；可对齐到 14.00 秒，需增加 0.30 秒”，提供“补齐末 Shot +0.30 秒”。只有用户点击后才修改末 Shot；也可直接调整任意 Shot 达到整数。
- 不在保存时自动四舍五入、截断、压缩其他 Shot 或暗中拉长末 Shot；超过 15 秒不提供向上取整按钮，改为提示转移、拆新段或手调。
- 不足 4 秒时提示补足；可以显式补齐末 Shot 到 4.00 秒，也可以继续调整或转入其他段。整段为空时提供删除空段操作，不制造空镜或对白补时。
- 帧对齐后的媒体时长和音轨容器时长不反推为 Shot 计划时长；需要显示时以独立说明呈现。

**3. Shot 转移与新增下一段**

转移有两个选择维度：

| 维度 | 选项 |
|---|---|
| 移动范围 | 仅选中 Shot；从选中 Shot 起的全部后续 Shot（包含选中 Shot） |
| 目标位置 | 已有下一视频段的开头；在本段后插入一个新视频段 |

为保持现有剧情顺序，默认选“从选中 Shot 起的全部后续 Shot”。只移中间一个 Shot 时，预览明确显示新的播放顺序。

- 新建下一段时，原下一段及后续段的显示编号顺延；新段取得新的 beat_uid 和 segment_uid；移动 Shot 的稳定 shot_uid 保留。
- 新段完整深拷贝当前编辑稿中的参考配置：人物素材及身份、人物/场景图片、参考视频、参考音频、音色归属、素材顺序、用途及元数据。复用原素材文件，不复制文件内容。
- 保留当前实际选定的音频版本和路径，不在复制后按全局人物音色重新替换。尚未上传的素材先完成文件暂存和存在性检查，再进入批量提交；不提前覆盖分段绑定。
- 复制本段必要的生成参数（画幅、分辨率等）；新段是独立待生成内容，任务、视频历史及 OK 状态不继承为新段结果。
- 移入已有下一段时，以目标段参考配置为基底合并本段参考素材，保留目标段原有引用；按稳定素材身份去重，不按名字猜测。
- 图片、Subject、Video、Audio 及音色指向分别重新映射，包含参考视频内嵌音轨占用 Audio 编号的情况。对两个来源的 Shot 分别做来源绑定到目标绑定的映射，不能直接拼接相同编号的文本。
- 同一身份有不同音色绑定、参考用途冲突、来源身份无法唯一确定或合并后超过素材限制时，显示具体冲突；保留草稿，可改选“新建下一段”，不自动丢素材或替换目标段音色。
- 目标为非 H3 分段时不跨模型合并，提供插入 H3 新段的操作。
- 移动后两段均从 0.00 开始重新编号和累计，各自检查整数总长及 4–15 秒范围。目标段超限时继续在草稿中处理，不自动连锁移动后续所有段。
- 同一个批次中源段、目标段及新增段一起预览和提交；不能出现源段已删而目标段未写入的状态。

**4. 删除、撤销及提示词重组**

- 删除仅移除指定 Shot 及其所属对白、动作与局部音效，其他 Shot 的正文保持原文。
- 剩余 Shot 重新编号、重算起止和合计；连续性与引用清理按明确的结构关系执行。
- 提供本次编辑会话的撤销/重做；删除整段也先进入草稿预览，不弹反复确认窗口。
- 删除最后一个 Shot 后显示空段状态，提供撤销或将空段一起删除；正式保存不留下空段。
- 重新生成主体定义、参考保留关系及音色绑定等编译器管理区，按剩余 Shot 更新“出现于 Shot ...”。复制来的参考素材可以继续保留为已选素材，不据此强制人物出镜。
- 只改时间时保留原概述与声音描述，更新明确的时间数字。
- 删除/移动改变内容归属时，从保留 Shot 的已有结构字段重组概述与声音汇总，不能让被删/移走的剧情在全局汇总中继续作为当前段要求。没有可靠结构来源的旧概述保存在历史中，当前使用指向详细 Shot 的中性说明，不让 LLM补写剧情。
- 自定义全局说明、跨镜续说和镜头衔接若无法确定归属，标出需要核对的原文；不通过姓名替换、关键词猜测或全局字符串删除来修复。
- 明确的镜内事件时间按旧区间到新区间的相对位置映射，再统一到百分秒；跨镜时间点按整条时间轴映射。无法解析、移走端点或压缩后区间塌缩时返回具体冲突，不默默删除时间约束。
- 金额、台词中的数字、屏幕文字、参考素材描述和音频文件自身时长不作为镜头时间修改。

**5. 编辑数据、保存和后续生成**

当前编辑文本是创作底稿。新增结构化 Shot 文档只是可验证的编辑表示，不允许从旧 Beat 或旧缓存恢复已删内容。

建议 `segment.h3_shot_editor` 保存版本、当前提示词哈希、素材绑定哈希、编辑版本、稳定 shot_uid、原段归属、正文块、明确台词/角色引用及百分秒时长。起止由累计生成，避免两套独立可变起止值。

- 首次打开从当前编辑稿解析；已有三位小数时间在预览中按边界统一量化为两位，并展示变动，不在打开界面时直接改后台数据。
- 纯文本手改、换素材、LLM 修正或重新规划使解析基线失效；根据最新稿重建，不能拿过期的滑块列表回写。
- 本地草稿按 runtime + stable segment_uid 保存，绑定源/目标提示词、参考配置、时长和分段顺序版本。恢复基线不一致时显示差异，不自动覆盖新版本。
- 新增 preview 和 commit 入口：preview 只产生候选、变化列表、素材映射和校验结果；commit 在锁内重新验证所有基线后批量写入。
- 正式提交同步：`beats.json` 的当前 plot、dialogue_units、action_units/event_units 等已有活动内容及 H3 shots/model_hints，Beat 时长、总长；`video_jobs.json` 中分段顺序、uid 关联、手动 Shot 文档、h3_prompt_plan/source、generation_config.prompt_override/asset_manifest/duration_sec、job 提示词/时长/参考、requested_media_spec 和总长。
- 原始小说/完整剧情作为上下文保留；旧的活动 Beat 对白和旧 H3 计划必须被更新或失效，后续再生成以手动修改后的当前稿为准。
- 扩展现有 h3_editor_prompt/editor 来源机制，使手动 Shot 稿后续 prepare 继续保留已选 manifest。现有非 editor 分支会调用 attach_character_voices 重建人物参考音频，不能让新段复制好的音频在下次准备时丢失。
- 用户原先输入的目标总时长作为目标保留；当前规划总时长随编辑变化并显示差额。
- 同总时长但 Shot 分配变化也属于新版本，需要重新生成；不能仅比较 segment duration 判断是否有修改。
- 新内容标记 needs_regenerate，作废当前生成缓存及合成结果索引；工作流由现有 H3 编译路径按新数据重建，不能复用旧提示词或覆写历史渲染快照。
- 正在生成的受影响段禁止结构提交，保留草稿。仅显示编号变化的无关段继续按稳定 uid 归属任务；存在依赖旧下标的未结束任务时需等其结束，避免重编号造成结果写错位置。
- 已生成视频、任务记录和人工 OK 样本保持原来的版本归属；新编辑稿不会获得旧版本的 OK 资格。
- 使用现有 runtime pin、写保护和 video_jobs_lock。全部候选先在内存校验，文件写入使用临时文件、备份及提交记录；失败恢复成一致版本，不能把两次独立写文件称为原子提交。

**6. 对应代码文件**

下表列出计划修改位置，尚未修改这些文件。所有路径均为绝对路径。

| 文件 | 类型 | 职责 |
|---|---|---|
| `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/shot_editor.py` | 新增 | 纯结构层：Shot 文档、百分秒计算、文本解析、时间映射、编号、删除/移动候选及结构校验；不调用 LLM、不写文件。 |
| `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/shot_edit_service.py` | 新增 | preview/commit；源目标素材配置复制/合并；Beat/job 候选同步；指纹、锁、恢复及历史状态处理。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/h3_shot_editor_ui.py` | 新增 | 独立封装 SHOT_EDITOR_JS/CSS：滑块、数字输入、摘要、实时预览、整数补齐、移动/新段、删除撤销、草稿。 |
| `F:/work/comfyui/ai_cartoon_factory/generation/service.py` | 修改 | 增加 Shot 编辑预览/提交 facade；普通提示词保存时刷新或失效 Shot 索引；共用锁内保存和版本来源，不沿用旧分次 duration 保存链。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/api_routes.py` | 修改 | 新增 `/api/segment/shot-editor/preview`、`/api/segment/shot-editor/commit`；复用 runtime pin 和写保护，返回受影响段、提示词、Beat 编辑文本及统计。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/web_llm_ui.py` | 修改 | 在 `A.mountPromptRevision` 中挂载红框面板；加载最新稿/修正版后同步；Shot 模式下停用 `_savePromptDuration` 的提前单独保存，合计改为派生值。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/scripts.py` | 修改 | 导入新 JS；改造 `savePromptModal`、`saveH3GenerationConfig`、弹窗打开/关闭、重新规划及修正版应用协调；有结构草稿时统一批量保存，避免素材自动保存和旧响应覆盖。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/styles.py` | 修改 | 导入新 CSS，沿用主题、两位小数等宽数字、焦点和响应式布局，避免在红框固定小高度里挤入所有 Shot。 |
| `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/prompt_compiler.py` | 修改 | 共用 Shot 文档的时间格式和管理区重组；保留对白原文；防止旧 dialogue overlay 把删除/转移内容补回。 |
| `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/prompt_reconciler.py` | 修改 | 扩展现有稳定素材身份映射，支持源段到目标段的图片/主体/视频/音频及音色关系处理；保护台词和屏幕文字。 |
| `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/pipeline.py` | 修改 | 识别手动 Shot 当前稿来源，后续准备/重新生成时使用当前有效稿和 Beat，保留完整已选 manifest、避免重新 attach 音频；同段总长未变也重建受影响工作流。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/project_handlers.py` | 修改 | 从现有 `_sync_video_jobs_with_beat_durations` 提取可在内存复用的同步部分；保留其他入口行为，Shot 编辑不经过 int(round()) 处理小数。 |
| `F:/work/comfyui/ai_cartoon_factory/ui/beat_split_migration.py` | 修改 | 复用稳定 UID/仅显示编号重排的逻辑，支持本次批量新增或删除空段；新增 Shot 段走完整参考配置复制规则。 |

重要复用边界：现有 `_split_placeholder` 会清掉 asset_manifest 和 prompt_override，不能直接拿来创建用户要求的新段；现有 update_segment_duration 不更新 Shot 文本且会取整，不能直接拿来保存 Shot 合计。

以下模块以复用和回归验证为主，不为本功能改估时策略或扩大范围：

- `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/prompt_planner.py`
- `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/prompt_retimer.py`
- `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/validator.py`
- `F:/work/comfyui/ai_cartoon_factory/generation/pipelines/minimax_h3/workflow_adapter.py`
- `F:/work/comfyui/ai_cartoon_factory/services/speech_timing.py`
- `F:/work/comfyui/ai_cartoon_factory/services/video_experience.py`
- `F:/work/comfyui/ai_cartoon_factory/workflow/runninghub_history.py`

**7. 测试与验收**

新增：

- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_shot_editor.py`：百分秒累计、两位格式、时间映射、对白数字保护、删除编号、跨镜时间冲突。
- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_shot_edit_service.py`：已有下段合并、新建下段完整参考复制、素材编号冲突、源目标同步、旧稿不复活、指纹冲突、写入失败恢复、历史 OK 隔离。
- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_shot_editor_ui.py`：执行真实前端 JS，验证滑块/数字联动、显式补齐、本地草稿、撤销、过期响应和统一保存；不只匹配代码字符串。

回归现有相关测试：

- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_prompt_reconciler.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_modal_state_runtime.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_prompt_retimer.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_h3_planning_integration.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_video_experience.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_minimax_h3_pipeline.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_segment_duration_edit.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_prompt_modal_workflow_prompt.py`
- `F:/work/comfyui/ai_cartoon_factory/tests/test_prompt_save_status.py`

验收场景：

1. 七个 Shot 显示七行；Shot 1 从 2.10 调成 2.80，后续整体顺延 0.70，提示词和合计一致，其他 Shot 时长不变。
2. 合计 13.70 时不暗中取整；点击补齐后变成 14.00，末 Shot 明确增加 0.30。
3. 合计 15.80 时显示超出 0.80，允许继续编辑但不能正式提交；将尾部移入新段后两段重新检查。
4. 新段完整复制当前人物、场景、参考音频及音色关系；原下一段顺延，历史视频仍归属原 UID。
5. 移入已有下一段时，同号不同人物不会串绑；同人物不同音色冲突明确可见。
6. 删除 Shot 后对白随之移除，剩余编号/时长/Beat/工作流一致，再生成不会把删除内容恢复。
7. 调整后总时长恰好没变，仍识别为修改并重建工作流。
8. 切项目、切分段、多窗口、LLM 任务晚返回时，旧草稿和响应不能覆盖新版本。
9. 提交中途失败后两段和 Beat/job 文件恢复为同一版本；不会产生丢 Shot 或重复 Shot。
10. 只做本地工作流构建和测试，不为验收自动发起付费视频生成。

实施顺序：纯结构和时间编辑 → 后端预览/批量提交及素材复制 → 面板与现有保存协调 → 回归测试与界面验收。

改动量判断：中等规模，核心复杂度在跨段素材合并、Beat 当前内容同步和多文件一致性；滑块本身较直接。该功能提供可控的人工时间安排，视频模型是否精确执行仍需通过实际成片判断。
