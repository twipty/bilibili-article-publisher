---
name: bilibili-article-publisher
description: 把 Markdown / HTML 图文稿件自动发布为哔哩哔哩专栏（含 2026-11 改版后的新版图文 opus 通道），支持扫码登录、本地与外链图片自动转存 B 站图床、离线预览、草稿保存、公开发布、队列批量与定时自动投稿、发布记录与幂等防重复。适用于用户要求「发布 B 站专栏」「哔哩哔哩专栏投稿」「把这篇图文发到 B 站」「自动/定时发专栏」「生成 B 站专栏预览」「查看我的专栏草稿」等场景；也适用于把已有稿件批量转成专栏草稿。触发词：B站专栏、哔哩哔哩专栏、bilibili article、cv 文章、opus 图文、专栏投稿、专栏草稿、图文自动发布。
agent_created: true
display_name: 哔哩哔哩专栏图文发布
display_name_en: Bilibili Article Publisher
description_zh: 把 Markdown 图文稿自动转成 B 站专栏并保存草稿或发布，支持扫码登录、离线预览、图片自动转存与定时任务。
description_en: Turn Markdown articles into Bilibili column posts with QR login, offline preview, automatic image upload and scheduled drafts.
examples_zh:
  - "把这篇 Markdown 发到 B 站专栏"
  - "生成 B 站专栏预览页看看排版"
  - "把 ~/articles 里的稿子批量存成专栏草稿"
  - "查看我最近的 B 站专栏草稿"
examples_en:
  - "Publish this Markdown article to my Bilibili column"
  - "Generate an offline preview of the Bilibili article"
  - "Save all drafts in ~/articles as Bilibili column drafts"
  - "List my recent Bilibili column drafts"
version: 1.1.0
author: "twipty"
allowed-tools: "Bash,Read,Write,WebFetch"
---

# 哔哩哔哩专栏图文自动发布

## 概述

把一份 Markdown（或 HTML）图文稿转换成 B 站专栏正文 HTML，把本地/外链图片转存到 B 站图床，
再保存到创作中心草稿或公开发布。全部脚本仅用 Python 标准库，无需安装任何依赖。
提供队列批处理与发布记录，可挂到 WorkBuddy 定时任务实现无人值守投稿。

## 何时使用

- 用户要求把一篇图文/Markdown 稿件发到 B 站专栏、生成专栏预览、或先看排版效果。
- 用户要求批量投稿、定时自动发布专栏、或管理一个待发布稿件目录。
- 用户询问 B 站专栏草稿、专栏接口、图片被拒、412 风控等问题。
- 不用于视频投稿、动态、评论区，也不用于他人账号。

## 安全边界（每次执行前必读，不可绕过）

1. **默认零写入**：任何 `publish` / `queue` 命令不加 `--confirm` 时只生成预览并返回 JSON，
   完全不联网、不碰账号。必须先把 dry-run 结果展示给用户看。
2. **`--confirm` 必须是最后一个参数**，且需由用户明确同意后才追加。
   出现在其它位置会被直接拒绝，防止参数拼接误触发写入。
3. **默认只保存草稿**：即使带 `--confirm`，也只有额外显式加 `--publish` 才会公开发布。
   涉及公开发布时，先向用户确认标题、正文与配图。
4. **登录态即账号完整权限**：默认凭据存 `~/.workbuddy/secrets/bilibili.json`（`0600`）；
   用 `BILI_AUTH_PROVIDER=connector` 时凭据由外部注入、只在内存中。
   两种来源都绝不打印 Cookie、绝不写进日志/预览/文档、绝不提交到版本库；
   Cookie 只发往白名单域名。详见「鉴权 Provider」。
5. **不规避风控**：`article/submit` 返回 412 / `-352` 时立即停止，返回已保存的草稿链接让用户到网页发布，
   绝不循环重试。内容需合规，转载须如实声明。
6. **不删用户内容**：技能只列出草稿，不提供删除命令。

## 快速开始

脚本零第三方依赖；WorkBuddy 环境推荐用托管 Python，其它机器任意 `python3 ≥ 3.9` 即可。

```bash
BILI=~/.workbuddy/skills/bilibili-article-publisher/scripts/bili.py
PY=python3  # 或你的虚拟环境：/path/to/venv/bin/python
```

### 1. 首次登录（二选一）

```bash
# 方式 A：扫码（推荐）。会生成二维码 PNG，手机 B 站 App 扫码确认
$PY "$BILI" login qr

# 方式 B：手动导入浏览器 Cookie（把整段 Cookie 存到文件，避免进入 shell 历史）
$PY "$BILI" login cookie --cookie-file /path/to/cookie.txt
```

阻塞式 `login qr` 会输出**两行 JSON**：第一行含 `qrcode_image` 路径，用 `present_files` 打开给用户扫；
第二行是登录结果。二维码 180 秒有效。本机已检测到系统 `qrencode`，无需额外安装。
`qrcode_url` 是一次性登录凭证，只展示给账号本人，不要转发或写进日志/文档。

**Agent 场景推荐两阶段扫码**，避免把对话卡在等待里：

```bash
$PY "$BILI" login qr --stage-only --out ./bili-login-qr.png   # 生成二维码并保存会话，立即返回
# 用 present_files 展示 PNG，请用户用 B 站 App 扫码并确认
$PY "$BILI" login qr-poll                                      # 用户扫完后执行，完成登录并保存凭据
```

未完成的会话存于 `~/.workbuddy/bilibili-article-publisher/qr_pending.json`（0600），
180 秒内有效；登录成功会自动删除。若超时，重新执行 `login qr --stage-only`。
这一设计让「扫码」这一步可以跨对话轮次完成，定时任务里不需要也不应该包含扫码。

### 2. 预检与身份确认

```bash
$PY "$BILI" preflight     # 环境、网络、登录态
$PY "$BILI" whoami        # 确认操作的是哪个账号
```

### 3. 准备稿件

复制 `assets/article_template.md` 作为起点。支持 frontmatter（`title` / `tags` / `category` /
`original` / `summary` / `dynamic` / `cover`），正文用 Markdown；本地图片路径相对于稿件文件。

### 4. 离线预览（不联网）

```bash
$PY "$BILI" build --file article.md
```

产出 `*.preview.html`（浅色主题、含图片清单与发布提示）与 `*.body.html`。
用 `present_files` 打开预览页让用户确认排版。

### 5. Dry-run（仍不写入）

```bash
$PY "$BILI" publish --file article.md
```

返回标题、字数、图片分类（本地/外链/已是 B 站图床）、检查结果、预览路径与 `would_do`。
**把这些信息汇报给用户并取得明确同意**，再执行下一步。

### 6. 确认写入

```bash
# 只保存草稿（推荐，稳定且可人工复核）
$PY "$BILI" publish --file article.md --confirm

# 公开发布（需用户二次确认；风控 412/-352 时会自动降级为草稿）
$PY "$BILI" publish --file article.md --publish --confirm
```

成功后返回 `aid`、`edit_url`，公开发布时另有 `url`（旧版为 `https://www.bilibili.com/read/cv<aid>`）。
只汇报真实返回的 URL，不要编造。
**注意**：2026-11 实测 B 站网页端已切到新版专栏（opus），旧 `read/cv` 链接会跳「页面不存在」，
新版公开链接是 `https://www.bilibili.com/opus/<dyn_id>`。详见下方「新版专栏通道」。
**带图稿件不要走 `publish --confirm`**：脚本会直接拒绝并提示改用 `opus_browser.py`，
因为新版接口拒收 HTML `<img>`；老接口现在只适合纯文字稿。

## 工作流决策树

```
用户要发 B 站专栏
├─ 首次使用 / 登录态过期 → preflight → login qr | login cookie → whoami
├─ 只要看排版 → build → present_files 打开预览
├─ 单篇投稿 → publish(无 --confirm) → 展示 dry-run → 用户同意 → publish --confirm
│              └─ 用户明确要求公开 → 追加 --publish，并说明 412 会降级为草稿
├─ 批量/定时 → queue --dir <目录>（同样先 dry-run，再加 --confirm）
└─ 失败 → 查 references/troubleshooting.md；优先降级为「预览 + 网页手动发布」
```

## 命令参考

| 命令 | 作用 | 是否写入账号 |
|---|---|---|
| `login qr [--stage-only] [--out PNG] [--timeout 180]` | 扫码登录并保存 0600 凭据 | 仅本地 |
| `login qr-poll [--timeout 120]` | 继续完成 `--stage-only` 生成的扫码会话 | 仅本地 |
| `login cookie --cookie-file PATH` | 导入 Cookie 字符串 | 仅本地 |
| `logout` | 删除本地登录态 | 仅本地 |
| `whoami` | 校验登录账号 | 否 |
| `preflight` | 环境 / 网络 / 登录态预检 | 否 |
| `auth-info` | 查看当前鉴权 Provider（不输出凭据值） | 否 |
| `build --file F [--title T] [--out-dir D]` | 生成 HTML 与预览页 | 否 |
| `publish --file F [选项] [--confirm]` | 单篇发布 | 仅带 `--confirm` 时 |
| `drafts [--page N] [--limit N]` | 列出创作中心草稿 | 否 |
| `queue --dir D [选项] [--confirm]` | 批量处理目录内 `*.md` | 仅带 `--confirm` 时 |
| `status [--limit N]` | 查看本地发布记录 | 否 |
| `scripts/opus_browser.py draft --file F` | 新版专栏：带图稿件存草稿 | **是** |
| `scripts/opus_browser.py probe / verify` | 新版专栏：探测与复核 | 否 |
| `scripts/opus_browser.py publish-draft --confirm` | 公开发布已有草稿（对外不可逆） | **是（对外）** |

`publish-draft` 的三道门禁：必须显式 `--confirm`；编辑器 `aid` 必须等于草稿接口返回的最新 aid
（可用 `--aid` 指定）；标题 / 正文长度 / 配图数量（默认 `>=2`）全部校验通过才点击「发布」。
可选 `--min-images N`、`--title T`。成功时输出 `dyn_id` 与 `public_url`，
未检测到成功信号则提示人工确认、**不重试**，避免重复投稿。

`publish` / `queue` 常用选项：

- `--publish` 公开发布；不加则只存草稿
- `--cover PATH` 指定封面图（单图布局）
- `--category ID` 专栏分类，默认 `0`
- `--skip-failed-images` 图片上传失败时丢弃该图而非中止
- `--no-rehost` 保留外链图片（**很可能被 37130 拒绝**，仅用于调试）
- `--force` 忽略重复发布幂等检查
- `--out-dir DIR` 预览输出目录
- `--confirm` **只能放在最后**

所有命令输出 JSON 到 stdout，进度与告警到 stderr，便于解析。
失败时 `ok=false` 并以非零码退出。

## 鉴权 Provider（可替换注入）

发布链路分四层，**只有鉴权层与凭据来源有关**：Markdown 转换、图片处理、
草稿/发布编排都不关心 Cookie 从哪来。`Client` 只依赖
`AuthProvider.load() -> {"cookies": {...}}`，所以将来若出现 B 站官方连接器，
只换这一层的注入方式即可，其余三层一行都不用改。

### 选择 provider

| `BILI_AUTH_PROVIDER` | 凭据来源 | 适用 |
|---|---|---|
| `file`（默认，不设置即生效） | 本机 `~/.workbuddy/secrets/bilibili.json`（0600） | 个人账号、扫码 / Cookie 导入 |
| `connector` | 环境变量或注入文件，**不落本机磁盘** | 未来连接器 / 托管凭据 / 多账号 |

```bash
python3 scripts/bili.py auth-info   # 查看当前注入方式，绝不输出 Cookie 值
```

### connector 注入点（优先级从高到低）

| 变量 | 内容 |
|---|---|
| `BILI_AUTH_JSON` | 完整 JSON：`{"cookies": {"SESSDATA": "...", "bili_jct": "..."}}`，或直接给 cookies 对象 |
| `BILI_COOKIE_STRING` | 浏览器整段 Cookie 字符串 |
| `BILI_AUTH_SOURCE` | 指向一个 JSON 文件的路径 |

连接器只要设置 `BILI_AUTH_PROVIDER=connector` 加任一注入点，`bili.py` 与
`opus_browser.py`（浏览器通道）会**同时**生效：

```bash
export BILI_AUTH_PROVIDER=connector
export BILI_AUTH_JSON='{"cookies":{"SESSDATA":"...","bili_jct":"..."}}'
python3 scripts/bili.py whoami
```

进程内扩展自定义 provider：实现 `AuthProvider` 子类的 `load()` 与安全摘要
`describe()`，再 `bc.register_provider("name", YourProvider)`。**不要做动态导入**
（避免加载不可信模块）；未知 provider 会直接报错，不会静默回退到 file。

### 边界

- `auth-info` / `preflight` / `describe()` 绝不输出 Cookie 值。
- `connector` 凭据只在内存中，不会写 `bilibili.json`；`logout` 只删本地文件，不影响外部注入。
- `BILI_AUTH_FILE` 仅覆盖 **file provider** 的路径，与 connector 的 `BILI_AUTH_SOURCE` 相互独立。
- 未注入凭据或未知 provider 都直接报错退出，避免误用错误账号。
- 直接把 Cookie 写进 `export` 会进入 shell 历史；生产用法应由连接器/进程环境注入。
- 实测（2026-11-10）：把 `BILI_AUTH_FILE` 指向不存在的路径、改用 `connector` 注入后，
  `whoami` / `preflight` 仍联网识别出账号，证实鉴权层已完全解耦。

## 自动发布与定时任务

队列模式会递归扫描目录内 `*.md`，跳过带 `.skip` / `.published` 标记的稿件，
按 `标题 + 正文` 哈希去重，单篇失败不影响其它稿件：

```bash
$PY "$BILI" queue --dir ~/bilibili-queue                     # dry-run
$PY "$BILI" queue --dir ~/bilibili-queue --confirm          # 批量存草稿
$PY "$BILI" queue --dir ~/bilibili-queue --publish --confirm # 批量公开发布
```

创建 WorkBuddy 定时任务时，用 `automation_update` 工具（`scheduleType=recurring` + `rrule`），
prompt 需自包含且不含时间信息，例如：

> 扫描 ~/bilibili-queue 目录中的待发布 Markdown 稿件，使用 bilibili-article-publisher 技能：
> 先执行 auth-info、preflight 与 whoami 校验登录态；对每篇稿件生成预览；
> 纯文字稿用 queue --confirm 存草稿（不要加 --publish），带图稿件改用
> scripts/opus_browser.py draft 逐篇保存（新版专栏拒绝 HTML <img> 外链）；
> 汇总成功/失败篇数与草稿链接；登录态失效时不要重试，
> file provider 提示重新扫码，connector provider 提示检查凭据注入。

**不要在自动化里放扫码登录**，首次必须人工完成登录（或由连接器完成授权）。
默认只存草稿，公开发布建议保留人工确认环节。

## 稿件格式要点

- 专栏 `content` 是 HTML；脚本自动把 Markdown 转为 HTML（标题、列表、引用、代码块、表格、图片）。
- `# 一级标题` 默认降级为 `h2`，因为标题另有独立字段；`--no-demote-h1` 可关闭。
- 图片可以是相对路径、绝对路径或 http(s) 外链；发布前统一转存到 B 站图床，
  否则整篇稿件会被 `37130` 拒绝。图片路径不要带空格。
- 正文建议 200 字以上或至少 3 张图（开放平台通道的硬性要求）；Web 通道较宽松但会提示。
- 也可直接提交 HTML 文件（`build` 会识别块级 HTML 并原样保留）。

## 新版专栏通道（2026-11-10 实机验证）

B 站网页端专栏已改版，旧通道失效。若本技能 `publish` 报接口错误、或打开投稿页看到
「旧版编辑器已下线」，改走本节。带图稿件**必须**用下面的脚本通道。

### 脚本通道（首选，已实机跑通）

`scripts/opus_browser.py` 复用 `login qr` 保存的 0600 Cookie，注入一次性 Chromium context
（不读浏览器 profile、不打印 Cookie、用完即关），依赖 Playwright + 系统 `/usr/bin/chromium`。

```bash
OPUS=~/.workbuddy/skills/bilibili-article-publisher/scripts/opus_browser.py
$PY "$OPUS" probe                     # 只读：验证登录态 + 探测页面
$PY "$OPUS" probe --enter             # 只读：进入编辑器看控件
$PY "$OPUS" draft --file article.md   # 写入：填标题 + 粘贴正文 + 保存为草稿
$PY "$OPUS" verify                    # 只读：复核草稿正文与图片
```

`probe` / `verify` 只读；`draft` 只保存草稿；**只有 `publish-draft` 会点击「发布」**，
且必须显式加 `--confirm`。旧 `publish` 模式已停用，调用会直接报错并指向 `publish-draft`。

2026-11-10 实测结论（排错时先看这里）：

- 只要 HTML 正文含 `<img src>`，无论老接口 `draft/addupdate` 还是新版
  `x/dynamic/feed/article/draft/add`，一律 `37130 内容包含非法图片链接`；换 https、
  协议相对、`i0.hdslb.com`、`bfs/article` 全部无效。无图正文可正常保存。
- `x/article/creative/article/upcover` 返回 `http://article.biliimg.com/bfs/new_dyn/...`，
  **这个地址专栏不接受**，不要写进正文（脚本已不再用它）。
- 正确做法：正文文字用 `ClipboardItem('text/html')` 写入系统剪贴板再 `Ctrl+V`；
  图片把二进制写入剪贴板后逐张 `Ctrl+V`，由编辑器自己上传，得到
  `http://i0.hdslb.com/bfs/new_dyn/<md5><mid>.png` 才能通过校验。
  合成 `ClipboardEvent` 会被 ProseMirror 忽略，必须用真实剪贴板。
- 保存成功返回 `{"code":0,"data":{"article_id":<aid>}}`，编辑器提示「保存成功」。
  老草稿列表接口读不到新版正文（`content` 为空），所以**必须用 `verify` 复核**。
- 每次 `draft` 都会点「新的创作」生成新草稿，重复运行会留下多条同名草稿，需人工清理。

### 变化清单

| 项目 | 旧（已失效） | 新（实测） |
|---|---|---|
| 投稿入口 | `member.bilibili.com/read/editor` | `member.bilibili.com/platform/upload/text` → `new-article` → 点「新的创作」 |
| 编辑器 | 页面内 | iframe `member.bilibili.com/york/read-editor`，Tiptap/ProseMirror |
| 提交接口 | `x/article/creative/article/submit` | `x/dynamic/feed/create/opus` |
| 公开链接 | `www.bilibili.com/read/cv<aid>` | `www.bilibili.com/opus/<dyn_id>` |
| 文章管理 | `/platform/upload-manager/article`（只列视频） | `/platform/upload-manager/opus` |
| 状态查询 | — | `x/polymer/web-dynamic/v1/opus/creationlist?pn=1&ps=10&filter_type=0`；`filter_group.filter_type` 1=审核中、2=通过 |
| 只读校验 | — | `api.bilibili.com/x/article/viewinfo?id=<rid>`，无凭据 `code=0` 即公开 |

新版编辑器标题是 `textarea.title-input__inner`（上限 50 字），正文是
`.tiptap.ProseMirror[contenteditable=true]`；按钮「保存为草稿」「发布」「发布设置」。

### 备用：从本机浏览器 profile 提取登录态

`scripts/opus_browser.py` 默认走 `login qr` 保存的鉴权 Provider（或 connector 注入），
**不需要**读浏览器 profile。下面只是没有扫码条件时的备用思路，脚本未实现；
凭据只在内存中使用，不打印、不落盘、不上传，不改动原浏览器 profile。

1. **登录态注入**：Chromium Linux 的 Cookie 多为 `v11` 加密，固定 `peanuts` 密钥常解不开。
   用 `secretstorage` 从会话密钥环读标签含 `Chromium Safe Storage` 的 secret，
   `PBKDF2-HMAC-SHA1(pw, b'saltysalt', 1, dkLen=16)` 派生 AES-128-CBC 密钥
   （IV 为 16 个空格，密文去掉 `v11` 前缀，必要时再去掉 32 字节域名哈希前缀与 PKCS7 padding），
   解密后用 Playwright `context.add_cookies()` 注入。复制整个 profile 通常无效——
   加密 Cookie 会在新实例里丢失，只剩十几条匿名 Cookie。
   先用 `api.bilibili.com/x/web-interface/nav` 验证 `isLogin=true` 再继续。
2. **富文本粘贴**：不要裸调接口拼 `content`（新版 `content` 是编辑器内部结构，且请求带 WBI 签名）。
   必须用 `navigator.clipboard.write()` 写真实剪贴板再 `Ctrl+V`；**合成 `ClipboardEvent`
   会被 ProseMirror 直接忽略**（已实测）。图片同理，把二进制写进剪贴板逐张粘贴，
   让编辑器自己上传成合法 `native-image` 节点。
3. **发布**：`publish-draft` 精确匹配「发布」按钮（避免误点「发布设置」），成功后
   `x/dynamic/feed/create/opus` 返回 `code:0` 与 `dyn_id_str`，编辑器提示「你的专栏已提交成功」。
   发布后页面不跳转，靠接口响应与 creationlist 判断结果。
4. **复核**：从 creationlist 拿 `dyn_id` / `rid` 与审核状态；审核通常数分钟内通过。
   匿名 headless 访问 opus 页可能撞验证码（自动化风控），不代表文章不可见，
   用无凭据的 `x/article/viewinfo` 判断更可靠。

### 内容适配

B 站专栏后端不渲染 Markdown 表格，发布前把表格转成结构化列表；
正文一级标题去掉（标题栏单独填）；代码块用内联样式的 `<pre>`。
纯 HTML 文件可直接作为 `text/html` 粘贴源，不需要第三方 Markdown 扩展。

### 安全提醒

- 这是用户本人账号的公开发布动作，执行前必须取得明确授权。
- 不读取 / 不打印 / 不持久化任何 Cookie 值；临时 Cookie 副本用完立即删除。
- 遇到验证码或 412 立即停止，不要重试、不要绕过风控。
- 可见范围默认「所有人可见」；原创声明、AI 辅助创作声明默认不勾，需要时由用户确认后再操作。

## 失败与降级

- 提交被 412 / `-352` 风控拦截：草稿已保存，返回 `edit_url`，**不要重试同一接口**；
  优先用 `opus_browser.py publish-draft --confirm` 公开发布该草稿（-352 后此路径实测可通），
  或到网页编辑器完成发布。
- 图片上传失败：默认中止；可用 `--skip-failed-images` 跳过。
- 草稿满 999（`37106`）：`drafts` 查看后到创作中心网页手动清理。
- 接口变更：用 `build` 生成 HTML → 复制粘贴到网页编辑器；
  接口字段与抓包排查方法见 `references/api.md`、`references/troubleshooting.md`。
- 任何异常都先跑 `preflight` 区分「登录态问题」与「接口变更」。

## 资源

| 路径 | 用途 |
|---|---|
| `scripts/bili.py` | CLI 主入口与发布编排 |
| `scripts/bili_client.py` | HTTP / **鉴权 Provider 层** / 图片上传 / 草稿与发布 API |
| `scripts/md2html.py` | Markdown→专栏 HTML、图片处理、预览页渲染 |
| `scripts/opus_browser.py` | 新版 opus 编辑器通道（Playwright，带图稿件存草稿） |
| `scripts/selftest.py` | 离线自测（68 项断言，不联网、不碰账号） |
| `references/api.md` | Web 端接口字段、开放平台通道、已验证状态、资料来源 |
| `references/troubleshooting.md` | 错误码、412/37130 处理、降级路径、接口变更排查 |
| `assets/article_template.md` | 带 frontmatter 的图文稿模板 |

修改脚本后执行 `python3 scripts/selftest.py` 验证。所有通道与接口细节以
`references/api.md` 为准；Web 创作中心接口为非公开接口，存在变更与风控可能。
