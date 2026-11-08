# AI Cartoon Factory

本地运行的 AI 视频创作工具，提供剧情与小说分段、Beats、视频提示词、参考素材管理、连续场景编辑、生成任务与视频历史。仓库只发布源码、文档、提示词规则、工作流 JSON 和必要的前端静态文件。

**不包含视频、图片素材、音频、资产库、个人项目、日志、模型权重、安装器或打包产物。** 首次使用需要自行准备素材及生成服务。

## Windows 快速开始

打开 **Anaconda Prompt**，执行：

```bat
git clone https://github.com/songyj2008/ai_cartoon_factory.git
cd ai_cartoon_factory
conda create -n ai_cartoon_factory python=3.13 -y
conda activate ai_cartoon_factory
python -m pip install -r requirements.txt
conda install -c conda-forge ffmpeg -y
copy config.example.json config.json
python app.py
```

打开 [本机界面](http://127.0.0.1:7860)。再次启动可在 Anaconda Prompt 中运行根目录 `start_windows.bat`，脚本使用 `ai_cartoon_factory` conda 环境，缺少 `config.json` 时复制示例，已有配置不会覆盖。

完整步骤、ComfyUI 路径配置、Chrome 扩展、桌面模式、升级和排错见 [Windows 使用指南](docs/windows.md)。日常创作流程见 [使用说明](docs/usage.md)。

## 配置与生成

- `config.example.json` 是公开默认配置；复制得到的 `config.json` 是本机配置，Git 忽略它。更新示例不会自动覆盖已有设置。
- 在应用设置中填写自己的 DeepSeek API Key；选择 RunningHub 时配置自己的 RunningHub API Key 和可用工作流。密钥通过应用在本机加密保存，不能直接设置为明文环境变量，也不要写入仓库。
- 默认示例使用本地 ComfyUI `http://127.0.0.1:8188`。本项目不安装 ComfyUI、自定义节点或模型，须先在 ComfyUI 中确认工作流可运行。云端生成需要对应账号、权限和额度。
- 当前源码保留 LTX / LiconMSR 与 MiniMax H3 / Ref2VA 模型注册；具体模板和节点映射以示例配置与 `templates/` 为准。LTX 默认 50 fps。网页生成渠道有独立的素材和时长限制，见 [渠道说明](docs/video_channels.md)。
- 项目架构说明中记录的 `Ltx2.3_Licon-MSRV1_api.json` 在当前目录中缺失；现有配置指向 `Ltx2.3 Licon-MSRV2-bfs_api.json`。此处如实记录现状，发布不自动迁移模板或改写生成逻辑。

## 源码目录

| 目录 | 内容 |
| --- | --- |
| `generation/`、`workflow/` | 分段、模型管线、工作流与生成渠道 |
| `pipeline/`、`prompt_builder/` | 剧情、Beats 与提示词构建 |
| `services/`、`ui/`、`web/` | 本地服务、Gradio 界面与静态代码 |
| `templates/`、`profiles/`、`prompt_profiles/` | 工作流 JSON、创作规则和提示词配置 |
| `tools/chrome_prompt_bridge/` | 可选 Chrome 扩展源码 |
| `tests/`、`docs/`、`scripts/` | 测试、说明与维护脚本 |

`projects/`、`episodes/`、`outputs/`、`assets/` 和 `logs/` 是本机数据，不能提交。品牌公开说明和验签公钥属于应用文件；私钥不得提交。

## 开发检查

```bat
conda activate ai_cartoon_factory
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node --test tools/chrome_prompt_bridge/tests/*.test.cjs
node --test tests/*.test.cjs
```

Node.js 仅用于 JavaScript 检查。离线测试不代表真实网页账号、云端工作流或最终生成画面已验证。更新与提交约定见 [源码发布说明](docs/publishing.md)。
