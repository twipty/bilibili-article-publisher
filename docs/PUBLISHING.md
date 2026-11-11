# 发布到 GitHub

## 当前状态（2026-11-10 22:55 实测）

本地仓库已备好：15 个文件、173 KB、全仓脱敏、本地首次提交完成。
目标仓库已由账号本人创建：**`twipty/bilibili-article-publisher`**（Public、空仓库、默认分支 `main`）。

**推送失败**：GitHub 连接器（GitHub App / 细粒度集成）的所有写操作都被拒绝。

| 操作 | 结果 |
|---|---|
| `get_me` / `search_repositories` / `list_branches` / `get_file_contents`（读） | ✅ 正常，能识别新仓库 |
| `create_repository`（账户级写） | ❌ `POST /user/repos` → `403 Resource not accessible by integration` |
| `push_files`（批量写） | ❌ `PUT /contents/README.md` → 同上 403 |
| `create_or_update_file`（单文件写） | ❌ `PUT /contents/.gitignore` → 同上 403 |

已排除「空仓库无法初始化」：单独写一个文件同样被 403 拒绝，与仓库是否为空无关。
`search_repositories` 返回的 `permissions.push=true` 是账号级权限，**不代表集成令牌的写权限**。

**根因（两种可能，需到 GitHub 侧确认）**：

1. App 安装时选择了 *Only select repositories*，且授权发生在建仓库之前 —
   新建的 `bilibili-article-publisher` 不在授权列表内；
2. 该 App 本身未被授予 *Contents: read & write* 权限（只读集成）。

二者都表现为 `403 Resource not accessible by integration`，本地无法区分也无法绕过。
配置里的 `Authorization: Bearer ...` 是 WorkBuddy 本地 MCP 代理票据，
直接请求 `api.github.com/user` 返回 `401 Bad credentials`，**不是 GitHub token**，
不能用于 REST 或 `git push`；本机也没有 `gh` CLI、SSH key、git 凭据或 PAT。

## 方式 A（最省事）：补授权后重推

1. 打开 <https://github.com/settings/installations>，找到 WorkBuddy 对应的 GitHub App
2. 点 **Configure** → *Repository access*：勾选 `bilibili-article-publisher`，或改为 *All repositories*
3. 若权限表里 *Contents* 不是 **Read & write**，需重新授权（可能要在客户端重新连接 GitHub 连接器）
4. 回对话说一句「补好了」，WorkBuddy 立刻重跑推送并复核远端文件树

> 这一步必须由账号本人在 GitHub 网页完成，助手无法代劳。补授权不需要新建仓库、不需要 token。

## 方式 B（零凭据，确定可行）：网页拖拽上传

把 `outputs/github-upload/` 里的 15 个文件（含目录结构）拖进仓库页的
*uploading an existing file*，填 commit message 提交即可。不需要任何 token 或命令行。
该目录已剔除 `.git`、`__pycache__` 和所有本机绝对路径。

## 方式 C（确定可行）：classic PAT

生成带 `repo` scope 的 **classic PAT**（fine-grained PAT 不支持创建仓库，但推送到已有仓库可用，
需给目标仓库 *Contents: Read & Write*），存到本机 `0600` 文件，**不要贴进对话**：

```bash
install -m 600 /dev/null ~/.workbuddy/secrets/github_pat
# 用编辑器把 PAT 写进该文件并保存

python3 push_via_pat.py --repo bilibili-article-publisher --dry-run
python3 push_via_pat.py --repo bilibili-article-publisher
```

`push_via_pat.py` 用 Git Data API 在**一次 commit** 中写入全部 15 个文件（空仓库自动创建首个
commit 与 `refs/heads/main`），推送后只读复核远端文件树；不打印凭据，推送完成建议撤销 token。
`push_to_github.py` 是走已授权连接器的等价脚本，遇到 403 会给出补授权提示且**不重试**。

## 方式 D：自己用 git 推送

```bash
cd /path/to/bilibili-article-publisher-repo
git remote add origin https://github.com/<YOUR_USER>/bilibili-article-publisher.git
git branch -M main
git push -u origin main
```

HTTPS 认证二选一：

```bash
# 1) Personal Access Token（repo 权限），用 credential helper 缓存，不要写进命令历史
git config --global credential.helper store
# 推送时用户名填 GitHub 用户名，密码填 PAT

# 2) SSH key
ssh-keygen -t ed25519 -C "your-email@example.com"
# 把 ~/.ssh/id_ed25519.pub 加到 GitHub → Settings → SSH keys
git remote set-url origin git@github.com:<YOUR_USER>/bilibili-article-publisher.git
```

若已安装 `gh` CLI，也可以一步创建并推送：

```bash
gh auth login
gh repo create bilibili-article-publisher --public --source=. --remote=origin --push
```

## 推送前检查清单

- [x] `.gitignore` 排除 `__pycache__`、日志、`.env`、登录二维码、cookies、zip 与输出目录
- [x] 全仓无硬编码本机绝对路径
- [x] 无真实 Cookie / token / 二维码图片
- [x] `LICENSE`（MIT）、`README.md`、`CHANGELOG.md` 齐备
- [x] `SKILL.md` frontmatter 符合开放平台规范
- [x] 目标仓库已创建且为空
- [ ] 集成写权限已补齐（方式 A）或改用 B / C / D
- [ ] 确认仓库可见性（公开 / 私有）
- [ ] 确认 `author` 字段与你的 GitHub 账号名一致
