# 视频模型工厂架构

项目现在以“模型工厂”组织视频生成；LTX/Licon 仍保持原来的工作流与提交行为，新模型不能通过在 Licon 代码中增加 `if/else` 接入。

## 已注册模型

| 模型 ID | 管线 | 已知输出契约 | 提交方式 |
| --- | --- | --- | --- |
| `ltx23_licon_msr_v2` | LTX 2.3 / LiconMSR | 原有 50fps、1–4 张人物参考图、必需背景图 | ComfyUI 或 RunningHub |
| `minimax_h3_local_ref2va` | MiniMax H3 / Ref2VA（本地） | 4–15 秒整数时长、原生 24fps、带音频 | 本地 ComfyUI |

H3 的本地 Ref2VA 管线支持最多 9 张图片、3 个视频、3 个独立音频参考，上传文件总数最多 12。独立音频需要至少一项图片或视频参考；视频可选择把内嵌音轨作为对应视频参考的音频输入。H3 的提示词使用本地节点所需的 `<Picture N>`、`<Video N>`、`<Audio N>` 标签，而不是 LTX 的导演分镜格式。

## 分层边界

```text
Story / Beats
    -> GenerationService（按每段已解析模型分组）
    -> ModelRegistry
    -> model pipeline
         -> Planner
         -> PromptCompiler
         -> AssetBinder
         -> Validator
         -> WorkflowAdapter
    -> SubmitProvider
    -> Render history / final merge
```

通用层只处理模型选择、配置继承、调度和结果归档。模型层独立拥有提示词、参考素材、工作流节点和输出规格；因此 LTX 的 50fps、背景图、Licon 节点 28 等事实不会泄漏到 H3。

## 模型配置与工作流 Profile

`config.json` 的 `models.<model_id>` 声明模型、版本、模板位置和执行 Profile。H3 初始模板为 `templates/MiniMaxH3_Ref2VA_api.json`；`workflow_profile` 声明提示词、seed、时长、分辨率、H3 条件节点、输出节点和各媒体加载节点。

H3 Adapter 会清理 profile 中声明的旧参考加载节点，并按实际素材动态添加：

- 图片：`LoadImage -> ref_images.ref_image_N`
- 视频：`LoadVideo -> GetVideoComponents -> ref_videos.ref_video_N`
- 视频内嵌音频（可选）：`GetVideoComponents -> ref_video_audios.ref_video_N`
- 独立音频：`LoadAudio -> ref_audios.ref_audio_N`

参考文件会先复制到 ComfyUI `input/ai_cartoon_factory/minimax_h3/...` 命名空间，保存的 H3 图只包含 ComfyUI 可识别的相对路径。将来拿到经过验证的 H3 工作流时，应更新模板和该 Profile；不要改动通用服务或把节点号写入 UI。

### 可声明的参考素材开关

有些 H3 导出的图会额外经过“是否接入图片 / 视频 / 音频参考”的开关节点。它们不是 H3 的通用接口，因此不会写死在 Adapter 里。若验证后的工作流含有这类节点，可在该模型的 `workflow_profile.reference_controls` 中声明，例如：

```json
[
  {"node_id": "249", "input_name": "<导出图中的输入名>", "media_kind": "image", "enabled_value": true, "disabled_value": false},
  {"node_id": "251", "input_name": "<导出图中的输入名>", "media_kind": "video", "enabled_value": true, "disabled_value": false},
  {"node_id": "254", "input_name": "<导出图中的输入名>", "media_kind": "audio", "enabled_value": true, "disabled_value": false}
]
```

上例中的 249、251、254 对应当前观察到的完整 H3 图的图片、视频、音频控制节点；`input_name` 与开关值必须以最终导出的 API 图为准。Adapter 会根据本段实际绑定的素材数量自动写入开或关，因此替换成后续工作流时只需更新模板/Profile，而无需改 LTX 或通用调度代码。

## 项目默认与逐段覆盖

项目的 `ui_run_config.json.default_video_model_id` 是新分段的默认模型。`video_jobs.json` 的每一段保存：

- `model_override`：用户明确选择的逐段覆盖；
- `model_id` / `resolved_model`：下一次生成将使用的模型和版本快照；
- `generation_config`：模型语义配置，例如 H3 的素材清单、内嵌视频音频选择和提示词覆盖；
- `renders[]`：每一次提交的不可变成片记录；
- `active_render_id`：当前可合并成片所对应的真实 render。

修改模型不会把旧 LTX 提示词、素材槽位或 workflow 转译为 H3。旧成片保留在 `renders[]` 中供追溯，新的模型必须重新编译再提交。最终合并从 `active_render_id` 取文件，并在 `final_video_source_manifest` 中记录每段的 render ID 和真实模型。

合并前会比较实际媒体规格；混用模型，或出现不同分辨率、fps、音频存在状态/采样率时，会先将各段规范化为统一的 H.264/AAC 立体声文件再 concat。纯 LTX 且规格一致时仍优先走无损 concat。`final_video_normalization` 记录是否发生转换、来源规格与最终目标规格。

## Beat 策略

Beat 生成依据项目默认模型的能力策略提示模型：LTX 保持原有 15–30 秒写作规划；H3 使用 4–15 秒整数分段，并使用独立的 H3 Beat Writer Prompt，不继承 Licon 的背景必填、参考图槽位或 Event Flow 写法。共享 Beat 结构仍保留角色/场景等叙事事实；H3 专用的 `core_idea`、`shots`、`exclusions` 等内容保存于 `model_hints.minimax_h3_local_ref2va`，只由 H3 PromptCompiler 消费。已存在的 Beat 若不符合切换后的模型契约，会在编译时明确报错，避免无提示地改写故事节奏。

## 添加后续模型

1. 在 `config.json` 增加模型声明和一个验证过的 API 模板/Profile。
2. 新建 `generation/pipelines/<model>/`，实现模型自己的 Planner、PromptCompiler、AssetBinder、Validator 和 WorkflowAdapter。
3. 在 `generation/registry.py` 注册该 Pipeline，并填写 `ModelCapabilities`。
4. 只在模型自己的 Pipeline 中选择允许的提交平台；例如 H3 当前明确只走本地 ComfyUI。
5. 为动态素材绑定、时长/媒体限制、保存后重提和 render 溯源补测试。

不要假设不同模型具有相同 fps、时长、参考素材规则、提示词格式、音频能力、输出编码或工作流节点。
