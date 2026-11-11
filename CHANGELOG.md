# Changelog

## 1.1.0 (2026-11-10)

- **新增 `scripts/opus_browser.py`**：B 站 2026-11 新版图文 opus 编辑器通道（Playwright + 系统 Chromium）。
  文字走真实剪贴板 `Ctrl+V`，图片二进制写进剪贴板逐张粘贴，让编辑器原生上传生成合法 `native-image` 节点。
- **修复 `37130 内容包含非法图片链接`**：确认根因是 `upcover` 返回的 `article.biliimg.com/bfs/new_dyn/...`
  不被专栏接受；换协议、换域名、`image_urls` 声明均无效。带图稿件现在强制走浏览器通道，
  `bili.py publish --confirm` 在联网前直接拦截并给出提示。
- **新增 `publish-draft`**：公开发布已有草稿，带 `--confirm` 显式授权、`--aid` 目标校验、
  标题/正文长度/配图数量预检、发布接口响应结构化判定与公开链接输出。
  不再重复点击发布，遇风控不重试。
- **鉴权层抽象为 Provider**：`file` / `connector` 两种实现，支持
  `BILI_AUTH_JSON`、`BILI_COOKIE_STRING`、`BILI_AUTH_SOURCE` 与 `register_provider()` 自定义扩展；
  API 通道与浏览器通道共用，未注入或未知 provider 明确报错、不静默回退。
- **新增 `auth-info` 命令**，输出安全的 provider 摘要，不泄露任何 Cookie 值。
- **两阶段扫码**：`login qr --stage-only` 先出二维码并保存 0600 会话，用户扫完再 `login qr-poll`，
  避免把对话阻塞在轮询里。
- 离线自测从 50 项扩充到 **68 项**，覆盖 Provider 注入、错误路径与浏览器通道凭据读取。
- SKILL.md frontmatter 补齐 WorkBuddy 开放平台市场规范字段。

## 1.0.0

- 初版：扫码登录 / Cookie 导入、Markdown→专栏 HTML、离线预览、图片转存、
  草稿保存、公开发布、队列批处理、发布记录与幂等防重复。
