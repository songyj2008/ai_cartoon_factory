# AI Cartoon Factory 教学视频讲解文案

## 课程定位

这节课讲解 `ai_cartoon_factory` 这个项目的核心实现。我们只扫描 Python 文件，不展开前端静态资源、模板 JSON 和素材文件。项目现在的定位很明确：它不是一个保留多条历史兼容路线的复杂工厂，而是一个围绕 LTX2.3 / LiconMSR 单工作流的 AI 视频生成工具。

一句话概括：

> 用户输入视频主题，系统生成故事和 Beats，再把每个 Beat 编译成 LiconMSR 视频提示词，绑定 1-4 张角色/物品参考图和 1 张背景图，最后 patch ComfyUI 工作流并提交生成视频。

核心路径是：

```text
主题 / 剧情
-> story.json
-> beats.json
-> video_jobs.json
-> workflows/licon_msr/*.json
-> ComfyUI / RunningHub
-> 分段视频
-> final video
```

讲解时建议把它拆成六层：入口层、项目上下文层、故事与 Beats 层、提示词编译层、素材绑定层、工作流提交层。

---

## 第一部分：先讲项目的设计哲学

开场可以这样讲：

这个项目最重要的不是某个函数，而是它的责任边界。AI 负责创作，代码负责把创作结果变成可执行、可验证、可追踪的结构。

项目里本地代码主要做确定性事情：

- 检查 prompt 不能为空。
- 检查时长、帧率必须是正数。
- 检查参考图数量必须是 1-4。
- 检查背景图必须存在。
- 检查 `totalframe = duration_sec * fps`。
- 检查 LiconMSR 节点只连接实际存在的参考图槽位。

它不会在运行时代替模型“修剧情审美”。比如节奏好不好、故事是否高级、镜头是否有电影感，这些属于 prompt、Project Bible、示例和用户输入，而不是硬编码规则。

这就是项目能保持清晰的原因：创作交给模型，结构交给代码，工作流交给模板。

---

## 第二部分：入口层，Gradio 应用怎么启动

入口文件是 `app.py`。

可以这样讲：

项目启动时，`app.py` 做三件事：

1. 配置素材索引服务。
2. 创建 Gradio 页面。
3. 启动队列和 Web 服务。

关键函数：

- `configure_services()`：初始化素材索引和项目目录。
- `allowed_paths()`：把项目目录、ComfyUI input/output 目录加入 Gradio 可访问路径。
- `create_app()`：构建 UI 并注册 API 路由。
- `main()`：安装 Windows 长任务信号保护，启动 Gradio。

教学时可以强调：入口层不关心故事怎么生成，也不关心 ComfyUI 节点怎么改。它只是把服务、UI、路由组织起来。

UI 的组装由 `ui/app_ui.py`、`ui/layout.py`、`ui/bindings.py` 完成：

- `ui/layout.py` 定义页面组件，比如主题输入、时长选择、故事编辑器、Beats 编辑器、分段视频面板。
- `ui/bindings.py` 把按钮事件绑定到后端函数，比如“生成故事”“拆分 Beats”“生成分段提示词”“生成视频”“合并最终视频”。
- `ui/app_ui.py` 只是一个薄封装：先 build layout，再 bind events。

这里的讲法可以是：UI 层是操作台，真正的生产线在 `pipeline/`、`prompt_builder/`、`workflow/` 里。

---

## 第三部分：项目上下文与运行目录

项目上下文在 `services/context.py`。

这层负责解决一个实际问题：每次生成视频都不是孤立的一次函数调用，它会产生 story、beats、workflow、视频、日志、最终合成文件。所有这些文件必须放到稳定的项目目录中。

核心概念：

- `BASE_DIR`：项目根目录。
- `CONFIG`：读取 `config.json`。
- `OUTPUT_DIR`：默认输出目录。
- `PROJECT_DIR`：当前运行项目目录。
- `ASSETS_DIR`：素材目录。
- `LICON_MSR_API_TEMPLATE`：当前主工作流模板路径。

运行目录会自动创建这些子目录：

```text
images/generated
images/cache
images/references
workflows/licon_msr
temp
videos
logs
final
```

讲解重点：

项目通过 `get_project_dir()` 让所有模块拿到当前项目目录。所以 story、beats、video_jobs、分段视频、最终视频都跟随当前项目切换。这也是 UI 能支持项目管理和归档的基础。

---

## 第四部分：Project Bible，约束角色和场景

`services/project_bible.py` 负责 Project Bible。

Project Bible 可以理解为“本项目允许出现哪些角色、哪些背景，以及它们对应哪些素材”。它的作用是给大模型上边界，让模型生成内容时不要随便发明角色和场景。

主要函数：

- `load_project_bible()`：读取当前项目的 `project_bible.json`。
- `save_project_bible()`：保存项目 Bible。
- `build_default_project_bible()`：根据素材索引生成默认角色和背景约束。
- `ensure_project_bible()`：如果没有 Bible，就自动创建。
- `project_bible_prompt()`：把 Bible 转成注入大模型的文本规则。

可以这样讲：

Project Bible 是连接“创作”和“素材”的桥。故事可以由模型发挥，但可见角色和场景必须落回这个项目已选择的素材集合。后面解析参考图时，也会靠 Bible 里的 `asset_identity_id` 和 `asset_background_id` 找到真实图片。

---

## 第五部分：故事生成，主题变成 story.json

故事生成入口在 `pipeline/story.py` 的 `generate_story()`。

执行过程：

1. 读取用户主题和目标时长。
2. 确保 Project Bible 存在。
3. 加载行业设定。
4. 构造系统 prompt 和用户 prompt。
5. 调用 `services.llm.call_llm()`。
6. 保存 `story.json`。

`story.json` 主要包含：

- `title`
- `summary`
- `story`
- `main_conflict`
- `ending`
- `target_duration_sec`

讲解重点：

这个阶段生成的不是视频 prompt，而是完整剧情。它的任务是确定故事主线、冲突、结局和信息密度。时长会影响故事复杂度：短视频少场景，长视频可以更多情节。

这里也能体现项目原则：故事 prompt 里会强调“不要随便改写用户主题”“不要新增无关重大任务”“核心信息尽量通过角色对白表达”。这些约束属于创作输入，而不是后面工作流 patch 的职责。

---

## 第六部分：Beats 拆分，story.json 变成可拍的视频段落

Beats 拆分入口在 `pipeline/beats.py` 的 `split_beats()`。

它不会直接在当前进程里写所有逻辑，而是调用脚本：

```text
scripts/story_script/generate_beats.py
```

流程是：

1. 读取 story 文本。
2. 构造 beat generation input。
3. 写入 `temp/beats_generation_input.json`。
4. 启动子进程调用 `generate_beats.py`。
5. 生成 `beats.json`。
6. 调用 `ensure_event_fields_on_beats()` 补齐结构字段。

`beats.json` 是项目里非常核心的中间产物。每个 Beat 不只是剧情文字，还要包含后续视频生成需要的结构字段：

- `scene_id`：使用哪个背景场景。
- `important_roles`：本段涉及的注册角色全集。
- `visible_roles`：第一帧就在画面里的角色。
- `reference_roles`：需要绑定到 LiconMSR 参考图槽位的角色，最多 4 个。
- `offscreen_speakers`：画外说话的人，不能进入参考图槽。
- `mentioned_roles`：只被提到的人，也不能进入参考图槽。
- `dialogue_units`：结构化对白。
- `action_units`：结构化动作。
- `event_units`：镜头事件流。
- `background_extras`：匿名背景群众。
- `estimated_duration_sec`：本段时长。

教学时建议重点讲一句：

Beat 是从“文学剧情”到“视频可执行任务”的转折点。后面所有角色绑定、提示词编译、工作流时长，都依赖 Beats 的结构。

---

## 第七部分：事件字段归一化，防止参考图绑定错

`pipeline/events.py` 的 `ensure_event_fields_on_beats()` 是一个关键稳定器。

它做的事情包括：

- 给 Beat 补齐 `id`、`order`、`node_id`。
- 确保 `visible_roles` 只来自 `important_roles`。
- 确保 `reference_roles` 是 `visible_roles` 的子集。
- 把电话、画外音、广播、门外声音等识别为 `offscreen_speakers`。
- 把不在画面里的角色移出参考图槽。
- 标准化 `dialogue_units`、`action_units`、`background_extras`。
- 如果没有总时长，就累加每个 Beat 的 `estimated_duration_sec`。

这里要讲透一个非常重要的点：

LiconMSR 的 80-83 节点是参考图槽位。只有真正出现在镜头里的角色，才应该占用这些槽位。被提到的人、电话里说话的人、门外声音，都不能绑定参考图，否则视频生成会误以为这个人必须出现在画面里。

这就是为什么项目把角色分成：

```text
important_roles
visible_roles
reference_roles
offscreen_speakers
mentioned_roles
```

这套结构不是为了好看，而是为了控制最终视频里“谁该出现、谁不该出现”。

---

## 第八部分：质量门与复杂 Beat 检测

质量门在 `pipeline/quality_gate.py`。

它扫描 story 和 beats 中的风险表达，比如：

- 推门进入。
- 从门外进入。
- 走进画面。
- 离开画面。
- 走廊、过道、门口等过渡空间。
- 起身、落座、走出等连续移动。

这些内容对视频模型不一定友好，因为它们容易导致人物从画外进入、身份不稳定、过渡镜头变成主事件。

复杂 Beat 检测在 `pipeline/beat_splitter/guard.py`。

它关注的是 Beat 内部角色关系是否太复杂，比如：

- 三个以上可见角色且多人说话。
- 同一个说话人频繁切换对话目标。
- 焦点人物组合频繁变化。
- 先发生物品交互，再切到另一个说话人。

讲解时可以这样说：

质量门不是审美裁判，它是“生成稳定性检查器”。它阻止的是那些容易让视频模型失控的结构，而不是判断故事好坏。

---

## 第九部分：提示词编译，Beat 变成 LiconMSR 导演分镜

提示词编译主要在 `prompt_builder/`。

核心链路：

```text
compile_beats_to_video_jobs()
-> compile_beat_video_prompt()
-> expand_beat_to_licon_prompt()
-> build_prompt_ir()
-> LLM 编译或 fallback_director_prompt()
```

`prompt_builder/beat_to_video_prompt.py` 负责把 Beats 转成视频任务列表。每个任务会包含：

- `segment_index`
- `segment_id`
- `part_id`
- `title`
- `duration_sec`
- `prompt`
- `scene_id`
- 各种角色字段

`prompt_builder/prompt_ir.py` 是这层的重点。它把 Beat 转成 PromptIR，也就是一个稳定中间表示。

PromptIR 包含：

- 当前段编号和时长。
- 场景 id 和场景显示名。
- 可见角色、参考角色、画外角色、提及角色。
- 剧情 plot。
- 对白、动作、事件流。
- 镜头约束。
- 输出契约。

可以这样讲：

PromptIR 的意义是“让 LLM 只负责把结构写成自然导演语言，不让它重新决定角色、场景、时长和对白”。这一步把不确定性压到最小。

如果 LLM 扩写不可用，`fallback_director_prompt()` 还能根据 PromptIR 生成一个确定性的导演分镜。

---

## 第十部分：素材绑定，角色和场景怎么找到真实图片

素材绑定在 `workflow/asset_resolver.py`。

它读取两个来源：

- `asset_index.json`：扫描得到的素材索引。
- `project_bible.json`：当前项目选定的角色和背景约束。

核心函数：

- `reference_images_for_roles(reference_roles, limit=4)`：根据角色找到参考图。
- `background_image_for_scene(scene_id)`：根据场景找到背景图。
- `prompt_with_character_descriptions(prompt, reference_roles)`：把角色身份描述拼到最终 prompt 前面。

讲解重点：

这个模块把抽象角色变成真实文件路径。比如 Beat 里写 `reference_roles = ["character_001"]`，它会通过 Project Bible 找到 `asset_identity_id`，再从 asset index 找到对应图片。

如果找不到指定角色或背景，它会退回默认素材。但根据项目当前架构，正常教学里要强调：推荐通过 Project Bible 正确选择素材，而不是依赖默认兜底。

---

## 第十一部分：分段执行，beats 变成 video_jobs

总编排在 `workflow/master.py`，分段执行在 `workflow/segment_runner.py`。

`workflow/master.py` 提供 UI 直接调用的高层函数：

- `generate_segment_prompts()`：只生成分段提示词和 workflow，不提交视频。
- `generate_final_video()`：根据选项生成或提交视频。
- `one_click_full_generation()`：一键从主题跑完整链路。
- `regenerate_segment_prompt_from_slot()`：重生成某一段提示词。
- `regenerate_part_from_slot()`：重生成某一段视频。

`workflow/segment_runner.py` 做真正的分段循环：

1. 调用 `ensure_event_fields_on_beats()` 标准化 Beats。
2. 调用 `compile_beats_to_video_jobs()` 生成分段任务。
3. 为每段解析参考图和背景图。
4. 拼接角色身份描述和导演分镜，得到 final prompt。
5. 调用 `build_and_optionally_submit()` 生成 workflow，必要时提交。
6. 每段结果写入 `video_jobs.json`。
7. 如果选择合并，则调用 `merge_segment_videos()`。

这里可以强调：

`video_jobs.json` 是“分段生产台账”。它记录每一段的 prompt、时长、帧数、参考图、背景图、workflow 路径、提交结果和视频路径。后续编辑某一段、重跑某一段、合并最终视频，都依赖它。

---

## 第十二部分：最核心的工作流 patch

`workflow/licon_msr.py` 是视频生成的核心执行模块。

它定义了 `VideoJobContract`：

```text
prompt
duration_sec
reference_images
background_image
fps
output_prefix
part_id
total_frames
```

其中 `total_frames = round(duration_sec * fps)`，默认 fps 来自配置，当前是 50。

`validate_video_job()` 会检查：

- prompt 非空。
- duration_sec > 0。
- fps > 0。
- total_frames > 0。
- reference_images 数量在 1-4。
- background_image 必填。
- 所有图片文件必须存在。

真正 patch 模板的是 `patch_licon_msr_workflow()`。

它只改这些节点：

- Node `5`：正向视频 prompt 文本。
- Node `50`：`totalframe`。
- Nodes `80`、`81`、`82`、`83`：1-4 张参考图。
- Node `84`：背景图。
- Node `28`：LiconMSR 输入连线。
- Node `20`：如果存在，则设置输出文件名前缀。

重点讲这个逻辑：

如果只有 2 张参考图，系统只连接 Node 80 和 81 到 LiconMSR 的 `1`、`2` 输入；`3`、`4` 输入会从 Node 28 里删除，对应的未用参考图节点也会从 workflow JSON 里移除。

这比“重复参考图”或者“塞占位图”更干净，因为它让工作流真实反映本段需要的角色数量。

---

## 第十三部分：提交后端，ComfyUI 与 RunningHub

提交后端通过 `workflow/submit_factory.py` 选择。

当前支持：

- `comfyui`
- `runninghub`

本地 ComfyUI 提交在 `workflow/submitters/comfyui_provider.py`：

1. POST 到 `/prompt`。
2. 拿到 `prompt_id`。
3. 轮询 history。
4. 定位输出视频。
5. 复制到当前项目 `videos/` 目录。

RunningHub 提交在 `workflow/submitters/runninghub_provider.py`，它会先处理媒体上传/缓存，再创建任务并轮询结果。

教学时不用展开所有 API 字段，只要讲清楚：工作流 patch 完后，后端只是负责“把同一个 API workflow 发到不同执行平台”。

---

## 第十四部分：视频合并

视频合并在 `workflow/video_merge.py`。

它会从 `video_jobs.json` 里找到每个 segment 的视频路径。如果路径缺失，则按 `part_id_*.mp4` 在 `videos/` 目录查找最新文件。

合并使用 `ffmpeg concat`：

1. 先尝试 `-c copy` 无损拼接。
2. 如果失败，再尝试 `libx264 + aac` 重编码。
3. 最终输出到 `final/final_video_时间戳.mp4`。

可以强调：合并不是生成模型能力，它是传统工程处理。AI 负责分段视频，ffmpeg 负责把片段串成最终视频。

---

## 第十五部分：完整调用链路复盘

可以用这段作为教学视频中间的总结：

用户点“一键全流程”后，主链路是：

```text
ui.bindings._one_click_direct
-> workflow.master.one_click_full_generation
-> pipeline.story.generate_story
-> pipeline.beats.split_beats
-> pipeline.events.ensure_event_fields_on_beats
-> workflow.segment_runner.run_licon_msr_segments
-> prompt_builder.beat_to_video_prompt.compile_beats_to_video_jobs
-> prompt_builder.prompt_ir.build_prompt_ir
-> workflow.asset_resolver.reference_images_for_roles
-> workflow.asset_resolver.background_image_for_scene
-> workflow.licon_msr.build_and_optionally_submit
-> workflow.licon_msr.patch_licon_msr_workflow
-> workflow.submitters.comfyui_provider / runninghub_provider
-> workflow.video_merge.merge_segment_videos
```

这条链路里最重要的三个契约：

1. Beat 契约：每段必须说明谁在场、谁参考、谁画外、用哪个场景、持续多久。
2. 素材契约：角色和场景必须能映射到真实图片。
3. 工作流契约：只 patch 固定节点，并保证 LiconMSR 连线与参考图数量一致。

---

## 第十六部分：教学视频建议结构

建议视频分为 8 个章节：

1. 项目是什么：一句话讲清 AI 视频生成链路。
2. 为什么是单工作流：解释 LTX2.3 / LiconMSR Direct。
3. UI 入口和项目目录：从 Gradio 到 runtime outputs。
4. Story 和 Beats：从创意文本到结构化视频段落。
5. Project Bible 和素材绑定：角色、背景、参考图如何落地。
6. PromptIR 和导演分镜：如何把 Beat 变成稳定视频 prompt。
7. Workflow patch：重点讲 Node 5、50、80-84、28。
8. 提交、追踪、合并：从 video_jobs 到最终视频。

每一章都建议用“问题 -> 代码位置 -> 数据产物 -> 下一步”的讲法。

---

## 第十七部分：适合口播的总收束

最后可以这样结尾：

这个项目的核心价值，不是简单地调用一次视频模型，而是把 AI 视频生成拆成了可检查的工程链路。Story 负责叙事，Beats 负责结构，PromptIR 负责稳定表达，Asset Resolver 负责素材绑定，LiconMSR patcher 负责把任务写进 ComfyUI workflow，Submit Provider 负责把 workflow 送去执行。

所以它的可维护性来自三个字：边界清楚。

模型可以自由创作，但必须在 Project Bible 和 Beat 结构里创作；本地代码不评价故事好坏，但会严格检查文件、时长、参考图数量和节点连线；视频生成可以切换 ComfyUI 或 RunningHub，但输入契约始终是同一个 LiconMSR 工作流。

理解这条主线之后，再看每个 Python 文件，就不会觉得它们是零散工具，而是一条从“主题”到“成片”的生产线。

