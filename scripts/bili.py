#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站专栏图文自动发布 CLI（WorkBuddy skill: bilibili-article-publisher）。

仅使用 Python 标准库。所有写操作默认 dry-run；只有把 --confirm 作为
**最后一个参数** 才会真正写入 B 站账号，且默认只保存草稿，
必须额外显式 --publish 才会公开发布。

用法见 SKILL.md 与 references/api.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bili_client as bc  # noqa: E402
import md2html as m2h  # noqa: E402

RAW = sys.argv[1:]
CONFIRM = bool(RAW) and RAW[-1] == "--confirm"
ARGV = RAW[:-1] if CONFIRM else list(RAW)


def out(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def warn(msg: str) -> None:
    print("[bili] " + msg, file=sys.stderr)


def fail(msg: str, **extra) -> "NoReturn":  # type: ignore[name-defined]
    payload = {"ok": False, "error": msg, "confirmed": CONFIRM}
    payload.update(extra)
    out(payload)
    sys.exit(1)


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s or "").strip("-")
    return (s[:48] or "article")


def log_path() -> Path:
    return bc.default_state_dir() / "publish_log.jsonl"


def append_log(record: dict[str, Any]) -> None:
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_log() -> list[dict[str, Any]]:
    p = log_path()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def find_prior(hash_: str) -> dict[str, Any] | None:
    for row in reversed(read_log()):
        if row.get("hash") == hash_ and row.get("status") in ("draft", "published"):
            return row
    return None


# --------------------------------------------------------------------------
# 稿件读取与构建
# --------------------------------------------------------------------------


def load_article(file: Path, cli_title: str | None, no_demote: bool = False) -> dict[str, Any]:
    if not file.exists() or not file.is_file():
        raise bc.BiliError("稿件文件不存在", detail=str(file))
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise bc.BiliError("稿件不是 UTF-8 文本", detail=str(file)) from None
    meta, body = m2h.parse_frontmatter(text)
    title = cli_title or meta.get("title")
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.M)
        if m:
            title = m.group(1).strip()
            body = re.sub(r"^#\s+.+$\n?", "", body, count=1, flags=re.M)
    if not title:
        title = file.stem
    body_html = body if m2h.looks_like_html(body) else m2h.markdown_to_html(body, demote_h1=not no_demote)
    return {
        "title": str(title).strip(),
        "meta": meta,
        "body_html": body_html.strip(),
        "source": str(file.resolve()),
        "base_dir": file.resolve().parent,
    }


def article_hash(title: str, body_html: str) -> str:
    return hashlib.sha256((title + "\n" + body_html).encode("utf-8")).hexdigest()[:24]


def build_outputs(article: dict[str, Any], out_dir: Path | None = None) -> dict[str, Any]:
    title = article["title"]
    images = m2h.extract_img_srcs(article["body_html"])
    check = m2h.check_article(title, article["body_html"], images)
    d = out_dir or (bc.default_state_dir() / "previews")
    d.mkdir(parents=True, exist_ok=True)
    stem = slugify(title)
    preview = d / f"{stem}.preview.html"
    body_file = d / f"{stem}.body.html"
    preview.write_text(
        m2h.render_preview(title, article["body_html"], article["meta"], images, check["warnings"]),
        encoding="utf-8",
    )
    body_file.write_text(article["body_html"], encoding="utf-8")
    return {
        "preview_path": str(preview),
        "body_path": str(body_file),
        "images": images,
        "check": check,
    }


def is_external(src: str) -> bool:
    parts = urlsplit(src or "")
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return not any(host == d or host.endswith("." + d) for d in ("hdslb.com", "biliimg.com", "bilibili.com"))


def describe_images(article: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for src in dict.fromkeys(m2h.extract_img_srcs(article["body_html"])):
        kind, resolved = m2h.resolve_image_kind(src, article["base_dir"])
        result.append({"src": src, "kind": kind, "resolved": resolved})
    return result


def rehost_images(article: dict[str, Any], client, skip_failed: bool, no_rehost: bool) -> tuple[str, list[dict[str, Any]], list[str]]:
    """把正文图片转存到 B 站图床。返回 (新 HTML, 处理明细, 阻断原因)。"""
    html = article["body_html"]
    mapping: dict[str, str] = {}
    dropped: set[str] = set()
    details: list[dict[str, Any]] = []
    blockers: list[str] = []
    for src in dict.fromkeys(m2h.extract_img_srcs(html)):
        kind, resolved = m2h.resolve_image_kind(src, article["base_dir"])
        if kind == "keep":
            details.append({"src": src, "action": "keep", "note": "已是 B 站图床地址"})
            continue
        if kind == "remote" and no_rehost:
            details.append({"src": src, "action": "kept-external", "note": "--no-rehost 指定保留外链"})
            continue
        try:
            if kind == "local":
                url = client.upload_file(Path(resolved))
            elif kind == "remote":
                url = client.upload_url(resolved)
            else:
                raise bc.BiliError(resolved)
            mapping[src] = url
            details.append({"src": src, "action": "uploaded", "url": url})
            time.sleep(0.3)  # 轻微限速，规避 upcover 的 code -1
        except bc.BiliError as e:
            if skip_failed:
                dropped.add(src)
                details.append({"src": src, "action": "dropped", "error": e.message, "detail": e.detail})
                warn(f"图片已丢弃: {src} ({e.message})")
            else:
                blockers.append(f"图片处理失败: {src} → {e.message} {e.detail}".strip())
                details.append({"src": src, "action": "failed", "error": e.message, "detail": e.detail})
    html = m2h.replace_img_srcs(html, mapping)
    html = m2h.drop_img_srcs(html, dropped)
    leftover = [s for s in m2h.extract_img_srcs(html) if is_external(s)]
    if leftover:
        blockers.append(
            "正文仍含外链图片，B 站会以 code 37130 拒绝整篇稿件；请去除 --no-rehost 或改用 --skip-failed-images"
        )
        details.append({"action": "leftover", "srcs": leftover})
    return html, details, blockers


# --------------------------------------------------------------------------
# 发布主流程
# --------------------------------------------------------------------------


def publish_article(args, client=None, quiet: bool = False) -> dict[str, Any]:
    file = Path(args.file).expanduser()
    article = load_article(file, args.title, getattr(args, "no_demote_h1", False))
    built = build_outputs(article, Path(args.out_dir).expanduser() if getattr(args, "out_dir", None) else None)
    images = describe_images(article)
    mode = "publish" if getattr(args, "publish", False) else "draft"
    result: dict[str, Any] = {
        "ok": True,
        "confirmed": CONFIRM,
        "mode": mode,
        "title": article["title"],
        "source": article["source"],
        "visible_chars": built["check"]["visible_chars"],
        "image_count": len(built["images"]),
        "images": images,
        "warnings": built["check"]["warnings"],
        "blockers": built["check"]["blockers"],
        "preview_path": built["preview_path"],
        "body_path": built["body_path"],
    }

    if not CONFIRM:
        result["dry_run"] = True
        result["would_do"] = (
            "仅预览，不写 B 站。要写入请把 --confirm 作为最后一个参数；"
            "默认只保存草稿，公开提交需额外 --publish。"
        )
        return result

    # 2026-11 新版专栏会拒绝 HTML 正文里的 <img>（code 37130），
    # upcover 返回的 article.biliimg.com/bfs/new_dyn/ 外链也不被接受。
    # 带图稿件必须走浏览器通道，由编辑器自己上传图片。
    if client is None and images:
        result["ok"] = False
        result["error"] = (
            "新版专栏（2026-11）拒绝 HTML <img> 图片，老接口只能保存纯文字稿"
        )
        result["hint"] = "请改用 scripts/opus_browser.py draft --file <稿件>"
        return result

    if built["check"]["blockers"]:
        result["ok"] = False
        result["error"] = "稿件检查未通过"
        return result

    hash_ = article_hash(article["title"], article["body_html"])
    prior = find_prior(hash_)
    if prior and not args.force:
        result["skipped"] = True
        result["reason"] = "相同内容此前已提交，避免重复发布（可用 --force 覆盖）"
        result["prior"] = {k: prior.get(k) for k in ("aid", "url", "edit_url", "status", "ts")}
        return result

    try:
        client = client or bc.Client()
        me = client.whoami()
        if not quiet:
            warn(f"账号校验通过: {me.get('uname')} (mid={me.get('mid')})")

        body_html, img_details, img_blockers = rehost_images(
            article, client, args.skip_failed_images, args.no_rehost
        )
        result["image_actions"] = img_details
        if img_blockers:
            result["ok"] = False
            result["error"] = "图片处理未通过"
            result["blockers"] = built["check"]["blockers"] + img_blockers
            return result

        # 重新生成预览，展示转存后的真实图片地址
        built2 = build_outputs({**article, "body_html": body_html},
                               Path(args.out_dir).expanduser() if getattr(args, "out_dir", None) else None)
        result["preview_path"] = built2["preview_path"]

        overrides: dict[str, Any] = {
            "category": args.category,
            "original": article["meta"].get("original", 1),
        }
        if article["meta"].get("tags"):
            tags = article["meta"]["tags"]
            overrides["tags"] = ",".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
        if article["meta"].get("summary"):
            overrides["summary"] = str(article["meta"]["summary"])
        if article["meta"].get("dynamic"):
            overrides["dynamic_intro"] = str(article["meta"]["dynamic"])

        tids = bc.TID_CANDIDATES
        if args.cover:
            cover = Path(args.cover).expanduser()
            if not cover.is_absolute() and not cover.exists():
                cover = (article["base_dir"] / cover).resolve()
            if not cover.exists():
                raise bc.BiliError("封面图片不存在", detail=str(cover))
            cover_url = client.upload_file(cover)
            overrides["image_urls"] = cover_url
            tids = ("4",)  # 单图封面布局
            result["cover_url"] = cover_url

        base = client.base_fields(article["title"], body_html, **overrides)
        aid, tid, attempts = client.save_draft(base, tids)
        result["aid"] = aid
        result["tid"] = tid
        result["tid_attempts"] = attempts
        result["edit_url"] = f"{bc.MEMBER}/article-text/home?aid={aid}"
        status = "draft"
        result["published"] = False

        if mode == "publish":
            http_status, resp, ok = client.submit(base, aid, tid)
            result["submit_http"] = http_status
            result["submit_response_code"] = resp.get("code") if isinstance(resp, dict) else None
            if ok:
                result["published"] = True
                result["url"] = f"https://www.bilibili.com/read/cv{aid}"
                status = "published"
            else:
                msg = str(resp.get("message", "") if isinstance(resp, dict) else resp)[:200]
                status = "submit_blocked"
                result["error"] = (
                    f"公开提交被拒（HTTP {http_status}，通常 412 为反爬风控）。草稿已保存，"
                    f"请在网页编辑器完成发布。详情: {msg}"
                )
                warn(result["error"])

        record = {
            "ts": bc._now(),
            "hash": hash_,
            "title": article["title"],
            "source": article["source"],
            "aid": aid,
            "url": result.get("url"),
            "edit_url": result["edit_url"],
            "status": status,
            "mode": mode,
            "user": me.get("uname"),
        }
        append_log(record)
        result["record"] = record
        result["ok"] = status != "submit_blocked"
        return result
    except bc.BiliError as e:
        result["ok"] = False
        result["error"] = e.message
        if e.code is not None:
            result["code"] = e.code
        if e.detail:
            result["detail"] = e.detail
        return result


# --------------------------------------------------------------------------
# 各子命令
# --------------------------------------------------------------------------


def _pending_path() -> Path:
    return bc.default_state_dir() / "qr_pending.json"


def _save_pending(key: str, url: str) -> Path:
    """把未完成的扫码会话存成 0600 文件，供跨对话轮次继续轮询。"""
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"key": key, "url": url, "created_at": time.time(), "expires_at": time.time() + 180}
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.chmod(p, 0o600)
    return p


def _load_pending() -> dict[str, Any] | None:
    p = _pending_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if d.get("key") else None
    except Exception:
        return None


def _clear_pending() -> None:
    try:
        _pending_path().unlink()
    except OSError:
        pass


def _poll_login(key: str, timeout: int) -> dict[str, Any]:
    """轮询扫码状态直至成功 / 失效 / 超时。成功后保存登录态。"""
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        try:
            data, cookies = bc.Client.qr_poll(key)
        except bc.BiliError as e:
            return {"ok": False, "error": e.message, "detail": e.detail}
        state = data.get("code")
        if state != last_state:
            warn(f"扫码状态: {state} {data.get('message', '')}")
            last_state = state
        if state == 0:
            try:
                jar = bc.Client.qr_finish(data, cookies)
                path = bc.save_auth(jar, refresh_token=data.get("refresh_token"))
                me = bc.Client().whoami()
                _clear_pending()
                return {
                    "ok": True,
                    "stage": "logged_in",
                    "auth_file": str(path),
                    "file_mode": "0600",
                    "user": me,
                    "hint": "登录态已保存。请执行 preflight 验证。",
                }
            except bc.BiliError as e:
                return {"ok": False, "error": e.message, "detail": e.detail}
        if state == 86038:
            _clear_pending()
            return {"ok": False, "error": "二维码已过期，请重新执行 login qr"}
        time.sleep(2)
    return {
        "ok": False,
        "error": f"等待扫码超时（{timeout}s）",
        "hint": "若随后完成扫码，可再执行一次 login qr-poll；若二维码已失效，重新 login qr --stage-only",
    }


def cmd_login_qr(args) -> dict[str, Any]:
    info = bc.Client.qr_generate()
    qr_out = Path(args.out).expanduser() if args.out else (bc.default_state_dir() / "login_qr.png")
    qr = bc.render_qrcode(info["url"], qr_out)
    payload: dict[str, Any] = {
        "ok": True,
        "stage": "waiting_for_scan",
        "qrcode": qr,
        "qrcode_url": info["url"],
        "expires_in_seconds": 180,
    }
    if qr.get("path"):
        payload["qrcode_image"] = qr["path"]
        warn("二维码已生成: " + qr["path"])
    else:
        warn("未检测到 qrcode 库或 qrencode，稍后请安装任一即可显示二维码图片")
    if args.stage_only:
        _save_pending(info["key"], info["url"])
        payload["hint"] = "用 B 站 App 扫码并确认，随后执行 login qr-poll 完成登录。二维码 180 秒内有效。"
        payload["next_command"] = "login qr-poll"
        return payload
    payload["hint"] = "用 B 站 App 扫码并在手机上确认。若只拿到 URL，可用任意二维码工具生成后扫描。"
    # 阻塞模式下先输出二维码信息，再输出结果（两行 JSON，调用方需流式读取）
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    final = _poll_login(info["key"], args.timeout)
    print(json.dumps(final, ensure_ascii=False), flush=True)
    return {"__already_printed__": True, "ok": final.get("ok")}


def cmd_login_qr_poll(args) -> dict[str, Any]:
    """读取上次保存的扫码会话，继续完成登录（适合 Agent 跨轮次交互）。"""
    pending = _load_pending()
    if not pending:
        return {"ok": False, "error": "没有待确认的扫码会话，请先执行 login qr --stage-only"}
    remain = int(pending.get("expires_at", 0) - time.time())
    if remain <= 0:
        _clear_pending()
        return {"ok": False, "error": "二维码已超过 180 秒有效期，请重新执行 login qr --stage-only"}
    return _poll_login(pending["key"], min(args.timeout, remain))


def cmd_login_cookie(args) -> dict[str, Any]:
    raw = ""
    if args.cookie:
        raw = args.cookie
    elif args.cookie_file:
        raw = Path(args.cookie_file).expanduser().read_text(encoding="utf-8")
    elif os.environ.get("BILIBILI_COOKIE"):
        raw = os.environ["BILIBILI_COOKIE"]
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    jar = bc.parse_cookie_string(raw)
    missing = [k for k in ("SESSDATA", "bili_jct") if not jar.get(k)]
    if missing:
        fail("Cookie 缺少必要字段: " + ", ".join(missing) + "（请从浏览器复制完整 Cookie 字符串）")
    path = bc.save_auth(jar)
    try:
        me = bc.Client().whoami()
    except bc.BiliError as e:
        fail("Cookie 校验失败: " + e.message, detail=e.detail)
    return {
        "ok": True,
        "auth_file": str(path),
        "file_mode": "0600",
        "cookie_keys": sorted(jar.keys()),
        "user": me,
        "hint": "凭据以 0600 权限保存在技能目录之外，不会打印 Cookie 值。",
    }


def cmd_logout(args) -> dict[str, Any]:
    p = bc.default_auth_path()
    if p.exists():
        p.unlink()
        return {"ok": True, "removed": str(p)}
    return {"ok": True, "removed": None, "note": "本来就没有登录态文件"}


def cmd_whoami(args) -> dict[str, Any]:
    client = bc.Client()
    me = client.whoami()
    desc = client.provider.describe() if client.provider else {"provider": "inline"}
    result: dict[str, Any] = {"ok": True, "user": me, "auth": desc}
    if client.provider and isinstance(client.provider, bc.FileAuthProvider):
        result["auth_file"] = str(client.provider.path)
    return result


def cmd_auth_info(args) -> dict[str, Any]:
    """只读展示当前鉴权注入方式，绝不输出任何 Cookie 值。"""
    return {
        "ok": True,
        "active_env": bc.AUTH_PROVIDER_ENV + "=" + (os.environ.get(bc.AUTH_PROVIDER_ENV) or "file(默认)"),
        "auth": bc.describe_auth(),
        "available_providers": bc.list_providers(),
        "hint": "切换注入方式：BILI_AUTH_PROVIDER=connector + BILI_AUTH_JSON / BILI_COOKIE_STRING / BILI_AUTH_SOURCE",
    }


def cmd_preflight(args) -> dict[str, Any]:
    desc = bc.describe_auth()
    result: dict[str, Any] = {
        "ok": True,
        "python": sys.version.split()[0],
        "stdlib_only": True,
        "auth": desc,
    }
    if desc.get("provider") == "file":
        result["auth_file"] = desc.get("auth_file")
        result["auth_exists"] = desc.get("exists")
    try:
        import importlib.util

        result["qrcode_library"] = bool(importlib.util.find_spec("qrcode"))
        result["qrencode_binary"] = bool(__import__("shutil").which("qrencode"))
    except Exception:
        result["qrcode_library"] = False
        result["qrencode_binary"] = False
    try:
        result["api_probe"] = bc.Client().probe()
        result["ok"] = bool(result["api_probe"].get("ok"))
    except bc.BiliError as e:
        result["ok"] = False
        result["api_probe"] = {"ok": False, "error": e.message, "detail": e.detail}
    return result


def cmd_build(args) -> dict[str, Any]:
    article = load_article(Path(args.file).expanduser(), args.title, args.no_demote_h1)
    built = build_outputs(article, Path(args.out_dir).expanduser() if args.out_dir else None)
    return {
        "ok": True,
        "title": article["title"],
        "body_html_chars": len(article["body_html"]),
        "preview_path": built["preview_path"],
        "body_path": built["body_path"],
        "images": describe_images(article),
        "check": built["check"],
        "note": "build 完全离线，不接触 B 站账号。",
    }


def cmd_publish(args) -> dict[str, Any]:
    return publish_article(args)


def cmd_drafts(args) -> dict[str, Any]:
    items = bc.Client().list_drafts(args.page, args.limit)
    return {"ok": True, "page": args.page, "count": len(items), "drafts": items}


def cmd_queue(args) -> dict[str, Any]:
    d = Path(args.dir).expanduser()
    if not d.is_dir():
        fail("待发布目录不存在: " + str(d))
    files = sorted(p for p in d.rglob("*.md") if not p.name.startswith("."))
    files = [p for p in files if not (p.with_suffix(".skip").exists() or p.with_suffix(".published").exists())]
    if args.limit > 0:
        files = files[: args.limit]
    summary: list[dict[str, Any]] = []
    for f in files:
        try:
            article = load_article(f, None)
            built = build_outputs(article, Path(args.out_dir).expanduser() if args.out_dir else None)
            entry: dict[str, Any] = {
                "file": str(f),
                "title": article["title"],
                "preview_path": built["preview_path"],
                "visible_chars": built["check"]["visible_chars"],
                "images": len(built["images"]),
                "blockers": built["check"]["blockers"],
            }
            if CONFIRM:
                sub = argparse.Namespace(
                    file=str(f), title=None, out_dir=args.out_dir, no_demote_h1=False,
                    publish=bool(args.publish), force=args.force,
                    skip_failed_images=args.skip_failed_images, no_rehost=args.no_rehost,
                    cover=None, category="0",
                )
                r = publish_article(sub, quiet=True)
                entry["result"] = {
                    "ok": r.get("ok"), "aid": r.get("aid"), "url": r.get("url"),
                    "status": (r.get("record") or {}).get("status"),
                    "skipped": r.get("skipped"), "error": r.get("error"),
                    "edit_url": r.get("edit_url"),
                }
                if r.get("published"):
                    try:
                        f.with_suffix(".published").write_text(r.get("url") or "published", encoding="utf-8")
                    except OSError:
                        pass
            else:
                entry["dry_run"] = True
            summary.append(entry)
        except bc.BiliError as e:
            summary.append({"file": str(f), "ok": False, "error": e.message})
        except Exception as e:  # 单篇失败不应中断整个队列
            summary.append({"file": str(f), "ok": False, "error": f"{type(e).__name__}: {e}"})
    return {
        "ok": True,
        "confirmed": CONFIRM,
        "mode": "publish" if args.publish else "draft",
        "dir": str(d),
        "total": len(summary),
        "items": summary,
        "note": "无 --confirm 时全部为 dry-run；--confirm 默认仍只存草稿。",
    }


def cmd_status(args) -> dict[str, Any]:
    rows = read_log()
    return {"ok": True, "log_file": str(log_path()), "total": len(rows), "recent": rows[-args.limit:]}


# --------------------------------------------------------------------------
# 参数与入口
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bili.py", description="B 站专栏图文自动发布（默认 dry-run）")
    sub = p.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="登录态管理")
    lsub = login.add_subparsers(dest="login_method", required=True)
    qr = lsub.add_parser("qr", help="扫码登录")
    qr.add_argument("--out", help="二维码 PNG 输出路径")
    qr.add_argument("--timeout", type=int, default=180, help="等待扫码秒数，默认 180")
    qr.add_argument("--stage-only", action="store_true",
                    help="只生成二维码并保存会话，不阻塞；随后用 login qr-poll 完成登录")
    qp = lsub.add_parser("qr-poll", help="继续完成上次生成的扫码登录")
    qp.add_argument("--timeout", type=int, default=120, help="本次最多等待秒数，默认 120")
    ck = lsub.add_parser("cookie", help="导入浏览器 Cookie 字符串")
    ck.add_argument("--cookie", help="Cookie 字符串（注意会进入 shell 历史，推荐 --cookie-file）")
    ck.add_argument("--cookie-file", help="存放 Cookie 字符串的文本文件")
    sub.add_parser("logout", help="删除本地登录态")
    sub.add_parser("whoami", help="校验当前登录账号")
    sub.add_parser("preflight", help="环境 / 网络 / 登录态预检")
    sub.add_parser("auth-info", help="查看当前鉴权 Provider（不输出凭据值）")

    b = sub.add_parser("build", help="离线生成专栏 HTML 与预览页")
    b.add_argument("--file", required=True, help="Markdown 或 HTML 稿件路径")
    b.add_argument("--title", help="覆盖标题")
    b.add_argument("--out-dir", help="预览输出目录")
    b.add_argument("--no-demote-h1", action="store_true", help="不把 # 降级为 h2")

    pub = sub.add_parser("publish", help="发布单篇（默认 dry-run / 默认只存草稿）")
    pub.add_argument("--file", required=True)
    pub.add_argument("--title")
    pub.add_argument("--out-dir")
    pub.add_argument("--publish", action="store_true", help="公开提交；不加则只保存草稿")
    pub.add_argument("--cover", help="封面图片路径，设置后使用单图封面布局")
    pub.add_argument("--category", default="0", help="专栏分类 ID，默认 0")
    pub.add_argument("--skip-failed-images", action="store_true", help="上传失败时丢弃该图而不是中止")
    pub.add_argument("--no-rehost", action="store_true", help="保留外链图片（很可能被 B 站 37130 拒绝）")
    pub.add_argument("--force", action="store_true", help="忽略重复发布幂等检查")
    pub.add_argument("--no-demote-h1", action="store_true")

    dr = sub.add_parser("drafts", help="列出创作中心草稿")
    dr.add_argument("--page", type=int, default=1)
    dr.add_argument("--limit", type=int, default=50)

    q = sub.add_parser("queue", help="批量处理目录中的待发布稿件")
    q.add_argument("--dir", required=True, help="待发布目录（递归扫描 *.md）")
    q.add_argument("--out-dir")
    q.add_argument("--publish", action="store_true", help="公开提交；不加则只保存草稿")
    q.add_argument("--limit", type=int, default=0, help="本次最多处理篇数，0 表示不限")
    q.add_argument("--force", action="store_true")
    q.add_argument("--skip-failed-images", action="store_true")
    q.add_argument("--no-rehost", action="store_true")

    st = sub.add_parser("status", help="查看本地发布记录")
    st.add_argument("--limit", type=int, default=20)
    return p


def main() -> None:
    if "--confirm" in ARGV:
        fail("--confirm 只能作为最后一个参数；已阻止本次写入")
    parser = build_parser()
    try:
        args = parser.parse_args(ARGV)
    except SystemExit:
        raise
    handlers = {
        "whoami": cmd_whoami,
        "logout": cmd_logout,
        "preflight": cmd_preflight,
        "auth-info": cmd_auth_info,
        "build": cmd_build,
        "publish": cmd_publish,
        "drafts": cmd_drafts,
        "queue": cmd_queue,
        "status": cmd_status,
    }
    try:
        if args.command == "login":
            if args.login_method == "qr":
                r = cmd_login_qr(args)
            elif args.login_method == "qr-poll":
                r = cmd_login_qr_poll(args)
            else:
                r = cmd_login_cookie(args)
        else:
            r = handlers[args.command](args)
        if not r.get("__already_printed__"):
            out(r)
        if r.get("ok") is False:
            sys.exit(1)
    except bc.BiliError as e:
        fail(e.message, code=e.code, detail=e.detail)
    except KeyboardInterrupt:
        fail("已中断")
    except Exception as e:
        fail(f"未预期错误: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
