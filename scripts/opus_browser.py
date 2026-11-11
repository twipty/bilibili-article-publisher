#!/usr/bin/env python3
"""B 站新版专栏（opus）浏览器 UI 通道。

旧版 x/article/creative/draft/addupdate 在 2026-11 已不能可靠保存带图稿件
（正文含 <img src> 一律返回 37130），新版编辑器使用 Tiptap/ProseMirror，
content 是编辑器内部结构且请求带 WBI 签名，因此改用浏览器富文本粘贴。

登录态复用 login qr / login cookie 保存的 ~/.workbuddy/secrets/bilibili.json，
只在内存里注入到一次性的 Chromium context，不打印、不落盘、不改动浏览器 profile。

用法：
  opus_browser.py probe                          # 只探测页面结构，不写入
  opus_browser.py verify                         # 只读复核草稿列表第一条的正文与配图
  opus_browser.py draft --file article.md        # 填标题 + 粘贴正文 + 保存为草稿
  opus_browser.py publish-draft --confirm        # 打开草稿并公开发布（必须显式加 --confirm）
  opus_browser.py publish --file article.md      # 已停用，请改用 publish-draft
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bili_client as bc  # noqa: E402

UPLOAD_URL = "https://member.bilibili.com/platform/upload/text"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
CHROMIUM = "/usr/bin/chromium"


def load_cookie_list() -> list[dict[str, str]]:
    """和 API 通道共用同一个鉴权 Provider；切换 BILI_AUTH_PROVIDER 时自动生效。"""
    provider = bc.resolve_provider()
    try:
        raw = provider.load().get("cookies") or {}
    except bc.BiliError as e:
        raise SystemExit(
            f"登录态不可用（provider={provider.name}）：{e.message}。请先执行 login qr 或注入连接器凭据"
        ) from None
    if not raw.get("SESSDATA") or not raw.get("bili_jct"):
        raise SystemExit("登录态不完整，请重新扫码登录")
    return [
        {"name": k, "value": str(v), "domain": ".bilibili.com", "path": "/"}
        for k, v in raw.items()
    ]


def build_paste_payload(md_path: Path, title: str | None) -> dict:
    """Markdown → 可粘贴 HTML，并收集本地图片交给编辑器自己上传。

    不要把 upcover 返回的 ``bfs/new_dyn`` 外链写进正文：专栏草稿/发布接口会
    以 code 37130「内容包含非法图片链接」拒绝整篇稿件。图片必须走编辑器
    自带的上传控件，由前端生成合法的 native-image 节点。
    """
    import tempfile
    import md2html as m2h

    src = md_path.read_text(encoding="utf-8")
    meta, body_md = m2h.parse_frontmatter(src)
    effective_title = title or meta.get("title") or md_path.stem
    html = m2h.markdown_to_html(body_md, demote_h1=True)

    local_images: list[str] = []
    for img in dict.fromkeys(m2h.extract_img_srcs(html)):
        kind, resolved = m2h.resolve_image_kind(img, md_path.parent)
        if kind == "local" and Path(resolved).is_file():
            local_images.append(str(resolved))
        elif kind == "remote":
            try:
                data, ext, _ = bc.fetch_image_bytes(resolved)
                tmp = Path(tempfile.gettempdir()) / f"opus_img_{len(local_images)}.{ext}"
                tmp.write_bytes(data)
                local_images.append(str(tmp))
            except Exception:
                pass

    html = re.sub(r"<img[^>]*>", "", html)
    plain = re.sub(r"<[^>]+>", "", html)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    return {
        "title": effective_title,
        "html": html,
        "plain": plain,
        "local_images": local_images,
    }


CLIPBOARD_JS = """
async ({html, plain}) => {
  const item = new ClipboardItem({
    'text/html': new Blob([html], {type: 'text/html'}),
    'text/plain': new Blob([plain], {type: 'text/plain'})
  });
  await navigator.clipboard.write([item]);
  return {ok: true};
}
"""

CLIPBOARD_IMAGE_JS = """
async ({b64, mime}) => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], {type: mime});
  await navigator.clipboard.write([new ClipboardItem({[mime]: blob})]);
  return {ok: true, size: bytes.length};
}
"""


def find_editor_frame(page):
    for fr in page.frames:
        if "read-editor" in (fr.url or "") or fr != page.main_frame:
            try:
                if fr.locator(".tiptap.ProseMirror").count():
                    return fr
            except Exception:
                pass
    return page.main_frame


def enter_editor(page) -> dict:
    """在草稿列表 iframe 里点「新的创作」，等编辑器 iframe 加载。"""
    for fr in page.frames:
        try:
            btn = fr.get_by_role("button", name=re.compile("新的创作"))
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(4500)
                return {"clicked": True, "from_frame": (fr.url or "")[:120]}
        except Exception:
            pass
    return {"clicked": False}


def probe(page, context) -> dict:
    resp = context.request.get(NAV_URL, timeout=15000)
    try:
        nav = resp.json()
        is_login = bool((nav.get("data") or {}).get("isLogin"))
        uname = ((nav.get("data") or {}).get("uname")) or ""
    except Exception:
        is_login, uname = False, ""
    frames = []
    for fr in page.frames:
        info = {"url": (fr.url or "")[:160]}
        try:
            info["buttons"] = fr.locator("button").all_text_contents()[:25]
            info["textareas"] = fr.locator("textarea").count()
            info["editables"] = fr.locator('[contenteditable="true"]').count()
            info["title_input"] = fr.locator("textarea.title-input__inner").count()
            info["file_inputs"] = fr.locator('input[type="file"]').count()
            info["titles"] = fr.locator("[title]").evaluate_all(
                "els => els.slice(0, 25).map(e => e.getAttribute('title'))"
            )
            if "read-editor" in (fr.url or ""):
                info["file_input_html"] = fr.locator('input[type="file"]').evaluate_all(
                    "els => els.map(e => e.outerHTML.slice(0, 400))"
                )
                info["image_widgets"] = fr.locator(
                    '[class*="image"],[class*="img"],[class*="pic"],[class*="upload"]'
                ).evaluate_all(
                    "els => els.slice(0, 25).map(e => e.tagName + '.' + (e.className || '').toString().slice(0, 80))"
                )
        except Exception as e:
            info["error"] = str(e)[:100]
        frames.append(info)
    return {
        "nav_is_login": is_login,
        "nav_uname": uname,
        "page_url": page.url,
        "page_title": page.title(),
        "frames": frames,
        "body_text_head": page.locator("body").inner_text()[:400] if page.locator("body").count() else "",
    }


def publish_existing_draft(page, context, args) -> dict:
    """公开发布草稿列表中最新的一条草稿。

    发布是对外不可逆动作，因此这里设三道门禁：
      1. 必须显式传 --confirm；
      2. 编辑器 aid 必须等于草稿接口返回的最新 aid（或 --aid 指定值）；
      3. 标题 / 正文长度 / 配图数量全部校验通过才允许点击「发布」。
    """
    out: dict = {"mode": "publish-draft", "ok": False}
    if not args.confirm:
        out["error"] = "公开发布需显式加 --confirm；本次未点击任何发布按钮"
        return out

    # 取草稿接口的最新一条作为期望目标，避免发布到调试残留的草稿
    latest: dict = {}
    try:
        resp = context.request.get(bc.DRAFT_LIST + "?pn=1&ps=5", timeout=15000)
        drafts = ((resp.json().get("artlist") or {}).get("drafts") or [])
        if drafts:
            latest = {"id": drafts[0].get("id"), "title": drafts[0].get("title")}
        out["latest_draft"] = latest
    except Exception as e:
        out["drafts_error"] = str(e)[:150]

    expected_aid = args.aid or latest.get("id")
    if not expected_aid:
        out["error"] = "无法确定目标草稿 aid，拒绝发布"
        return out
    out["expected_aid"] = expected_aid

    # 打开草稿列表中的第一条「编辑」
    for fr in list(page.frames):
        try:
            if "read-draft" in (fr.url or ""):
                btn = fr.get_by_role("button", name=re.compile("编辑"))
                if btn.count():
                    btn.first.click()
                    out["edit_clicked"] = True
                    page.wait_for_timeout(5000)
                    break
        except Exception as e:
            out["list_error"] = str(e)[:150]
    if not out.get("edit_clicked"):
        out["error"] = "未能打开草稿编辑器，拒绝发布"
        return out

    frame = find_editor_frame(page)
    out["editor_frame"] = (frame.url or "")[:160]
    m = re.search(r"aid=(\d+)", frame.url or "")
    aid = int(m.group(1)) if m else None
    out["aid"] = aid
    if aid != expected_aid:
        out["error"] = f"草稿 aid 不符：期望 {expected_aid}，实际 {aid}，拒绝发布"
        return out

    # 内容校验
    try:
        title = frame.locator("textarea.title-input__inner").first.input_value()
        ed = frame.locator('.tiptap.ProseMirror[contenteditable="true"]').first
        text_len = len(ed.inner_text())
        img_count = ed.locator("img").count()
        out["precheck"] = {"title": title, "text_len": text_len, "img_count": img_count}
    except Exception as e:
        out["error"] = f"读取编辑器内容失败：{str(e)[:180]}"
        return out

    expected_title = args.title or latest.get("title")
    if expected_title and title.strip() != str(expected_title).strip():
        out["error"] = "标题与草稿记录不一致，拒绝发布"
        return out
    if text_len < 1000:
        out["error"] = f"正文仅 {text_len} 字，疑似内容未保存完整，拒绝发布"
        return out
    if img_count < args.min_images:
        out["error"] = f"配图仅 {img_count} 张（要求 >= {args.min_images}），拒绝发布"
        return out

    # 抓取发布相关接口响应
    pub_responses: list[dict] = []

    def _on_pub(r):
        u = r.url or ""
        if any(k in u for k in ("/draft/", "create/opus", "/publish", "/submit", "article/add")):
            rec: dict = {"status": r.status, "url": u[:180]}
            try:
                rec["body"] = json.dumps(r.json(), ensure_ascii=False)[:700]
            except Exception:
                pass
            pub_responses.append(rec)

    page.on("response", _on_pub)

    # 精确匹配「发布」，避免误点「发布设置」
    publish_btn = None
    for fr in (frame, page.main_frame):
        try:
            b = fr.get_by_role("button", name="发布", exact=True)
            if b.count():
                publish_btn = b.first
                break
        except Exception:
            pass
    if publish_btn is None:
        out["error"] = "未找到「发布」按钮，拒绝发布"
        return out

    publish_btn.click()
    out["publish_clicked"] = True
    page.wait_for_timeout(4000)

    # 若出现二次确认弹窗，按精确文案点击一次
    for kw in ("确认发布", "立即发布", "确定发布"):
        for fr in list(page.frames):
            try:
                b = fr.get_by_role("button", name=re.compile("^" + kw + "$"))
                if b.count() and b.first.is_visible():
                    b.first.click()
                    out["confirm_clicked"] = kw
                    page.wait_for_timeout(5000)
                    break
            except Exception:
                pass
        if out.get("confirm_clicked"):
            break

    page.wait_for_timeout(3000)
    out["publish_responses"] = pub_responses[-10:]
    out["page_url_after"] = page.url
    for fr in list(page.frames):
        try:
            if "read-editor" in (fr.url or ""):
                out["editor_tail"] = fr.locator("body").inner_text()[-500:]
        except Exception:
            pass
    # 结构化判定，别用整体字符串子串匹配（二次 JSON 转义会让 '"code":0' 失配）
    signals: list[str] = []
    for rec in pub_responses:
        raw = rec.get("body")
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            data = obj.get("data") or {}
            if obj.get("code") == 0 and data.get("dyn_id_str"):
                did = str(data["dyn_id_str"])
                out["dyn_id"] = did
                out["public_url"] = "https://www.bilibili.com/opus/" + did
                signals.append("create/opus code=0 dyn_id=" + did)
        except Exception:
            pass
    tail = out.get("editor_tail") or ""
    if any(k in tail for k in ("提交成功", "发布成功", "审核中", "已发布")):
        signals.append("编辑器提示：专栏已提交成功")
    out["success_signal"] = signals
    out["ok"] = bool(signals)
    if not out["ok"]:
        out["hint"] = "未检测到明确成功信号，请人工到创作中心确认，不要重复点击发布"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("probe", "draft", "publish", "verify", "publish-draft"))
    ap.add_argument("--file", help="Markdown 稿件路径")
    ap.add_argument("--title", help="覆盖 / 校验标题")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--enter", action="store_true", help="probe 时也点击进入编辑器")
    ap.add_argument("--aid", type=int, help="publish-draft 指定目标草稿 aid")
    ap.add_argument("--min-images", type=int, default=2, help="publish-draft 最少配图数，默认 2")
    ap.add_argument("--confirm", action="store_true", help="publish-draft 必须显式加此参数才会真发布")
    args = ap.parse_args()

    if args.mode == "publish":
        ap.error("publish 模式已停用：请改用 `publish-draft --confirm` 发布已有草稿")
    if args.mode in ("draft",) and not args.file:
        ap.error("draft 需要 --file")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=CHROMIUM, args=["--no-sandbox"]
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
            ),
        )
        context.add_cookies(load_cookie_list())
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()
        api_calls: list[dict] = []

        def _on_response(r):
            u = r.url or ""
            if any(k in u for k in ("/draft/", "create/opus", "draft/add")):
                api_calls.append({"status": r.status, "url": u[:160]})

        page.on("response", _on_response)
        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3500)

        result: dict = {"mode": args.mode, "ok": False}
        if args.mode in ("draft", "publish") or args.enter:
            result["enter"] = enter_editor(page)
        info = probe(page, context)
        result["probe"] = info
        if not info["nav_is_login"]:
            result["error"] = "Cookie 注入后 nav 仍显示未登录，请重新扫码"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            browser.close()
            return

        if args.mode == "probe":
            result["ok"] = True
            print(json.dumps(result, ensure_ascii=False, indent=2))
            browser.close()
            return

        if args.mode == "verify":
            detail: dict = {}
            for fr in list(page.frames):
                try:
                    if "read-draft" in (fr.url or ""):
                        detail["draft_list_text"] = fr.locator("body").inner_text()[:500]
                        btn = fr.get_by_role("button", name=re.compile("编辑"))
                        if btn.count():
                            btn.first.click()
                            detail["edit_clicked"] = True
                            page.wait_for_timeout(5000)
                            break
                except Exception as e:
                    detail["list_error"] = str(e)[:150]
            frame = find_editor_frame(page)
            try:
                detail["title"] = frame.locator("textarea.title-input__inner").first.input_value()
                ed = frame.locator('.tiptap.ProseMirror[contenteditable="true"]').first
                detail["text_len"] = len(ed.inner_text())
                detail["text_head"] = ed.inner_text()[:300]
                detail["img_count"] = ed.locator("img").count()
                raw_srcs = ed.locator("img").evaluate_all("els => els.map(e => e.src)")[:5]
                detail["img_srcs"] = [
                    (s[:110] + f"...(len={len(s)})") if len(s) > 110 else s for s in raw_srcs
                ]
            except Exception as e:
                detail["read_error"] = str(e)[:200]
            # 只读：收集发布相关控件文案，供实现真实发布时定位按钮
            try:
                btn_js = (
                    "els => els.map(e => (e.innerText || '').trim())"
                    ".filter(Boolean).slice(0, 40)"
                )
                detail["editor_buttons"] = frame.locator("button").evaluate_all(btn_js)
                detail["main_buttons"] = page.locator("button").evaluate_all(btn_js)
                detail["all_frames_buttons"] = {
                    (fr.url or "")[:80]: fr.locator("button").evaluate_all(btn_js)[:20]
                    for fr in page.frames
                    if fr != page.main_frame
                }
            except Exception as e:
                detail["buttons_error"] = str(e)[:150]
            try:
                resp = context.request.get(bc.DRAFT_LIST + "?pn=1&ps=5", timeout=15000)
                drafts = ((resp.json().get("artlist") or {}).get("drafts") or [])
                detail["latest_drafts"] = [
                    {"id": x.get("id"), "title": x.get("title"), "mtime": x.get("mtime")}
                    for x in drafts[:5]
                ]
            except Exception as e:
                detail["drafts_error"] = str(e)[:150]
            result["ok"] = True
            result["verify"] = detail
            print(json.dumps(result, ensure_ascii=False, indent=2))
            browser.close()
            return

        if args.mode == "publish-draft":
            result.update(publish_existing_draft(page, context, args))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            browser.close()
            return

        payload = build_paste_payload(Path(args.file).expanduser(), args.title)
        result["title"] = payload["title"]
        result["images"] = payload["local_images"]

        frame = find_editor_frame(page)
        result["editor_frame"] = (frame.url or "")[:160]

        # 标题
        for fr in (frame, page.main_frame):
            try:
                loc = fr.locator("textarea.title-input__inner")
                if loc.count():
                    loc.first.fill(payload["title"])
                    result["title_filled"] = True
                    break
            except Exception:
                pass

        # 正文：清空草稿残留 → 真实剪贴板 + Ctrl+V → 必要时纯文本兜底
        try:
            frame.wait_for_selector('.tiptap.ProseMirror[contenteditable="true"]', timeout=15000)
            ed = frame.locator('.tiptap.ProseMirror[contenteditable="true"]').first
            marker = payload["plain"][:18]
            ed.click()
            page.keyboard.press("Control+A")
            result["clipboard_write"] = frame.evaluate(
                CLIPBOARD_JS, {"html": payload["html"], "plain": payload["plain"]}
            )
            ed.click()
            page.keyboard.press("Control+V")
            frame.wait_for_timeout(2500)
            if marker not in ed.inner_text():
                # 剪贴板 API 不可用时，至少保证正文文字完整
                ed.click()
                page.keyboard.press("Control+A")
                page.keyboard.insert_text(payload["plain"])
                frame.wait_for_timeout(1200)
                result["fallback"] = "plain text via keyboard.insert_text"
            result["editor_img_count"] = ed.locator("img").count()
            result["editor_text_len"] = len(ed.inner_text())
            result["editor_text_head"] = ed.inner_text()[:200]
            result["content_matches"] = marker in ed.inner_text()
        except Exception as e:
            result["paste_error"] = str(e)[:200]

        # 图片：把二进制写进系统剪贴板再 Ctrl+V，触发编辑器原生图片上传
        try:
            import base64

            ed = frame.locator('.tiptap.ProseMirror[contenteditable="true"]').first
            pasted = []
            for p in payload["local_images"]:
                raw = Path(p).read_bytes()
                mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
                pasted.append(
                    frame.evaluate(
                        CLIPBOARD_IMAGE_JS,
                        {"b64": base64.b64encode(raw).decode(), "mime": mime},
                    )
                )
                ed.click()
                page.keyboard.press("Control+V")
                frame.wait_for_timeout(4000)
            frame.wait_for_timeout(8000)  # 等编辑器把 data URL 异步上传成图床地址
            result["image_paste"] = pasted
            srcs = ed.locator("img").evaluate_all(
                "els => els.slice(0, 6).map(e => e.getAttribute('src') || '')"
            )
            result["native_images"] = [
                (s[:110] + f"...(len={len(s)})") if len(s) > 110 else s for s in srcs
            ]
            result["editor_img_count"] = len(srcs)
        except Exception as e:
            result["native_image_error"] = str(e)[:200]

        if args.mode == "draft":
            for fr in list(page.frames):
                try:
                    btn = fr.get_by_role("button", name=re.compile("保存为草稿|保存草稿"))
                    if btn.count():
                        try:
                            with page.expect_response(
                                lambda r: "article/draft/add" in (r.url or ""), timeout=15000
                            ) as ri:
                                btn.first.click()
                            result["draft_clicked"] = True
                            try:
                                result["save_response"] = ri.value.json()
                            except Exception as e:
                                result["save_body_error"] = str(e)[:150]
                        except Exception as e:
                            result["draft_clicked"] = True
                            result["save_expect_error"] = str(e)[:200]
                        page.wait_for_timeout(4000)
                        break
                except Exception:
                    pass
            result["api_calls"] = api_calls[-12:]
            for fr in list(page.frames):
                try:
                    if "read-editor" in (fr.url or ""):
                        result["editor_tail"] = fr.locator("body").inner_text()[-400:]
                except Exception:
                    pass
            try:
                resp = context.request.get(bc.DRAFT_LIST + "?pn=1&ps=5", timeout=15000)
                d = resp.json()
                drafts = ((d.get("artlist") or {}).get("drafts") or [])
                result["drafts_after"] = [
                    {"id": x.get("id"), "mtime": x.get("mtime"), "title": x.get("title")}
                    for x in drafts
                ]
            except Exception as e:
                result["drafts_check_error"] = str(e)[:150]
            result["ok"] = bool(result.get("draft_clicked"))
            result["page_url_after"] = page.url
        else:
            result["ok"] = False
            result["error"] = "内部错误：未知的写入模式"

        print(json.dumps(result, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
