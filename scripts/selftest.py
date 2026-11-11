#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自测：不联网、不碰真实 B 站账号，验证技能的全部核心逻辑。

运行：python3 scripts/selftest.py
失败时以非零码退出并打印失败的断言。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bili_client as bc  # noqa: E402
import md2html as m2h  # noqa: E402

CHECKS: list[str] = []


def ok(name: str, cond: bool, extra: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name} 失败" + (f" :: {extra}" if extra else ""))
    CHECKS.append(name)


class FakeClient:
    """模拟 B 站 API，用于离线验证发布编排。"""

    def __init__(self, submit_ok: bool = True):
        self.submit_ok = submit_ok
        self.calls: list[str] = []

    def whoami(self):
        self.calls.append("whoami")
        return {"mid": 1, "uname": "测试账号", "level": 4}

    def upload_file(self, path: Path):
        self.calls.append("upload_file:" + path.name)
        return f"https://i0.hdslb.com/bfs/article/fake_{path.name}.png"

    def upload_url(self, url: str):
        self.calls.append("upload_url")
        return "https://i0.hdslb.com/bfs/article/fake_remote.png"

    def base_fields(self, title, content, **overrides):
        self.calls.append("base_fields")
        base = {"title": title, "content": content, "csrf": "fake", "category": "0"}
        base.update({k: v for k, v in overrides.items() if v is not None})
        return base

    def save_draft(self, base, tids=bc.TID_CANDIDATES):
        self.calls.append("save_draft")
        return "123456789", tids[0], [{"tid": tids[0], "ok": True, "aid": "123456789"}]

    def submit(self, base, aid, tid):
        self.calls.append("submit")
        if self.submit_ok:
            return 200, {"code": 0, "message": "0"}, True
        return 412, {"code": -412, "message": "风控拦截"}, False


def run(tmp: Path) -> None:
    # ---- frontmatter ----
    meta, body = m2h.parse_frontmatter(
        '---\ntitle: "测试标题"\ntags: [AI, 教程]\noriginal: 0\ncover: ./cover.png\n---\n\n## 小节\n\n正文 **加粗**。\n'
    )
    ok("frontmatter 标题", meta.get("title") == "测试标题")
    ok("frontmatter 数组", meta.get("tags") == ["AI", "教程"], str(meta))
    ok("frontmatter 布尔/数字", meta.get("original") == 0)
    ok("frontmatter 剥离正文", body.startswith("## 小节"))

    # ---- Markdown → HTML ----
    md = """# 一级标题

## 小节

普通段落 **加粗** *斜体* `code` 与 [链接](https://example.com)。

![风景](./assets/pic.png)

- 项目一
- 项目二

1. 第一步
2. 第二步

> 引用内容

```python
print("hi")
```

| A | B |
|---|---|
| 1 | 2 |

---

![外链](https://example.com/a.jpg)
"""
    html = m2h.markdown_to_html(md)
    ok("h1 降级为 h2", "<h2>一级标题</h2>" in html, html[:120])
    ok("h2 降级为 h3", "<h3>小节</h3>" in html)
    ok("粗体", "<strong>加粗</strong>" in html)
    ok("斜体", "<em>斜体</em>" in html)
    ok("行内代码", "<code>code</code>" in html)
    ok("链接", '<a href="https://example.com">链接</a>' in html)
    ok("无序列表", "<ul><li>项目一</li>" in html)
    ok("有序列表", "<ol><li>第一步</li>" in html)
    ok("引用", "<blockquote><p>引用内容</p></blockquote>" in html)
    ok("围栏代码", '<pre><code class="language-python">' in html and 'print(&quot;hi&quot;)' in html)
    ok("表格", "<table><thead><tr><th>A</th>" in html)
    ok("分割线", "<hr>" in html)
    ok("本地图片保留相对路径", '<img src="./assets/pic.png" alt="风景">' in html)
    ok("危险协议被拦截", '<img src="#"' in m2h.markdown_to_html("![x](javascript:alert(1))"))
    ok("HTML 转义", "&lt;script&gt;" in m2h.markdown_to_html("<script>alert(1)</script>"))

    # ---- 图片分类 / 替换 ----
    srcs = m2h.extract_img_srcs(html)
    ok("提取图片", "./assets/pic.png" in srcs and "https://example.com/a.jpg" in srcs, str(srcs))
    asset = tmp / "assets" / "pic.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    kinds = {s: m2h.resolve_image_kind(s, tmp)[0] for s in srcs}
    ok("本地图片识别", kinds["./assets/pic.png"] == "local", str(kinds))
    ok("外链图片识别", kinds["https://example.com/a.jpg"] == "remote")
    ok("B 站图床识别", m2h.resolve_image_kind("https://i0.hdslb.com/bfs/article/x.png", tmp)[0] == "keep")
    ok("无效图片识别", m2h.resolve_image_kind("data:image/png;base64,AAAA", tmp)[0] == "invalid")
    replaced = m2h.replace_img_srcs(html, {"./assets/pic.png": "https://i0.hdslb.com/bfs/article/u.png"})
    ok("替换图片地址", "https://i0.hdslb.com/bfs/article/u.png" in replaced)
    ok("丢弃图片", "example.com/a.jpg" not in m2h.drop_img_srcs(html, {"https://example.com/a.jpg"}))

    # ---- Cookie / 凭据 ----
    jar = bc.parse_cookie_string("SESSDATA=abc%2Cdef; bili_jct=xyz123; DedeUserID=42; buvid3=u")
    ok("Cookie 解析保留编码", jar["SESSDATA"] == "abc%2Cdef", str(jar))
    ok("Cookie 解析 csrf", jar["bili_jct"] == "xyz123")
    auth = tmp / "secrets" / "bilibili.json"
    bc.save_auth(jar, auth, refresh_token="fake")
    ok("凭据文件 0600", (auth.stat().st_mode & 0o777) == 0o600, oct(auth.stat().st_mode & 0o777))
    ok("凭据回读", bc.load_auth(auth)["cookies"]["bili_jct"] == "xyz123")
    ok("凭据不含明文在异常里", "xyz123" not in str(bc.default_auth_path()))

    # ---- 鉴权 Provider 层（可替换注入） ----
    env_keys = ("BILI_AUTH_PROVIDER", "BILI_AUTH_JSON", "BILI_COOKIE_STRING", "BILI_AUTH_SOURCE")
    saved_env = {k: os.environ.get(k) for k in env_keys}
    try:
        for k in env_keys:
            os.environ.pop(k, None)
        ok("默认 provider 为 file", bc.resolve_provider().name == "file")
        fp = bc.FileAuthProvider(auth)
        ok("file provider 读回凭据", fp.load()["cookies"]["bili_jct"] == "xyz123")
        ok("file describe 不泄露凭据", "xyz123" not in json.dumps(fp.describe(), ensure_ascii=False))

        os.environ["BILI_AUTH_PROVIDER"] = "connector"
        os.environ["BILI_AUTH_JSON"] = json.dumps({"cookies": jar})
        cp = bc.resolve_provider()
        ok("环境变量切到 connector", cp.name == "connector")
        ok("connector 注入可加载", cp.load()["cookies"]["SESSDATA"] == "abc%2Cdef")
        ok("describe_auth 不泄露凭据", "xyz123" not in json.dumps(bc.describe_auth(), ensure_ascii=False))
        ok("connector 凭据为内存态且不落盘",
           cp.describe().get("in_memory") is True and cp.describe().get("ready") is True)
        c1 = bc.Client()
        ok("Client 使用 connector provider", isinstance(c1.provider, bc.ConnectorAuthProvider))
        ok("Client 拿到注入的 csrf", c1.csrf == "xyz123")

        os.environ.pop("BILI_AUTH_JSON")
        os.environ["BILI_COOKIE_STRING"] = "SESSDATA=abc%2Cdef; bili_jct=xyz123; DedeUserID=42"
        ok("connector 支持整段 Cookie 注入", bc.resolve_provider().load()["cookies"]["bili_jct"] == "xyz123")

        os.environ.pop("BILI_COOKIE_STRING")
        src_file = tmp / "connector_inject.json"
        src_file.write_text(json.dumps({"cookies": jar}), encoding="utf-8")
        os.environ["BILI_AUTH_SOURCE"] = str(src_file)
        ok("connector 支持注入文件", bc.resolve_provider().load()["cookies"]["bili_jct"] == "xyz123")
        os.environ.pop("BILI_AUTH_SOURCE")

        try:
            bc.resolve_provider().load()
            ok("connector 未注入时报错", False)
        except bc.BiliError as e:
            ok("connector 未注入时报错", "未注入凭据" in e.message)

        os.environ["BILI_AUTH_PROVIDER"] = "not_exist"
        try:
            bc.resolve_provider()
            ok("未知 provider 被拒绝", False)
        except bc.BiliError as e:
            ok("未知 provider 被拒绝", "未知的鉴权 provider" in e.message)

        class CustomProvider(bc.AuthProvider):
            name = "custom"

            def load(self):
                return {"cookies": {"SESSDATA": "s", "bili_jct": "custom-csrf"}}

        bc.register_provider("custom", CustomProvider)
        ok("自定义 provider 可注册", "custom" in bc.list_providers())
        os.environ["BILI_AUTH_PROVIDER"] = "custom"
        c2 = bc.Client()
        ok("Client 使用自定义 provider", c2.csrf == "custom-csrf")
        c3 = bc.Client(provider=bc.FileAuthProvider(auth))
        ok("显式 provider 优先于环境变量", c3.csrf == "xyz123")
        c4 = bc.Client(auth={"cookies": {"bili_jct": "inline-csrf"}})
        ok("显式 auth 走 inline provider",
           isinstance(c4.provider, bc.InlineAuthProvider) and c4.csrf == "inline-csrf")
        ok("自定义 provider 摘要不泄露值",
           "custom-csrf" not in json.dumps(bc.describe_auth(), ensure_ascii=False))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        bc._PROVIDERS.pop("custom", None)

    # ---- 预览 / 检查 ----
    preview = m2h.render_preview("测试标题", html, meta, srcs, ["标题偏短"])
    ok("预览是完整 HTML", preview.startswith("<!doctype html>") and "测试标题" in preview)
    ok("预览含提示", "发布前提示" in preview and "图片清单" in preview)
    check = m2h.check_article("测试标题", html, srcs)
    ok("检查返回字数", check["visible_chars"] > 0)
    ok("检查返回图片数", check["image_count"] == 2)
    ok("空标题被阻断", m2h.check_article("", html, srcs)["blockers"])

    # ---- 发布编排（不联网） ----
    import bili

    draft = tmp / "post.md"
    draft.write_text(
        '---\ntitle: "离线测试稿"\ntags: [测试]\n---\n\n## 开头\n\n这是一段用于验证的正文，包含 **加粗** 和图片。\n\n![本地图](./assets/pic.png)\n',
        encoding="utf-8",
    )
    os.environ["BILI_AUTH_FILE"] = str(auth)
    os.environ["BILI_STATE_DIR"] = str(tmp / "state")

    def ns(**kw):
        base = dict(
            file=str(draft), title=None, out_dir=str(tmp / "previews"), no_demote_h1=False,
            publish=False, force=False, skip_failed_images=False, no_rehost=False,
            cover=None, category="0",
        )
        base.update(kw)
        return argparse.Namespace(**base)

    bili.CONFIRM = False
    r = bili.publish_article(ns())
    ok("无 --confirm 为 dry-run", r.get("dry_run") is True and "would_do" in r)
    ok("dry-run 生成预览", Path(r["preview_path"]).exists())

    bili.CONFIRM = True
    fake = FakeClient()
    r = bili.publish_article(ns(), client=fake)
    ok("确认后保存草稿", r.get("aid") == "123456789" and r.get("published") is False, json.dumps(r, ensure_ascii=False)[:400])
    ok("草稿返回编辑链接", "article-text/home?aid=" in r.get("edit_url", ""))
    ok("本地图片被转存", any(a.get("action") == "uploaded" for a in r.get("image_actions", [])))
    ok("发布记录已写入", len(bili.read_log()) == 1)

    r2 = bili.publish_article(ns(), client=FakeClient())
    ok("幂等防重复发布", r2.get("skipped") is True, json.dumps(r2, ensure_ascii=False)[:300])

    r3 = bili.publish_article(ns(publish=True, force=True), client=FakeClient())
    ok("显式 --publish 才公开发布", r3.get("published") is True, json.dumps(r3, ensure_ascii=False)[:300])
    ok("发布返回真实格式 URL", r3.get("url") == "https://www.bilibili.com/read/cv123456789")

    r4 = bili.publish_article(ns(publish=True, force=True), client=FakeClient(submit_ok=False))
    ok("412 风控保留草稿", r4.get("ok") is False and r4.get("aid") == "123456789" and r4.get("edit_url"))
    ok("412 不重试 submit", "草稿已保存" in r4.get("error", ""))

    bad = tmp / "bad.md"
    bad.write_text('---\ntitle: "坏稿"\n---\n\n![外链](https://example.com/x.jpg)\n', encoding="utf-8")
    r5 = bili.publish_article(ns(file=str(bad), force=True), client=FakeClient())
    ok("外链转存成功时不阻断", r5.get("ok") is True, json.dumps(r5, ensure_ascii=False)[:300])
    r6 = bili.publish_article(ns(file=str(bad), force=True, no_rehost=True), client=FakeClient())
    ok("残留外链被阻断(防 37130)", r6.get("ok") is False and any("37130" in b for b in r6.get("blockers", [])), json.dumps(r6, ensure_ascii=False)[:400])

    bili.CONFIRM = False
    qdir = tmp / "queue"
    qdir.mkdir()
    (qdir / "a.md").write_text("---\ntitle: 队列稿 A\n---\n\n正文内容。\n", encoding="utf-8")
    (qdir / "b.md").write_text("---\ntitle: 队列稿 B\n---\n\n正文内容二。\n", encoding="utf-8")
    qargs = argparse.Namespace(
        dir=str(qdir), out_dir=str(tmp / "previews"), publish=False, limit=0,
        force=False, skip_failed_images=False, no_rehost=False,
    )
    qr = bili.cmd_queue(qargs)
    ok("队列 dry-run 处理多篇", qr["total"] == 2 and all(i.get("dry_run") for i in qr["items"]), json.dumps(qr, ensure_ascii=False)[:300])


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="bili-selftest-"))
    try:
        run(tmp)
    except AssertionError as e:
        print(json.dumps({"ok": False, "error": str(e), "passed": CHECKS}, ensure_ascii=False, indent=2))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "passed": CHECKS}, ensure_ascii=False, indent=2))
        sys.exit(1)
    finally:
        # 清理临时文件（仅限本次自建目录）
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({"ok": True, "passed": len(CHECKS), "checks": CHECKS}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
