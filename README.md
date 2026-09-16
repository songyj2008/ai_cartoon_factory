# AI Cartoon Factory

AI 视频生成工具。此仓库提供可运行源码、默认配置、工作流模板及界面所需资源，不包含个人项目、剧集、角色素材、生成视频、模型权重或 Python 环境。

## Windows 安装与启动

在 Anaconda Prompt 中进入下载并解压后的目录：

```bat
conda create -n ai_cartoon_factory python=3.13 -y
conda activate ai_cartoon_factory
python -m pip install -r requirements.txt
conda install -c conda-forge ffmpeg -y
python app.py
```

浏览器访问 http://127.0.0.1:7860 。FFmpeg/ffprobe 用于视频合并及媒体信息读取，需能从 PATH 调用。首次启动会自动建立空的运行目录；请自行导入角色和背景素材、创建项目。

## 生成服务配置

- 在应用设置中填写自己的 DeepSeek API Key；使用 RunningHub 时还需填写自己的 RunningHub API Key。密钥由应用在本机保存，不包含在仓库中，不要直接把明文密钥放入环境变量或提交到 Git。
- 默认配置保留当前本地版本的模型注册信息：LTX 2.3 / LiconMSR 与 MiniMax H3 / Ref2VA。默认提交后端为本地 ComfyUI，地址为 `http://127.0.0.1:8188`。
- 本地生成前，需另行安装 ComfyUI、对应自定义节点及模型权重。按照 `templates/` 内选用的 API 工作流配置节点和模型；此源码包不会自动下载模型。
- `config.json` 中的 `comfy_input_dir`、`comfy_output_dir` 及 `submit_backends.comfyui` 下的路径，均需按本机 ComfyUI 位置配置；默认相对路径假设 ComfyUI 与应用目录同级。
- 使用 RunningHub 时，在设置中选择该后端，并确认配置里的工作流 ID 在自己的账号中可用，必要时更换为自己的工作流 ID。云端生成需要相应服务账号和额度。
- `python_executable` 留空即可使用当前 Python 环境。LTX 配置默认 50 fps；不同模型按各自工作流契约运行。

## 可选桌面窗口

```bat
python -m pip install -r requirements-desktop.txt
python desktop_app.py
```

Windows 桌面窗口需要 Microsoft Edge WebView2 Runtime，请提前安装。推荐先使用 `python app.py` 验证环境。

## 发布范围

保留 `generation/`、`pipeline/`、`prompt_builder/`、`services/`、`ui/`、`workflow/`、剧情脚本、提示词配置、工作流模板、静态资源和品牌公开资源。`projects/`、`episodes/`、`outputs/`、`assets/`、缓存、教学演示文件、安装器和开发工具均不在发布包内。

更新前请备份自己的 `config.json` 与项目数据。本仓库发布历史从经过检查的干净源码版本开始，不包含旧项目、素材和历史密钥。不要将旧仓库历史合并或推送回本仓库。
