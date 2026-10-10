# AI Cartoon Factory 教学 PPT 大纲

## 01 标题

AI Cartoon Factory 项目解析  
从视频主题到 LTX2.3 / LiconMSR 成片

讲解重点：这是一个单工作流 AI 视频生成项目。

## 02 项目一句话

- 输入：视频主题、1-4 张参考图、1 张背景图。
- 中间：Story、Beats、PromptIR、video_jobs。
- 输出：ComfyUI / RunningHub 分段视频与最终合成视频。

讲解重点：先让观众知道整体链路，不急着进入代码。

## 03 六层架构

- UI 入口层：Gradio 操作台。
- 上下文层：项目目录、配置、运行文件。
- 故事层：主题生成 `story.json`。
- Beats 层：剧情拆成可拍段落。
- 编译层：Beat -> PromptIR -> 导演分镜。
- 工作流层：patch LiconMSR workflow 并提交。

讲解重点：后面所有文件都能归入这六层。

## 04 启动与 UI

- `app.py` 启动 Gradio。
- `ui/layout.py` 定义页面。
- `ui/bindings.py` 绑定按钮事件。
- `ui/app_ui.py` 组装 layout 和 bindings。

讲解重点：UI 是操作台，不是生成逻辑主体。

## 05 项目目录与配置

- `services/context.py` 维护 `BASE_DIR`、`CONFIG`、`PROJECT_DIR`。
- 每个项目有独立 story、beats、workflow、videos、final。
- `config.json` 决定 fps、模板路径、提交后端。

讲解重点：所有运行产物都围绕当前项目目录管理。

## 06 Project Bible

- 文件：`services/project_bible.py`。
- 定义允许出现的角色和背景。
- 连接素材索引与故事创作。
- 限制模型不要随意发明主角色和主场景。

讲解重点：Project Bible 是“创作边界”和“素材绑定”的桥。

## 07 Story 生成

- 文件：`pipeline/story.py`。
- 输入用户主题和目标时长。
- 注入 Project Bible 与行业设定。
- 调用 LLM 生成 `story.json`。

讲解重点：Story 阶段确定主线、冲突、结局，不直接生成视频。

## 08 Beats 拆分

- 文件：`pipeline/beats.py`。
- 调用 `scripts/story_script/generate_beats.py`。
- 输出 `beats.json`。
- 每个 Beat 包含场景、角色、对白、动作、事件流、时长。

讲解重点：Beat 是从文学剧情到视频任务的关键中间层。

## 09 角色可见性契约

- `important_roles`：本段相关角色全集。
- `visible_roles`：第一帧可见角色。
- `reference_roles`：需要参考图绑定的角色。
- `offscreen_speakers`：画外说话的人。
- `mentioned_roles`：只被提到的人。

讲解重点：只有 `reference_roles` 可以进入 LiconMSR 参考图槽。

## 10 质量门与复杂 Beat

- `pipeline/quality_gate.py` 检查高风险文本。
- `pipeline/beat_splitter/guard.py` 检查复杂角色焦点。
- 目标不是审美判断，而是视频生成稳定性。

讲解重点：本地规则只处理确定性风险，不修剧情风格。

## 11 PromptIR 编译

- `prompt_builder/beat_to_video_prompt.py` 生成视频任务。
- `prompt_builder/prompt_ir.py` 构造 PromptIR。
- LLM 只负责把结构写成自然导演分镜。
- fallback 可生成确定性导演分镜。

讲解重点：PromptIR 把不确定创作转成稳定中间表示。

## 12 素材解析

- 文件：`workflow/asset_resolver.py`。
- 从 Project Bible 和 asset index 找角色图。
- 从 scene_id 找背景图。
- 把角色身份描述拼进最终 prompt。

讲解重点：抽象角色必须落到真实图片路径。

## 13 分段生产台账

- 文件：`workflow/segment_runner.py`。
- 遍历每个 Beat。
- 生成 reference images、background、final prompt。
- 保存 `video_jobs.json`。

讲解重点：`video_jobs.json` 是重跑、编辑、合并的核心台账。

## 14 LiconMSR 工作流 Patch

- 文件：`workflow/licon_msr.py`。
- Node 5：prompt。
- Node 50：totalframe。
- Node 80-83：参考图。
- Node 84：背景图。
- Node 28：LiconMSR 输入连线。

讲解重点：不用占位图，未使用的参考图节点会删除。

## 15 提交与合并

- `submit_factory.py` 选择 ComfyUI 或 RunningHub。
- `comfyui_provider.py` POST `/prompt` 并等待结果。
- `video_merge.py` 使用 ffmpeg 合并最终视频。

讲解重点：执行平台可换，但输入 workflow 契约不变。

## 16 总结

- 模型负责创作。
- 代码负责结构、素材、校验、节点连线。
- Project Bible 控制角色和场景。
- Beats 控制视频段落。
- LiconMSR patcher 控制最终 workflow。

讲解重点：项目的可维护性来自边界清楚。

