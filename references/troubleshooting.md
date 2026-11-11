# 故障排查与降级路径

## 快速决策

| 现象 | 先做 | 再做 |
|---|---|---|
| `未找到登录态文件` | `auth-info` 确认当前 provider；file 模式执行 `login qr` / `login cookie` | `whoami` 确认账号 |
| `connector 鉴权未注入凭据` | 设置 `BILI_AUTH_JSON` / `BILI_COOKIE_STRING` / `BILI_AUTH_SOURCE` | `auth-info` 应显示 `ready: true` |
| `未知的鉴权 provider` | 检查 `BILI_AUTH_PROVIDER`（仅 `file` / `connector` 内置） | `auth-info` 列出可用值 |
| `登录态无效或已过期` | 重新登录，不要重试旧 Cookie | `preflight` |
| 二维码出不来图片 | 检查 `qrencode` 或安装 `qrcode` 库 | 直接用输出的 `qrcode_url` 生成二维码 |
| `图片上传失败` | 检查格式/体积/网络 | `--skip-failed-images` 或换图 |
| `残留外链图片` | 去掉 `--no-rehost`，让技能自动转存 | 或把图片换成本地文件 |
| `保存草稿失败 code=-17` | 技能会自动换 `tid` | 仍失败则检查 `--category` |
| `文章提交 412 / -352` | **停止**，草稿已保存，不要重试同一接口 | `opus_browser.py publish-draft --confirm` 或网页发布 |
| `草稿数已达最大上限` | `drafts` 查看 | 到创作中心网页手动清理 |

## 错误码

| 码 / 状态 | 含义 | 处理 |
|---|---|---|
| `-101` | 账号未登录 / SESSDATA 失效 | 重新登录 |
| `-111` | CSRF 校验失败 | 确认 `bili_jct` 与 `csrf` 一致；重新登录 |
| `-17` / 消息含「分类」 | `tid`（封面布局）或 `category` 不被接受 | 技能自动尝试 `4,3,6,7,2,17,28,41` |
| `-1` | 图片上传被限速 | 技能已内置 0.3s 间隔；稍后重试 |
| `37106` | 草稿数达 999 上限 | 网页端清理草稿 |
| `37130` | 内容包含非法图片链接 | 新版专栏拒收任何 HTML `<img>`；见下方「37130 深挖」 |
| `HTTP 412` | 反爬风控拦截 `article/submit`（传输层，网关直接拒绝） | 不要重试；用已保存的草稿走 opus 通道或网页发布 |
| `-352` | 风控校验失败（业务层，HTTP 200 包裹，响应常带 `v_voucher`） | 同 412 处理；详见下方「-352 深挖」 |
| `86038` | 二维码失效 | 重新 `login qr` |
| `86090` | 已扫码、待手机确认 | 继续等待 |
| `86101` | 未扫码 | 继续等待 |
| 草稿保存成功但无 `aid` | 接口返回结构变化 | 见「接口变更排查」 |

## `37130` 深挖（2026-11-10 实机复现）

症状：图片上传接口返回 `code:0` 和 URL，但保存草稿时整篇被拒；去掉 `<img>` 就能保存。

排查结论（逐项实测过，别再重复试）：

| 尝试 | 结果 |
|---|---|
| `http://article.biliimg.com/bfs/new_dyn/...` | 37130 |
| 改成 `https://` | 37130 |
| 改成 `//i0.hdslb.com/...` 协议相对 | 37130 |
| 换成 `i0.hdslb.com/bfs/new_dyn/`（该 URL 实测 HTTP 200） | 37130 |
| 换成 `i0.hdslb.com/bfs/article/` | 37130（且路径 404） |
| 额外传 `image_urls` 字段 | 37130 |
| 只留一张图 | 37130 |
| 完全移除 `<img>` | 保存成功 |

根因：`upcover` 返回的是动态图床地址，专栏保存接口只认**编辑器自己上传**后生成的
`http://i0.hdslb.com/bfs/new_dyn/<md5><mid>.png`。新版 `x/dynamic/feed/article/draft/add`
与老 `draft/addupdate` 用的是同一套校验。

修复路径：`scripts/opus_browser.py draft --file 稿件.md`

1. 正文 HTML 去掉所有 `<img>`，用 `navigator.clipboard.write` + `Ctrl+V` 粘贴文字；
   合成 `ClipboardEvent` 会被 ProseMirror 忽略，必须用真实剪贴板。
2. 图片二进制写入剪贴板后逐张 `Ctrl+V`，编辑器自动上传并插入 `native-image` 节点。
   编辑器里的 `input.file-input[accept=".docx,.md"]` 是文档导入控件，**不是**图片上传。
3. 点「保存为草稿」，用 `verify` 复核（老草稿列表接口的 `content` 为空，不能作为依据）。

注意：每次 `draft` 会新建一条草稿，重复运行会留下同名草稿，需到草稿箱人工清理。

## 常见场景

### 1. 首次使用，网络与依赖是否正常

```bash
python3 scripts/bili.py preflight
```

输出中 `qrcode_library` / `qrencode_binary` 至少一个为 true 才能生成二维码图片；
两者都 false 时仍可用 `login cookie` 手动导入，或安装：

```bash
uv pip install --python <venv>/bin/python qrcode[pil]
```

（本机已检测到系统 `qrencode`，通常无需安装。）

**在 WorkBuddy 对话里登录**：用 `login qr --stage-only --out <png>` 先生成二维码并展示，
用户扫码后再执行 `login qr-poll` 完成登录，避免阻塞对话。会话保存在
`~/.workbuddy/bilibili-article-publisher/qr_pending.json`，180 秒内有效；
超时就重新生成二维码。若 `qr-poll` 报「没有待确认的扫码会话」，说明会话已结束或被清理，
重新执行 `login qr --stage-only`。

### 2. 只想看看排版效果，不想碰账号

```bash
python3 scripts/bili.py build --file article.md
```

`build` 完全离线，产出 `*.preview.html` 与 `*.body.html`，可用 `present_files` 打开预览。

### 3. 公开发布被 412 / -352 风控拦截

这是最常见的失败。技能此时**已经保存了草稿**，返回值包含 `aid` 与 `edit_url`：

```text
https://member.bilibili.com/article-text/home?aid=<aid>
```

不要反复调用 `--publish`，那只会持续触发风控。两条降级路径，按优先级：

1. **opus 浏览器通道直接发布草稿**（2026-11-11 实测：-352 后用此路径一次成功）：
   `opus_browser.py publish-draft --min-images 0 --confirm`（无图稿须显式放宽图片校验）。
   它会打开最新草稿对应的编辑器 aid，正文经真实 Chromium + 真实剪贴板进入新版编辑器，
   与纯接口请求的风控路径不同，可正常通过。
2. 网页编辑器人工发布：打开 `edit_url` 点「投稿」。

建议把自动化配置成默认 `--draft-only`（即不加 `--publish`），
由人工或 opus 通道完成最后一步。

## `-352` 深挖（2026-11-11 实机复现）

`-352` = **风控校验失败**，业务层错误码，HTTP 状态仍是 200：
`{"code": -352, "message": "风控校验失败", "data": {"v_voucher": "voucher_…"}}`。
与 412 同属风控家族但分层不同：**412 是传输层拦截**（网关直接拒请求），
**-352 是业务层判定**（请求进来了，但被判定「环境/行为可疑」而拒绝执行）。
社区资料普遍认为 -352 是「黄牌」（针对单次请求），-412 更严重（IP 级封锁）。

实测证据（同一账号 twipty，同一篇 2052 字纯文字稿）：

| 场景 | 结果 |
|---|---|
| 老接口 `article/submit` 公开提交（带完整登录 Cookie） | HTTP 200 + `code:-352`，**同请求里草稿保存已成功**（aid 正常返回） |
| 同一草稿改走 opus 浏览器通道 `publish-draft` | `create/opus code:0`，公开发布成功 |
| 裸 `curl`（无 Cookie、curl 默认 UA）访问公开读接口 `x/article/viewinfo` | 同样 `code:-352` |

推论：

- -352 针对**请求环境与接口路径**，不是账号状态或内容合规问题——同账号同内容换通道即成功。
- 老的 `article/submit` 公开提交接口已被风控覆盖（2026-11-11 起），即使登录态完全有效；
  草稿保存（`draft/addupdate`）暂不受影响。**纯文字稿公开发布也应优先走 opus 通道。**
- 裸脚本请求公开读接口也可能撞 -352：**验证失败 ≠ 未公开**，判断发布结果以
  发布接口 `code:0` + 编辑器「已提交成功」提示为准，不要用匿名 curl 复核。
- 响应里的 `v_voucher` 是风控会话追踪标识，出现即代表本次请求被风控系统记录。


### 4. 图片相关问题

- B 站正文只接受自家图床（`hdslb.com` / `biliimg.com` / `bilibili.com`）。
- 支持 png / jpg / gif / webp / bmp，单图 ≤ 12MB；webp 若被 `upcover` 拒绝，先转成 png。
- 图片路径不要含空格；含空格时改用不含空格的文件名，或直接写 HTML `<img>` 标签。
- 外链图片下载会阻止私网 / 环回 / 保留地址，且禁止重定向，避免 SSRF。
- 上传失败默认**中止发布**而不是悄悄丢图；确需跳过时用 `--skip-failed-images`。

### 5. 重复发布 / 自动化幂等

每次成功提交都会在 `~/.workbuddy/bilibili-article-publisher/publish_log.jsonl`
记录 `标题 + 正文` 的哈希。相同内容再次提交会被跳过并返回 `skipped: true`。
确需重发用 `--force`。队列模式下成功公开发布还会生成 `<文件名>.published` 标记，
把 `.skip` 标记文件放在稿件旁可跳过该篇。

### 6. 接口变更排查

Web 创作中心接口非公开，若某天全部返回非预期结构：

1. 先跑 `preflight`，确认是登录态问题还是接口变更。
2. 浏览器登录创作中心，打开开发者工具 Network，手动编辑并保存一篇专栏，
   观察 `draft/addupdate` 的真实表单字段与响应结构。
3. 对照 `references/api.md` 更新 `bili_client.py` 的 `base_fields()` 与 `save_draft()`。
4. 跑 `scripts/selftest.py` 确认编排逻辑未被破坏。
5. 临时降级：只用 `build` 生成 HTML，复制粘贴到网页编辑器。

### 7. 自动化（定时任务）建议

- 首次必须人工完成一次登录并 `whoami` 验证，自动化里不要包含扫码步骤。
- 自动化默认只存草稿；需要公开发布时再显式加 `--publish --confirm`，
  并接受 412 时「草稿已保存」的降级结果。
- 队列目录中的稿件建议带 frontmatter `title`，避免用文件名当标题。
- 定期检查 `status` 与 `drafts`，避免草稿堆积到 999 上限。

## 安全提醒

- 登录态文件是账号完整访问凭证，**不要**提交到 git、发到聊天、写进文档或日志。
- `--cookie` 参数会进入 shell 历史，优先用 `--cookie-file` 或环境变量 `BILIBILI_COOKIE`（仅 `login cookie` 导入用）。
- `BILI_AUTH_JSON` / `BILI_COOKIE_STRING` / `BILI_AUTH_SOURCE` 是 `connector` provider 的注入变量，凭据只在内存；`BILI_AUTH_FILE` 只覆盖 `file` provider 的路径，两者不要混用。
- 需要退出登录时执行 `logout` 删除本地凭据（不影响外部注入）。
- 技能不提供删除草稿的命令，避免误删用户内容。
