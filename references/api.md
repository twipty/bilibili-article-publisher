# B 站专栏接口参考

> 核实日期：2026-11-10。本文件区分「已实测」「社区文档记录」「平台公开文档」三类信息。
> B 站 Web 创作中心接口属于**非公开接口**，随时可能变更或加入风控；使用时应以实际响应为准。

## 通道选择

| 通道 | 前置条件 | 稳定性 | 本技能支持 |
|---|---|---|---|
| A. Web 创作中心（Cookie） | 一个自己的 B 站账号 + 登录 Cookie | 中，可能触发 412 风控 | ✅ 完整实现 |
| B. 开放平台（OAuth + 签名 2.0） | 开发者资质、`ATC_BASE` scope、用户授权 | 高，官方契约 | 📄 仅文档，需按资质自行扩展 |

默认走通道 A。若用于企业/长期批量发布，建议申请通道 B 后扩展 `bili_client.py`。

---

## 通道 A：Web 创作中心（本技能默认）

### 认证：Cookie

必需字段：

| 字段 | 用途 |
|---|---|
| `SESSDATA` | 登录凭证 |
| `bili_jct` | CSRF token，所有写操作必须作为参数 `csrf` 传入 |
| `DedeUserID` / `DedeUserID__ckMd5` / `sid` | 常见配套字段 |
| `buvid3` / `buvid4` | 设备指纹，风控相关 |

**SESSDATA 值可能含 `%2C` 等 URL 编码，必须原样保留，不要解码。**

两种获取方式：

1. **扫码登录（推荐）**，无需手动复制 Cookie：
   - `GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate`
     → `data.url`（二维码内容）、`data.qrcode_key`（32 位，180 秒有效）。
     *本机实测 2026-11-10：HTTP 200，`code: 0`，字段齐全。*
   - 把 `data.url` 生成二维码（技能优先用 `qrcode` 库，其次系统 `qrencode`；本机 `qrencode` 已可用）。
   - `GET https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=...`
     两层状态码：外层 `code` 表示接口调用结果；`data.code` 表示扫码进度：
     `86101` 未扫码、`86090` 已扫待确认、`86038` 已失效、`0` 确认成功。
   - 成功后响应头会下发部分 Cookie，并给出 `data.url`（形如
     `https://passport.biligame.com/x/passport-login/web/crossDomain?...`）。
     需带 `Referer: https://www.bilibili.com` 访问该回调 URL，服务端才会补齐
     `SESSDATA`、`bili_jct`、`DedeUserID`。同时保留 `refresh_token` 备用。
2. **手动导入**：浏览器登录 B 站后，从开发者工具 Network 面板任取一个
   `api.bilibili.com` 请求，复制完整 `Cookie` 请求头，通过 `--cookie-file` 传入
   （避免 `--cookie` 明文进入 shell 历史）。

登录态校验：`GET https://api.bilibili.com/x/web-interface/nav` →
`data.isLogin`、`data.mid`、`data.uname`。`isLogin` 为 false 即 Cookie 失效，
**不要循环重试**，直接引导重新登录。

凭据来源由**鉴权 Provider 层**统一提供（`Client` 只调用
`AuthProvider.load() -> {"cookies": {...}}`）：

| `BILI_AUTH_PROVIDER` | 来源 | 落盘 |
|---|---|---|
| `file`（默认） | `~/.workbuddy/secrets/bilibili.json`（可用 `BILI_AUTH_FILE` 覆盖），权限 `0600`、父目录 `0700` | 是 |
| `connector` | `BILI_AUTH_JSON` / `BILI_COOKIE_STRING` / `BILI_AUTH_SOURCE` | 否，仅内存 |

`bili.py` 与 `opus_browser.py` 共用同一 provider；`auth-info` 可只读查看当前注入方式。
技能绝不打印 Cookie 值，也绝不把凭据写入日志或预览页。
HTTP 层只允许向白名单域名发送 Cookie，且禁用自动重定向以防跳转外泄。

### 接口一览

| 用途 | URL | 方法 | 报文 |
|---|---|---|---|
| 登录态校验 | `https://api.bilibili.com/x/web-interface/nav` | GET | — |
| 图片上传 | `https://api.bilibili.com/x/article/creative/article/upcover` | POST | `multipart/form-data` |
| 保存/更新草稿 | `https://api.bilibili.com/x/article/creative/draft/addupdate` | POST | `application/x-www-form-urlencoded` |
| 公开发布 | `https://api.bilibili.com/x/article/creative/article/submit` | POST | 同上 |
| 草稿列表 | `https://api.bilibili.com/x/article/creative/draft/list?pn=1&ps=50` | GET | — |

通用请求头：`Cookie`、`Referer: https://member.bilibili.com/`、`Origin: https://member.bilibili.com`、
浏览器 `User-Agent`。

### `draft/addupdate` 表单字段

`content` 是 **HTML 字符串**（不是 Markdown，也不是 Quill ops JSON）。

| 字段 | 值 | 说明 |
|---|---|---|
| `title` | 文章标题 | 建议 ≤ 40 字 |
| `content` | 专栏正文 HTML | 图片 src 必须是 B 站图床 |
| `csrf` | `bili_jct` | 写操作必需 |
| `category` | `"0"` | 专栏分类 ID，0 为默认 |
| `list_id` | `"0"` | 文集 ID，0 为不加入 |
| `tid` | `"4"` 等 | 封面布局；错误值返回 `-17` / 含「分类」的错误 |
| `reprint` | `"0"` | 转载标记 |
| `original` | `"1"` | 原创声明 |
| `media_id` / `spoiler` / `pgc_id` / `save` | `"0"` | 固定占位 |
| `tags` | 可选 | 逗号分隔标签 |
| `summary` / `dynamic_intro` | 可选 | 简介 / 动态文案 |
| `image_urls` | 可选 | 封面图 URL（逗号分隔），配合 `tid` |
| `aid` | 更新草稿时 | 新建时不传 |

技能依次尝试 `tid` 候选 `4, 3, 6, 7, 2, 17, 28, 41`，仅在 `-17` 或消息含「分类」时换下一个；
其它错误（登录失效、风控、参数错误）立即停止，避免无意义重试。

成功响应：`{"code": 0, "data": {"aid": ...}}`。

### `article/submit`

复用 `draft/addupdate` 的全部字段，额外带生效的 `tid` 与 `aid`。
成功：`code == 0`，文章地址 `https://www.bilibili.com/read/cv{aid}`。

**关键坑**：该接口常被反爬风控以 **HTTP 412** 拦截，即使草稿已保存成功。
此时技能返回 `ok=false` + `edit_url`，明确提示到网页编辑器完成发布，**绝不循环重试 submit**。
草稿地址：`https://member.bilibili.com/article-text/home?aid={aid}`。

### 图片上传 `upcover`

`multipart/form-data`，两个字段：

- `binary`：图片二进制，`filename="image.png"`，`Content-Type` 按实际格式
- `csrf`：`bili_jct`

成功：`{"code": 0, "data": {"url": "https://i0.hdslb.com/..."}}`。
支持 png / jpg / gif / webp / bmp，单图上限约 12MB。连续上传需轻微限速（技能每次间隔 0.3s），否则易返回 `-1`。

**必须转存的理由**：正文若残留外链图片，B 站会以 **37130** 拒绝整篇稿件。
技能在发布前会扫描 `<img src>`，把本地文件与外链图片全部转存，
并在提交前复查是否仍有外链；有则阻断，而不是静默丢图。
`--skip-failed-images` 才会丢弃上传失败的图片。

### 其它已知限制

- 专栏草稿上限 **999**，满了返回 `37106 草稿数已达最大上限`。技能只提供 `drafts` 列出草稿，
  **不提供批量删除命令**，需到创作中心网页手动清理，避免误删。
- `category` 的具体子分类 ID 未公开稳定文档；Web 通道默认 `0` 可用。

### 验证状态

| 项目 | 状态 |
|---|---|
| `qrcode/generate` 可达 | ✅ 本机实测 2026-11-10，HTTP 200 |
| 二维码生成 | ✅ 本机 `qrencode` 可用 |
| Markdown→HTML、图片转存、发布编排、幂等、412 降级、Provider 切换 | ✅ `scripts/selftest.py` 68 项离线断言通过 |
| 真实账号存草稿 | ✅ 2026-11-10 实测成功（新版 opus 浏览器通道，article_id 392963，正文 1737 字 + 2 图） |
| 真实账号公开发布 | ⚠️ 未执行，按约定需用户单独确认 |
| 鉴权 Provider 解耦 | ✅ 2026-11-10 实测：本地凭据路径指向不存在的文件、改用 `connector` 注入后，`whoami` / `preflight` 仍联网识别账号 |

---

## 通道 B：哔哩哔哩开放平台（需申请资质）

来源：开放平台文档「专栏稿件管理 → 文章提交」（2026-11-10 检索）。

- 前置：申请 `ATC_BASE` scope 权限、完成用户 OAuth 授权、按「接口签名说明 2.0」签名，
  请求需带公共请求头。
- `POST https://member.bilibili.com/arcopen/fn/article/add`

| 字段 | 必填 | 约束 |
|---|---|---|
| `title` | 是 | 建议 30 字内，最多 40 字 |
| `category` | 是 | 子分类 ID，由 `categories` 接口获取 |
| `template_id` | 是 | `3` 封面三图；`4` 单图/无封面且 `banner_url` 或 `top_video_bvid` 非空；`5` 无封面且两者皆空（平台生成默认封面） |
| `summary` | 是 | **不少于 200 字** |
| `content` | 是 | 200–40000 字，**或至少 3 张图片** |
| `banner_url` | 否 | 头图，推荐 960×540，与 `top_video_bvid` 二选一 |
| `original` | 否 | `1` 原创 / `0` 非原创，默认 0 |
| `image_urls` | 否 | 封面图 URL，多个用英文逗号分隔 |

配套接口（文档侧栏）：图片上传、文章编辑、文章删除、查看文章、文集管理、
文章分类 `categories`、卡片信息接口。正文插图使用文档约定的
`${图片描述信息}` 模板，卡片使用「卡片信息接口」返回的模板。
图片 URL 必须是「图片上传」接口生成的地址。

提交后进入**审核流程**，审核期间不对外公开——这与 Web 通道的即时发布不同。

接入步骤：申请开放平台应用 → 申请 `ATC_BASE` → 走 OAuth 拿 `access_token` →
实现签名 2.0 → 先调图片上传 → 再调 `article/add`。技能未内置该通道，
如需使用，可在 `bili_client.py` 中新增一个 `OpenPlatformClient`。

---

## 合规与安全边界

- 仅操作**用户本人**账号，不代理他人、不做批量刷量、不规避平台风控。
- 内容需遵守 B 站专栏规范与法律法规；转载必须如实设置 `original=0` 并注明来源。
- 遵守平台频率限制，自动化建议低频、先草稿后人工确认。
- 非公开接口随时可能变更；接口失效时优先降级为「生成预览 + 保存草稿 + 网页手动发布」，
  不要反复重试或尝试绕过 412。

## 资料来源

- 开放平台「文章提交」文档：<https://open.bilibili.com/doc/4/b14b77b6-8889-8c8b-2e83-17c5a4c550fb>
- bilibili-API-collect 二维码登录：<https://sessionhu.github.io/bilibili-API-collect/docs/login/login_action/QR.html>
- bilibili-API-collect 专栏内容结构：<https://sessionhu.github.io/bilibili-API-collect/docs/article/view.html>
- 社区 Web 端投稿实现（字段与 412 风控经验）：
  <https://www.skillhub.club/skills/acedatacloud-skills-bilibili>、
  <https://deepwiki.com/zihengCat/bilibili-zhuanlan-markdown-tool/4.3-bilibili-api-integration>、
  <https://gitee.com/No_C/BiliExptengxunyun/blob/master/BiliClient/BiliApi.py>
