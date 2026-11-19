# Windows 安装、启动与更新

## 1. 准备环境

使用 Windows、Miniconda 或 Anaconda、Git。源码浏览器模式使用 Python 3.13；本机检查环境为 Python 3.13.13、Gradio 6.15.0。需要媒体探测或视频合并时安装 FFmpeg（包含 ffprobe）。Chrome 扩展要求 Chrome 120 或以上；JavaScript 测试另需 Node.js。

这些步骤在 **Anaconda Prompt（CMD 语法）** 执行。建议将源码放在可写的普通目录，例如 `D:\work\ai_cartoon_factory`。应用和 ComfyUI 可以使用不同的 Python 环境；不要把 ComfyUI 的整套依赖装入应用环境。

```bat
git clone https://github.com/songyj2008/ai_cartoon_factory.git
cd ai_cartoon_factory
conda create -n ai_cartoon_factory python=3.13 -y
conda activate ai_cartoon_factory
python -m pip install -r requirements.txt
conda install -c conda-forge ffmpeg -y
copy config.example.json config.json
python --version
ffmpeg -version
ffprobe -version
python app.py
```

已存在环境时跳过创建；已有 `config.json` 时不要再执行复制。浏览器打开 [http://127.0.0.1:7860](http://127.0.0.1:7860)，终端保持运行。按 Ctrl+C 停止。首次启动会建立空数据目录，不附带演示项目或资产库。

后续在 Anaconda Prompt 中进入源码目录，执行 `start_windows.bat` 即可。脚本只启动服务，在浏览器中手动打开地址；缺依赖时先重新安装 requirements。

## 2. 配置文字模型

进入应用设置，填写自己的 DeepSeek API Key，保存后再生成剧情或提示词。模型名称由 `config.json` 的 `deepseek_model` 指定，需与自己的服务权限一致。

密钥由应用加密并绑定本机保存，Windows 使用当前用户环境存储。不要把 API Key 写进 JSON 或 Git，也不要把明文填到 `DEEPSEEK_API_KEY`、`RUNNINGHUB_API_KEY` 环境变量；当前读取逻辑要求应用生成的加密格式。迁移电脑后在新电脑的应用设置重新填写。

也可使用网页 LLM：按下节安装扩展，再在设置选择相应服务。只打开项目页面不会自动建立模型网页登录状态。

## 3. 配置视频生成

### 本地 ComfyUI

先单独启动 ComfyUI，确认所选 `templates/` 工作流依赖的节点、模型和编码器已安装，且能在 ComfyUI 运行。本应用只负责编译并提交工作流，不包含 GPU 推理环境、模型权重或自定义节点。

在应用选择 ComfyUI 渠道，检查 `config.json`：

| 字段 | 示例与用途 |
| --- | --- |
| `comfyui_url`、`comfy_url` | `http://127.0.0.1:8188`，保持一致 |
| `comfy_input_dir` | `D:/ai/ComfyUI/input` |
| `comfy_output_dir` | `D:/ai/ComfyUI/output` |
| `submit_backends.comfyui.url` | 与实际服务地址一致 |
| `submit_backends.comfyui.input_dir`、`output_dir` | 与上面的实际目录一致 |
| `python_executable` | 留空，使用当前 conda 环境 |

JSON 路径可用 `/`，如使用反斜杠必须写成 `\\`。默认 `../ComfyUI/input` 相对于应用根目录，仅适用于两者目录同级的布局。不要指向不存在的目录。修改 JSON 后重启应用。

示例公开配置使用当前仓库已有的 LTX V2 与 H3 模板；不要把不同工作流的节点编号混用。若需要项目规范中的 V1 模板，应先提供并核实该模板，不能直接重命名 V2 文件。

### RunningHub

在应用设置保存自己的 RunningHub API Key，选择 RunningHub 渠道。核对 `models.<模型ID>.execution_profiles.runninghub.workflow_id` 是自己有权限运行的工作流，节点契约与本地模板一致。默认 ID 不保证所有账号均可调用。

云端排队与渲染独立于本地文字准备；视频提交成功不等于渲染完成。失败时先查看对应任务的错误，确认额度、工作流、节点和素材格式，再决定是否重试。

## 4. Chrome / Edge 扩展

1. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`，启用开发者模式。
2. 点击“加载已解压的扩展程序”，选择 `tools/chrome_prompt_bridge` 目录；不要选择整个项目，也不要加载旧 ZIP 包。
3. 在应用“设置 → 模型配置 → 网页 LLM / 浏览器扩展”查看本机后台地址和配对码。
4. 打开扩展侧边栏，填写该地址和配对码并启用连接。
5. 打开并登录所需模型网页，按扩展配置绑定服务，确认应用显示在线后提交文字任务。

当前清单版本为 0.4.59，服务包括 DeepSeek、ChatGPT、豆包和千问。各浏览器可填写不同实例名称，独立登录网站。设置页选择文字主浏览器和各网页视频渠道默认浏览器，分段编辑器支持单独保存执行浏览器和改派尚未领取的视频任务。文字主浏览器切换时等待原任务收尾，离线后可手动切换。同一网站账号的账号标识应填写相同值以共享额度统计。网页改版、登录、验证码和额度问题需要在浏览器处理。扩展声明 debugger 等权限用于受控输入，安装或更新时以 `manifest.json` 为准。多浏览器真实账号完整联调仍需验证。

更新源码后须在扩展管理页点击“重新加载”，再刷新已打开的模型页面。仅重启 Python 不会更新浏览器内容脚本。已尝试发送但状态不明确的任务，不要连续重试发送；先核对原会话和任务回执。已确认回传的文字任务仅清理扩展创建的对应页，保留后台结果，不清空其他任务。

扩展详细行为和历史变更见 [扩展说明](../tools/chrome_prompt_bridge/README.md)，网页视频限制见 [生成渠道](video_channels.md)。

## 5. 可选桌面窗口

先确认浏览器模式正常，然后执行：

```bat
conda activate ai_cartoon_factory
python -m pip install -r requirements-desktop.txt
python desktop_app.py
```

Windows 需要 Microsoft Edge WebView2 Runtime；源码仓库不携带安装器，应自行安装。桌面模式端口忙时会尝试其他端口；浏览器模式按指定端口启动。建议先使用浏览器模式排查依赖，不需要为此打包 EXE。

## 6. 更新与数据备份

更新前结束当前运行，备份 `config.json`、`projects/`、`episodes/`、`outputs/`、`assets/`。网页任务队列与配对数据另存于 `%LOCALAPPDATA%\AICartoonFactory`；密钥绑定当前机器，不随 Git 迁移。

```bat
git pull --ff-only origin master
conda activate ai_cartoon_factory
python -m pip install -r requirements.txt
python app.py
```

本次起 `config.json` 不再受版本管理，公开默认项位于 `config.example.json`。从旧提交升级时，先将自己的配置复制到仓库外备份；Git 可能因本地配置修改拒绝更新，或删除旧版被跟踪的配置。更新后将备份恢复为 `config.json`，再手动对照示例补充新字段。不要用示例覆盖自己的配置。其他本地代码改动应先提交或备份；不要使用强制重置解决冲突。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| 找不到 conda | 使用 Anaconda Prompt，确认已安装 Conda |
| 缺少 config.json | 从根目录复制 `config.example.json`，或运行启动脚本 |
| 缺少 Python 模块 | 激活 `ai_cartoon_factory`，用 `python -m pip install -r requirements.txt` |
| 7860 被占用 | 关闭自己原来的项目窗口，或在 CMD 执行 `set AI_CARTOON_FACTORY_PORT=7861` 再启动；访问新端口，扩展地址也要更新 |
| 网页无法连接 | 查看终端是否启动成功，确认地址与实际端口一致 |
| ComfyUI 拒绝任务 | 查看节点验证错误，核对模板、自定义节点、权重、输入路径 |
| 找不到 ffmpeg/ffprobe | 在应用环境安装 FFmpeg 后重启终端，检查两个命令均可执行 |
| 密钥与机器不匹配 | 在应用设置重新填写保存；不要复制其他机器的加密值 |
| 扩展离线或读取失败 | 确认后端运行、配对和登录有效，重新加载扩展并刷新模型页 |
| 千问输入过长 | 按任务错误提示减少输入上下文或选择其他支持该长度的服务；不要截断后当作原任务提交 |
| 无素材、无历史项目 | 属于源码初始状态；自行导入素材或恢复本机备份 |

PowerShell 设置端口的写法是 `$env:AI_CARTOON_FACTORY_PORT = "7861"`。默认只监听 `127.0.0.1`，本指南按本机使用配置。

## 8. Windows EXE 发布包

在 Windows x64 的 Anaconda Prompt 中进入项目，执行以下命令构建便携包。

```bat
conda activate ai_cartoon_factory
python -m pip install -r requirements-desktop.txt pyinstaller
python scripts/package_windows.py
```

构建需要 PATH 中可执行的 `ffmpeg` 和 `ffprobe`。首次构建会下载并验证微软签名的 WebView2 x64 离线安装器，缓存于 `.local/packaging/`。后续构建复用缓存。脚本在中文与空格路径的临时副本中清除 Python / Conda 路径，验证 EXE 启动、业务 API 和媒体工具，然后生成 `dist/AI_Cartoon_Factory_Windows_x64.zip` 及 SHA-256 文件。

新电脑将完整 ZIP 解压到可写目录，双击 `AI_Cartoon_Factory.exe`。包内包含 Python、依赖、媒体工具和 WebView2 离线安装器。首次运行自动创建公开默认配置和空项目。生成服务、账号和素材按前文配置，迁移后在设置中重新填写密钥。便携包使用 Windows 10 / 11 x64 系统的 .NET Framework 4.8。详细启动说明随包附在 `使用说明.txt`。
