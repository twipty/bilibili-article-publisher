# 哔哩哔哩专栏图文自动发布 · WorkBuddy Skill

把 Markdown / HTML 图文稿件自动发布为哔哩哔哩专栏（含 2026-11 改版后的新版图文 opus 通道）。

纯 Python 标准库实现，**不需要 pip 安装任何依赖**。扫码登录、图片自动转存、离线预览、草稿保存、公开发布、队列批处理、定时投稿、幂等防重复，全部可脚本化。

> 这是 WorkBuddy 的用户级 Skill，也可作为独立的 CLI 工具使用。

---

## 能力一览

| 环节 | 实现 |
|---|---|
| 登录 | 扫码登录（两阶段，二维码先展示、扫完再轮询）或导入浏览器 Cookie；凭据存 `~/.workbuddy/secrets/bilibili.json`，权限 `0600` |
| 写稿 | 带 YAML frontmatter 的 Markdown 模板：标题 / 标签 / 分类 / 原创声明 / 封面 / 简介 |
| 排版 | Markdown → 专栏 HTML（标题、列表、引用、代码块、表格），生成浅色主题离线预览页 |
| 图片 | 本地图片与 http(s) 外链自动转存；新版编辑器走剪贴板原生上传，规避 `37130` |
| 发布 | 保存草稿 / 公开发布 / 查草稿 / 查发布记录 / 哈希幂等防重复 |
| 自动化 | `queue` 批量处理目录，可挂 WorkBuddy 定时任务 |

## 快速开始

```bash
# 1. 安装：把整个目录放到用户级技能目录
cp -r . ~/.workbuddy/skills/bilibili-article-publisher

# 2. 登录（二选一）
python3 scripts/bili.py login qr --stage-only --out ./login-qr.png   # 先出二维码
python3 scripts/bili.py login qr-poll                                 # 扫完再执行
# 或：python3 scripts/bili.py login cookie --cookie-file ./cookies.txt

# 3. 校验
python3 scripts/bili.py preflight
python3 scripts/bili.py whoami

# 4. 生成预览（纯离线，不联网）
python3 scripts/bili.py build --file article.md --out-dir ./out

# 5. 发布
python3 scripts/bili.py publish --file article.md          # dry-run，完全不联网
```

### 新版专栏（opus）浏览器通道

2026-11 起，B 站网页端专栏编辑器已改为 Tiptap/ProseMirror 的「图文 opus」。**只要 HTML 正文里含 `<img src>`，无论老接口 `draft/addupdate` 还是新版 `article/draft/add`，一律返回 `37130 内容包含非法图片链接`**——换 https、换域名、加 `image_urls` 均无效。图片必须由编辑器自己上传，生成合法的 `native-image` 节点。

因此带图稿件请走浏览器通道（需要本机 Playwright + Chromium）：

```bash
python3 scripts/opus_browser.py verify                    # 只读复核草稿
python3 scripts/opus_browser.py draft --file article.md   # 保存为草稿
python3 scripts/opus_browser.py publish-draft --confirm  # 公开发布已有草稿
```

`publish-draft` 带三道门禁：必须显式 `--confirm`；编辑器 `aid` 必须等于草稿接口返回的最新 aid（或用 `--aid` 指定）；标题、正文长度、配图数量全部校验通过才点击「发布」。未检测到成功信号时不会重试，避免重复投稿。

`bili.py publish --confirm` 遇到带图稿件会**在联网前直接拒绝**并提示改用 `opus_browser.py`。

## 鉴权 Provider

`Client` 只依赖 `AuthProvider.load() -> {"cookies": {...}}`，凭据来源可替换：

| Provider | 来源 | 落盘 |
|---|---|---|
| `file`（默认） | `~/.workbuddy/secrets/bilibili.json`（0600） | 是 |
| `connector` | `BILI_AUTH_JSON` / `BILI_COOKIE_STRING` / `BILI_AUTH_SOURCE` | 否，仅内存 |

```bash
python3 scripts/bili.py auth-info       # 查看当前注入方式，不输出任何 Cookie 值
export BILI_AUTH_PROVIDER=connector
export BILI_AUTH_JSON='{"cookies":{"SESSDATA":"...","bili_jct":"..."}}'
python3 scripts/bili.py whoami
```

API 通道与浏览器通道共用同一个 Provider，切换注入方式时两边同时生效。未注入凭据或未知 provider 会明确报错，**不静默回退**到本地文件。

## 安全边界

- **默认零写入**：`publish` / `queue` 不加 `--confirm` 只生成预览，完全不联网。
- `--confirm` 默认**只存草稿**，要公开必须再显式加 `--publish`；浏览器通道则要显式 `--confirm`。
- 公开提交遇 412 风控不重试，返回草稿编辑链接由本人在网页点最后一下。
- Cookie 只发往域名白名单、禁用自动重定向；不打印、不入日志、不写进预览页。
- 技能不提供删除草稿命令，避免误删账号内容。
- 二维码 `qrcode_url` 是一次性登录凭证，只给账号本人，不要转发或写进日志。

## 目录结构

```
.
├── SKILL.md                    # 技能定义与完整工作流
├── scripts/
│   ├── bili.py                 # 主 CLI：登录 / 转换 / 预览 / 发布 / 队列
│   ├── bili_client.py          # HTTP、鉴权 Provider、图片上传、草稿与发布 API
│   ├── md2html.py              # Markdown → 专栏 HTML、图片处理、预览页
│   ├── opus_browser.py         # 新版 opus 编辑器浏览器通道（Playwright）
│   └── selftest.py             # 离线自测，不联网、不碰账号
├── references/
│   ├── api.md                  # 接口细节、字段、错误码与不确定性说明
│   └── troubleshooting.md      # 排错手册
└── assets/
    └── article_template.md     # 稿件模板
```

## 测试

```bash
python3 scripts/selftest.py      # 68 项离线断言，不联网、不碰账号
```

覆盖：Markdown 转换、图片处理、dry-run、草稿、公开发布、412 降级、幂等、外链图片阻断、队列、Provider 注入与错误路径。

## 上架 WorkBuddy 技能市场

本仓库的 `SKILL.md` frontmatter 已按开放平台规范补齐 `display_name` / `display_name_en` / `description_zh` / `description_en` / `examples_zh` / `examples_en` / `version` / `author` 等字段，可直接打包提交。详见 [docs/MARKETPLACE.md](docs/MARKETPLACE.md)。

## 免责说明

B 站 Web 创作中心是**非公开接口**，可能变更或增加风控。本项目使用本人账号的登录态，仅供个人自动化使用；请遵守哔哩哔哩社区规范与相关条款。开放平台官方 `article/add` 通道需 `ATC_BASE` 资质，本项目未实现。

## License

MIT — 见 [LICENSE](LICENSE)。
