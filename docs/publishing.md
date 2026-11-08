# 源码发布范围

本仓库只提交应用源码、测试、文档、依赖清单、提示词规则、工作流 JSON、前端静态代码和应用公开品牌说明及验签公钥。

以下内容保留在本机，不上传：

- 视频、音频、参考图片、素材库及索引、模型权重。
- `projects/`、`episodes/`、`outputs/`、`assets/`、`logs/`。
- 本机 `config.json`、密钥、配对码、SQLite 队列、浏览器资料。
- 分析报告、缓存、临时文件、教学演示二进制文件、安装器、旧扩展 ZIP 和构建产物。

公开配置为 `config.example.json`。首次使用复制为 `config.json`，已有配置由用户自行维护。公开示例只放通用默认项，不包含账号凭据或本机绝对路径。`resources/brand/brand_public_key.pem` 是验签公钥，不是私钥；不能删除或替换以绕过校验。

`.gitignore` 不能自动移除已经跟踪的文件，也不能清除旧提交历史。每次发布仍需检查暂存文件和内容，不能仅依赖忽略规则。

```bat
git status --short
git diff --cached --stat
git diff --cached --name-only
git diff --cached --check
```

在 `ai_cartoon_factory` conda 环境执行适用测试，记录真实结果。不要为了发布而自动生成收费视频或向真实模型网页发送测试消息。推送沿用远程分支历史，使用普通 push，不进行 force push。
