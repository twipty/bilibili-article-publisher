# 上架 WorkBuddy 技能市场

## 结论：需要本人在开放平台网页提交，无法通过对话自动完成

原因有三层：

1. WorkBuddy 内置的推荐市场（BuiltinMarket）工具只提供 `search` 与 `install` 两个动作，**没有 publish / submit / upload**。
2. 官方开放平台的技能上架是**网页后台流程**：开发者认证 → 技能发布管理 → 上传 zip → 填写介绍 → 提交 → 人工审核 → 通过后正式发布。没有公开的提交 API 或 CLI。
3. 审核通过与正式发布是两个独立状态，都需要人在后台确认。

因此本项目采取的策略是：**把技能做成完全符合开放平台规范、可直接上传的 zip**，把提交动作留给账号本人在网页完成。

## 手动上架步骤

1. 打开 [WorkBuddy 开放平台](https://open.workbuddy.cn)，完成个人或企业开发者认证。
2. 进入「技能发布管理」→ 创建技能 → 上传本文末生成的 zip 包。
3. 填写商品介绍：适合谁、需要提供什么、能拿到什么结果、有哪些限制。
4. 配一张结果图（建议用 `build` 生成的预览页截图），比堆形容词更有效。
5. 提交后查看审核状态；**审核通过 ≠ 已正式发布**，两个状态分别检查。
6. 解析 zip 失败时，按本文的结构清单排查；仍失败可邮件联系 `openworkbuddy@tencent.com`，或扫开放平台首页下方二维码加入社群。

> 另一个入口：WorkBuddy 客户端左侧「专家·技能·连接器」→ 技能 → 右上角「添加技能」→「创建技能」，会跳转到对话里引导创建；自行上传的技能包则走「上传技能」导入（那是**本地安装**，不等于上架市场）。

## SKILL.md frontmatter 必需字段

开放平台要求以下字段（本仓库的 `SKILL.md` 已全部补齐）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 技能标识 |
| `display_name` | 是 | 市场中文展示名 |
| `display_name_en` | 是 | 市场英文展示名 |
| `description` | 是 | 写清用途和触发词 |
| `description_zh` | 是 | 详情页中文简介 |
| `description_en` | 是 | 详情页英文简介 |
| `version` | 是 | 版本号 |
| `author` | 是 | 合作方 / 开发者名称 |
| `examples_zh` / `examples_en` | 否 | 详情页「试试这样用」 |
| `allowed-tools` | 否 | 工具白名单，逗号分隔 |

## zip 包结构要求

顶层必须是**以技能名命名的单一目录**，`SKILL.md` 位于该目录根部：

```
bilibili-article-publisher/
├── SKILL.md              # 必须
├── references/           # 可选
│   ├── api.md
│   └── troubleshooting.md
├── scripts/              # 可选
│   ├── bili.py
│   ├── bili_client.py
│   ├── md2html.py
│   ├── opus_browser.py
│   └── selftest.py
└── assets/
    └── article_template.md
```

生成命令（在本仓库的**上级目录**执行）：

```bash
cd /path/to/bilibili-article-publisher-repo
mkdir -p /tmp/marketplace/bilibili-article-publisher
cp -r SKILL.md scripts references assets /tmp/marketplace/bilibili-article-publisher/
cd /tmp/marketplace && zip -r bilibili-article-publisher.zip bilibili-article-publisher
```

本工作区已用 `skill-creator` 的 `package_skill.py` 生成好同名 zip，可直接使用。

## 常见解析失败原因

- zip 顶层没有技能名目录，或 `SKILL.md` 不在根目录。
- `SKILL.md` 缺少 YAML frontmatter、frontmatter 不是合法 YAML、或缺少必需字段。
- 包内混入了 `__pycache__/`、`.pyc`、日志、二维码图片、登录态文件等非技能资源。
- 文件名含非 ASCII 字符导致解压异常（脚本文件名建议保持 ASCII）。

## 上架前自查

- [x] 凭据文件、二维码、测试输出不在包内
- [x] 代码中无硬编码的本地绝对路径（已脱敏为 `python3` / `~`）
- [x] 无 `eval` / `exec` / `shell=True` / `pickle`
- [x] 默认不联网、不写入；公开发布必须显式确认
- [x] `selftest.py` 68 项离线断言通过
- [ ] 开发者认证（需本人完成）
- [ ] 上传 zip 并提交审核（需本人完成）
