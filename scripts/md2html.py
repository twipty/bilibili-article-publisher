#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown → B 站专栏正文 HTML（仅标准库）。

B 站专栏 content 字段接受 HTML 字符串，不是 Markdown，也不是 Quill ops。
正文中的图片必须托管在 B 站图床（hdslb.com / biliimg.com），外链图片会导致
整篇稿件被拒（code 37130），因此本模块保留图片 src 原值，由 bili.py 统一
转存后再替换。

本模块不联网、不写文件（render_preview 只返回字符串）。
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

IMG_TAG_RE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)(\2)', re.I | re.S)

_FENCE_RE = re.compile(r"^\s*```")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_ONLY_IMG_RE = re.compile(r"^\s*!\[([^\]]{0,300})\]\(([^)\s]{0,1000})\)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")

_IMG_RE = re.compile(r"!\[([^\]]{0,300})\]\(([^)\s]{0,1000})\)")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]{0,300})\]\(([^)\s]{0,1000})\)")
_CODE_RE = re.compile(r"`([^`\n]{1,500})`")


def _attr_url(u: str) -> str:
    u = re.sub(r"[\x00-\x20\x7f]", "", u or "")
    m = re.match(r"(?i)([a-z][a-z0-9+.\-]*):", u)
    if m and m.group(1).lower() not in ("http", "https", "mailto"):
        return "#"
    return u.replace('"', "%22").replace("'", "%27")


def _alt(s: str) -> str:
    return (s or "").replace('"', "&quot;").replace("'", "&#x27;")


def inline(text: str) -> str:
    """行内 Markdown → HTML。先转义再还原标记，避免 XSS。"""
    t = _html.escape(text, quote=False).replace("\x00", "")
    spans: list[str] = []

    def _stash(m: re.Match) -> str:
        spans.append("<code>" + m.group(1) + "</code>")
        return f"\x00{len(spans) - 1}\x00"

    t = _CODE_RE.sub(_stash, t)

    def _img(m: re.Match) -> str:
        return f'<img src="{_attr_url(m.group(2))}" alt="{_alt(m.group(1))}">'

    def _link(m: re.Match) -> str:
        return f'<a href="{_attr_url(m.group(2))}">{m.group(1)}</a>'

    t = _IMG_RE.sub(_img, t)
    t = _LINK_RE.sub(_link, t)
    t = re.sub(r"\*\*([^*\n]{1,500})\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"~~([^~\n]{1,500})~~", r"<s>\1</s>", t)
    t = re.sub(r"(?<!\*)\*([^*\n]{1,500})\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], t)
    return t


def _table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_html(src: str, demote_h1: bool = True) -> str:
    """把 Markdown 转成专栏 HTML。demote_h1 把 # 降为 h2（标题另有字段）。"""
    lines = (src or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        # 围栏代码块
        if _FENCE_RE.match(line):
            lang = line.strip()[3:].strip()
            i += 1
            code: list[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                code.append(lines[i])
                i += 1
            if i < n:
                i += 1
            attr = f' class="language-{_html.escape(lang, quote=True)}"' if re.fullmatch(r"[A-Za-z0-9_+-]{1,30}", lang) else ""
            out.append("<pre><code" + attr + ">" + _html.escape("\n".join(code)) + "</code></pre>")
            continue

        # 标题
        m = _HEAD_RE.match(line)
        if m:
            level = len(m.group(1))
            if demote_h1:
                level = min(level + 1, 6)
            out.append(f"<h{level}>" + inline(m.group(2).strip()) + f"</h{level}>")
            i += 1
            continue

        # 分割线
        if _HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue

        # 表格（表头 + 分隔行 + 数据行）
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_table_row(lines[i]))
                i += 1
            parts = ["<table><thead><tr>"]
            parts += ["<th>" + inline(c) + "</th>" for c in header]
            parts.append("</tr></thead><tbody>")
            for row in rows:
                parts.append("<tr>")
                parts += ["<td>" + inline(c) + "</td>" for c in row]
                parts.append("</tr>")
            parts.append("</tbody></table>")
            out.append("".join(parts))
            continue

        # 引用（连续 > 行）
        if _QUOTE_RE.match(line):
            buf: list[str] = []
            while i < n and _QUOTE_RE.match(lines[i]):
                buf.append(_QUOTE_RE.match(lines[i]).group(1))
                i += 1
            out.append("<blockquote><p>" + inline(" ".join(buf)) + "</p></blockquote>")
            continue

        # 列表（连续同类项）
        ul = _UL_RE.match(line)
        ol = _OL_RE.match(line) if not ul else None
        if ul or ol:
            pattern = _UL_RE if ul else _OL_RE
            tag = "ul" if ul else "ol"
            items: list[str] = []
            while i < n:
                mm = pattern.match(lines[i])
                if not mm:
                    break
                items.append("<li>" + inline(mm.group(1).strip()) + "</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        # 独占一行的图片：不套 <p>，便于 B 站渲染居中大图
        only = _ONLY_IMG_RE.match(line)
        if only:
            out.append(f'<img src="{_attr_url(only.group(2))}" alt="{_alt(only.group(1))}">')
            i += 1
            continue

        # 段落：连续的普通行合并
        buf = [line.strip()]
        i += 1
        while i < n and lines[i].strip():
            nxt = lines[i]
            if (
                _FENCE_RE.match(nxt)
                or _HEAD_RE.match(nxt)
                or _HR_RE.match(nxt)
                or _QUOTE_RE.match(nxt)
                or _UL_RE.match(nxt)
                or _OL_RE.match(nxt)
                or _ONLY_IMG_RE.match(nxt)
            ):
                break
            if "|" in nxt and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
                break
            buf.append(nxt.strip())
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>")

    return "\n".join(out)


def looks_like_html(s: str) -> bool:
    return bool(
        re.search(
            r"</?(?:p|div|h[1-6]|ul|ol|li|table|blockquote|pre|figure|section|article|hr)\b",
            s or "",
            re.I,
        )
    )


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析极简 YAML frontmatter（不依赖 PyYAML）。未使用 frontmatter 时返回 ({}, 原文)。"""
    text = (text or "").replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text
    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", raw)
        if item and current_list_key:
            meta.setdefault(current_list_key, []).append(_scalar(item.group(1)))
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_list_key = None
        if not value:
            meta[key] = []
            current_list_key = key
            continue
        meta[key] = _scalar(value)
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


def _scalar(v: str) -> Any:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def extract_img_srcs(html: str) -> list[str]:
    return [m.group(3) for m in IMG_TAG_RE.finditer(html or "")]


def replace_img_srcs(html: str, mapping: dict[str, str]) -> str:
    def _sub(m: re.Match) -> str:
        return m.group(1) + m.group(2) + mapping.get(m.group(3), m.group(3)) + m.group(4)

    return IMG_TAG_RE.sub(_sub, html or "")


def drop_img_srcs(html: str, srcs: set[str]) -> str:
    full = re.compile(r"<img\b[^>]*>", re.I | re.S)

    def _sub(m: re.Match) -> str:
        tag = m.group(0)
        mm = IMG_TAG_RE.search(tag)
        if mm and mm.group(3) in srcs:
            return ""
        return tag

    return full.sub(_sub, html or "")


def resolve_image_kind(src: str, base_dir: Path) -> tuple[str, str]:
    """返回 (kind, resolved)。kind ∈ keep/local/remote/invalid。"""
    s = (src or "").strip()
    if not s or s == "#":
        return "invalid", "空图片地址"
    parts = urlsplit(s)
    if parts.scheme in ("http", "https"):
        host = (parts.hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in ("hdslb.com", "biliimg.com", "bilibili.com")):
            return "keep", s
        return "remote", s
    if parts.scheme:
        return "invalid", f"不支持的图片协议 {parts.scheme}"
    local = Path(unquote(parts.path))
    if not local.is_absolute():
        local = (base_dir / local).resolve()
    return ("local", str(local)) if local.exists() and local.is_file() else ("invalid", f"本地图片不存在: {local}")


def visible_text_length(html: str) -> int:
    text = re.sub(r"<[^>]+>", "", html or "")
    text = _html.unescape(text)
    return len(re.sub(r"\s+", "", text))


def check_article(title: str, body_html: str, images: list[str]) -> dict[str, Any]:
    """发布前的轻量合规/质量检查。返回 warnings 与 blockers。"""
    warnings: list[str] = []
    blockers: list[str] = []
    if not title or not title.strip():
        blockers.append("标题为空")
    if len(title or "") > 40:
        warnings.append(f"标题 {len(title)} 字，超过开放平台建议的 40 字上限，Web 端一般可正常提交")
    if len(title or "") < 6:
        warnings.append("标题偏短，建议 10 字以上")
    words = visible_text_length(body_html)
    if words < 200:
        warnings.append(f"正文约 {words} 字，少于 200 字；开放平台通道会拒绝，Web 端可提交但建议补充")
    if not images:
        warnings.append("正文没有图片，属于纯文字稿；若目标是「图文」建议至少配 1 张图")
    return {"warnings": warnings, "blockers": blockers, "visible_chars": words, "image_count": len(images)}


_PREVIEW_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#f6f7f9;color:#1f2329;font:16px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:40px 28px 80px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:36px 40px;box-shadow:0 2px 12px rgba(0,0,0,.04)}
h1.title{font-size:30px;line-height:1.4;margin:0 0 14px;color:#111827}
.meta{color:#6b7280;font-size:13px;border-bottom:1px solid #eef0f3;padding-bottom:18px;margin-bottom:28px}
.badge{display:inline-block;background:#eef4ff;color:#2f6feb;border-radius:6px;padding:2px 9px;margin-right:8px;font-size:12px}
article h2{font-size:23px;margin:34px 0 14px;border-left:4px solid #2f6feb;padding-left:12px}
article h3{font-size:19px;margin:26px 0 12px}
article p{margin:0 0 16px}
article img{max-width:100%;height:auto;border-radius:8px;display:block;margin:18px auto;background:#f3f4f6}
article blockquote{margin:18px 0;padding:12px 18px;background:#f8fafc;border-left:4px solid #cbd5e1;color:#475569}
article pre{background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:16px;overflow:auto;font-size:13px}
article code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
article table{border-collapse:collapse;width:100%;margin:18px 0}
article th,article td{border:1px solid #e5e7eb;padding:8px 12px}
article th{background:#f8fafc}
article a{color:#2f6feb}
.notice{margin-top:22px;padding:14px 18px;background:#fff8e6;border:1px solid #f5d98b;border-radius:10px;color:#7a5b12;font-size:13px}
.imglist{margin-top:22px;padding:14px 18px;background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;font-size:13px;color:#475569;word-break:break-all}
.imglist li{margin:4px 0}
"""


def render_preview(title: str, body_html: str, meta: dict[str, Any], images: list[str],
                   warnings: list[str] | None = None) -> str:
    esc = _html.escape
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    meta_bits = []
    if meta.get("category") not in (None, "", 0, "0"):
        meta_bits.append("分类 " + esc(str(meta["category"])))
    meta_bits.append(f"约 {visible_text_length(body_html)} 字")
    meta_bits.append(f"{len(images)} 张图")
    badge = "".join(f'<span class="badge">{esc(str(t))}</span>' for t in tags[:8])
    notice = ""
    if warnings:
        notice = '<div class="notice"><strong>发布前提示</strong><br>' + "<br>".join(esc(w) for w in warnings) + "</div>"
    imglist = ""
    if images:
        imglist = '<div class="imglist"><strong>图片清单（发布时会自动转存到 B 站图床）</strong><ul>' + "".join(
            f"<li>{esc(s)}</li>" for s in images
        ) + "</ul></div>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 专栏预览</title><style>{_PREVIEW_CSS}</style></head>
<body><div class="wrap"><div class="card">
<h1 class="title">{esc(title)}</h1>
<div class="meta">{badge}{esc(' · '.join(meta_bits))} · 本地预览，尚未提交到 B 站</div>
<article>{body_html}</article>
{notice}{imglist}
</div></div></body></html>"""
