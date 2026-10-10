# 分段视频生成渠道

“分段模型”和“生成渠道”是两个独立选择。分段原有的 LTX/H3 模型、时长和素材不会因选择渠道而被改写。在分段卡片点击“重新生成”后，先选择 ComfyUI、RunningHub 或豆包网页版；不满足当前渠道条件的选项显示原因。

## 豆包网页版

- 使用已有 Chrome 扩展的本机配对配置。更新代码后重启项目，并在 `chrome://extensions` 重新加载 `tools/chrome_prompt_bridge` 扩展。扩展使用一个专门的豆包标签页自动完成网页操作。
- 免费账号渠道接受 **4–10 秒整数时长**、非空视频提示词和至少一张本地参考图片。目标比例固定为 **16:9**。当前只传图片，不传 H3 的视频或音频素材；这类分段会显示不可用原因。
- 在网页自动点击“视频生成”、设置比例与时长、通过左下角加号上传参考图、填写提示词并发送。扩展继续检查新视频与下载状态，将下载文件写入分段渲染记录。不会自动重发发送状态不明的任务。
- 默认账号每天最多预留 10 次，额度在本机队列中跨项目计算。可在 `config.json` 的 `web_video_accounts` 中为渠道设置稳定的账号标识；默认标识为 `default`。发送前明确失败的任务不占额度；已发送或结果不确定的任务占额度。
- 队列数据库位于用户的 `LOCALAPPDATA/AICartoonFactory/web_video_bridge`。同一项目安装位置使用同一个数据库；系统本地日期决定每日额度。

网页 DOM 会随豆包更新。扩展在比例、时长、上传预览或发送确认无法验证时停止任务并记录错误/不确定状态，不会盲目点击或自动改用其他渠道。首次使用和网站改版后，应在已登录的豆包页面用一个 4–10 秒分段做端到端联调，特别检查预览上传和结果下载控件。

## 扩展新的渠道

1. 在 `generation/channels/` 新增渠道模块，导出 `channel`，实现 `id`、`label`、`uses_workflow`、`unavailable_reason(segment, payload)`、`submit(service, segment_index, wait=...)`。网页渠道还可复用 `services/web_video_bridge.py`，实现 `assets(segment)`、`duration(segment)`、`prompt(segment)`；按需定义 `daily_limit`。
2. 将模块路径加入 `generation/channels/registry.json`。打包脚本会收集渠道子模块和注册表。
3. 网页渠道在 `tools/chrome_prompt_bridge/video/` 新增单独的网页 DOM 适配器，并在 `video/channels.json` 注册渠道 ID、页面 URL、域名和脚本路径。Chrome 扩展 `manifest.json` 的 host permissions 需包含新网站。
4. 渠道适配器必须验证网页操作后的可观察状态，再回报提交或完成。核心分段服务、原有工作流模板和其他渠道不需要加入网站分支。
