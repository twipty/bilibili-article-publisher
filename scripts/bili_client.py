#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站专栏 Web 端 API 客户端（仅 Python 标准库，无第三方依赖）。

安全约束：
  1. 凭据只写权限 0600 的本地文件，绝不出现在 stdout / 异常文本 / 日志中；
  2. Cookie 只发送到 API_HOSTS 白名单域名；
  3. 禁用自动重定向，避免 Cookie 随 30x 跳转到第三方域名；
  4. 下载外部图片时阻止私网 / 环回 / 保留地址，并限制体积。

这些接口是 B 站 Web 创作中心使用的非公开接口，可能随时变更。
所有字段与已知坑位见 references/api.md。
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

API = "https://api.bilibili.com"
MEMBER = "https://member.bilibili.com"
PASSPORT = "https://passport.bilibili.com"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REFERER = MEMBER + "/"

# 只有这些主机才允许携带登录 Cookie；扫码跨域回调主机也在此列（不带 Cookie）。
API_HOSTS = frozenset(
    {
        "api.bilibili.com",
        "member.bilibili.com",
        "passport.bilibili.com",
        "passport.biligame.com",
        "account.bilibili.com",
        "www.bilibili.com",
    }
)
BILI_CDN_SUFFIXES = (
    "hdslb.com",
    "biliimg.com",
    "bilibili.com",
)
UPCOVER = API + "/x/article/creative/article/upcover"
DRAFT_ADDUPDATE = API + "/x/article/creative/draft/addupdate"
ARTICLE_SUBMIT = API + "/x/article/creative/article/submit"
DRAFT_LIST = API + "/x/article/creative/draft/list"
NAV = API + "/x/web-interface/nav"
QR_GENERATE = PASSPORT + "/x/passport-login/web/qrcode/generate"
QR_POLL = PASSPORT + "/x/passport-login/web/qrcode/poll"
MAX_IMAGE_BYTES = 12 * 1024 * 1024

# 封面布局 tid。错误的 tid 会返回 -17 / 含「分类」的错误，需依次尝试。
TID_CANDIDATES = ("4", "3", "6", "7", "2", "17", "28", "41")


class BiliError(Exception):
    def __init__(self, message: str, code: Any = None, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def default_auth_path() -> Path:
    env = os.environ.get("BILI_AUTH_FILE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".workbuddy" / "secrets" / "bilibili.json"


def default_state_dir() -> Path:
    env = os.environ.get("BILI_STATE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".workbuddy" / "bilibili-article-publisher"


def parse_cookie_string(raw: str) -> dict[str, str]:
    """解析浏览器复制的整段 Cookie 字符串。值保持原样（含 %2C 等编码）。"""
    jar: dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if key.lower() == "cookie":
            # 容忍用户把 "Cookie: " 前缀一起复制进来
            value = value.lstrip()
        jar[key] = value.strip().strip('"')
    return jar


def _parse_set_cookie(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    raws = []
    if hasattr(headers, "get_all"):
        raws = headers.get_all("Set-Cookie") or []
    elif headers.get("Set-Cookie"):
        raws = [headers.get("Set-Cookie")]
    for raw in raws:
        try:
            c = SimpleCookie()
            c.load(raw)
            for morsel in c.values():
                if morsel.value and morsel.value.lower() not in ("deleted", "expired"):
                    out[morsel.key] = morsel.value
        except Exception:
            pass
    return out


def load_auth(path: Path | None = None) -> dict[str, Any]:
    p = path or default_auth_path()
    if not p.exists():
        raise BiliError(
            "未找到登录态文件，请先执行 login qr 或 login cookie",
            detail=str(p),
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise BiliError("登录态文件无法解析", detail=f"{type(e).__name__}: {e}") from None
    cookies = data.get("cookies")
    if not isinstance(cookies, dict) or not cookies.get("SESSDATA") or not cookies.get("bili_jct"):
        raise BiliError("登录态文件缺少 SESSDATA / bili_jct，请重新登录")
    try:
        mode = p.stat().st_mode & 0o077
        if mode:
            os.chmod(p, 0o600)
    except OSError:
        pass
    return data


def save_auth(cookies: dict[str, str], path: Path | None = None, **extra) -> Path:
    p = path or default_auth_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    payload = {"cookies": cookies, "updated_at": _now()}
    payload.update({k: v for k, v in extra.items() if v is not None})
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(p, 0o600)
    return p


# --------------------------------------------------------------------------
# 鉴权 Provider 层
#
# Client 只依赖 AuthProvider.load() -> {"cookies": {...}}，不关心凭据来自本机
# 文件、环境变量注入，还是未来的 WorkBuddy 连接器。切换注入方式只需设置
# BILI_AUTH_PROVIDER；内容转换 / 图片处理 / 发布编排三层完全不用改。
# --------------------------------------------------------------------------

AUTH_PROVIDER_ENV = "BILI_AUTH_PROVIDER"


class AuthProvider:
    """凭据提供者接口。子类实现 load() 与安全摘要 describe()。"""

    name = "base"

    def load(self) -> dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        """给 CLI / 日志用的摘要，绝不能包含 Cookie 值。"""
        return {"provider": self.name}

    @staticmethod
    def ensure_cookies(data: dict[str, Any], require_sessdata: bool = True) -> dict[str, Any]:
        cookies = data.get("cookies") if isinstance(data, dict) else None
        if not isinstance(cookies, dict) or not cookies.get("bili_jct"):
            raise BiliError("登录态缺少 bili_jct（CSRF），请重新登录")
        if require_sessdata and not cookies.get("SESSDATA"):
            raise BiliError("登录态缺少 SESSDATA，请重新登录")
        return data


class FileAuthProvider(AuthProvider):
    """默认：读本机 0600 JSON 文件，对应现有扫码 / Cookie 导入流程。"""

    name = "file"

    def __init__(self, path: Path | None = None):
        self.path = path or default_auth_path()

    def load(self) -> dict[str, Any]:
        return self.ensure_cookies(load_auth(self.path))

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "auth_file": str(self.path), "exists": self.path.exists()}


class ConnectorAuthProvider(AuthProvider):
    """外部注入：供未来的连接器 / 托管凭据系统使用，凭据不落本机磁盘。

    连接器只需把凭据放进下列任一注入点（优先级从高到低）：
      BILI_AUTH_JSON      完整 JSON：{"cookies": {...}} 或直接 {"SESSDATA": ...}
      BILI_COOKIE_STRING  浏览器整段 Cookie 字符串
      BILI_AUTH_SOURCE    指向一个 JSON 文件的路径
    """

    name = "connector"

    @staticmethod
    def _as_auth(obj: Any) -> dict[str, Any]:
        if isinstance(obj, dict) and isinstance(obj.get("cookies"), dict):
            return {"cookies": obj["cookies"]}
        if isinstance(obj, dict):
            return {"cookies": obj}
        raise BiliError("外部注入的凭据必须是 JSON 对象")

    def _read(self) -> tuple[dict[str, Any], str]:
        raw_json = os.environ.get("BILI_AUTH_JSON")
        if raw_json:
            try:
                obj = json.loads(raw_json)
            except Exception as e:
                raise BiliError("BILI_AUTH_JSON 不是合法 JSON", detail=type(e).__name__) from None
            return self._as_auth(obj), "env:BILI_AUTH_JSON"
        raw_cookie = os.environ.get("BILI_COOKIE_STRING")
        if raw_cookie:
            return {"cookies": parse_cookie_string(raw_cookie)}, "env:BILI_COOKIE_STRING"
        src = os.environ.get("BILI_AUTH_SOURCE")
        if src:
            p = Path(src).expanduser()
            if not p.exists():
                raise BiliError("BILI_AUTH_SOURCE 指向的文件不存在", detail=str(p))
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                raise BiliError("BILI_AUTH_SOURCE 文件无法解析", detail=type(e).__name__) from None
            return self._as_auth(obj), "file:" + str(p)
        raise BiliError(
            "connector 鉴权未注入凭据",
            detail="请设置 BILI_AUTH_JSON / BILI_COOKIE_STRING / BILI_AUTH_SOURCE 之一",
        )

    def load(self) -> dict[str, Any]:
        data, _ = self._read()
        return self.ensure_cookies(data)

    def describe(self) -> dict[str, Any]:
        try:
            _, source = self._read()
            return {"provider": self.name, "source": source, "in_memory": True, "ready": True}
        except BiliError:
            return {"provider": self.name, "source": None, "in_memory": True, "ready": False}


class InlineAuthProvider(AuthProvider):
    """显式传入的凭据字典（离线测试 / 库内调用）。"""

    name = "inline"

    def __init__(self, data: dict[str, Any]):
        self.data = data

    def load(self) -> dict[str, Any]:
        # 离线 mock 可能只提供 bili_jct，放宽 SESSDATA 要求
        return self.ensure_cookies(self.data, require_sessdata=False)


_PROVIDERS: dict[str, type[AuthProvider]] = {
    "file": FileAuthProvider,
    "connector": ConnectorAuthProvider,
}


def register_provider(name: str, cls: type[AuthProvider]) -> None:
    """注册自定义 provider，供连接器在进程内注入（不做动态导入，避免越权加载）。"""
    if not (isinstance(name, str) and name.strip() and isinstance(cls, type) and issubclass(cls, AuthProvider)):
        raise ValueError("register_provider 需要 (名称, AuthProvider 子类)")
    _PROVIDERS[name.strip().lower()] = cls


def list_providers() -> list[str]:
    return sorted(_PROVIDERS)


def resolve_provider(name: str | None = None, auth_path: Path | None = None,
                     auth: dict[str, Any] | None = None) -> AuthProvider:
    if auth is not None:
        return InlineAuthProvider(auth)
    key = (name or os.environ.get(AUTH_PROVIDER_ENV) or "file").strip().lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise BiliError(
            "未知的鉴权 provider",
            detail=f"{key!r}；可用: {', '.join(list_providers())}（环境变量 {AUTH_PROVIDER_ENV}）",
        )
    return cls(auth_path) if cls is FileAuthProvider else cls()


def describe_auth(name: str | None = None, auth_path: Path | None = None) -> dict[str, Any]:
    """供 CLI 输出的安全摘要，不含任何凭据值。"""
    return resolve_provider(name, auth_path).describe()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _assert_public_host(host: str) -> None:
    """阻止私网/环回/保留地址，降低 SSRF 风险。"""
    if not host:
        raise BiliError("图片 URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise BiliError("图片域名解析失败", detail=f"{host}: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise BiliError("图片地址指向非公网地址，已拒绝下载", detail=host)


def sniff_image(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif", "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data.startswith(b"BM"):
        return "bmp", "image/bmp"
    raise BiliError("图片格式不受支持（仅 png/jpg/gif/webp/bmp）")


def fetch_image_bytes(url: str, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str, str]:
    """下载外链图片。禁用重定向、限制体积、阻止私网地址。"""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BiliError("图片 URL 仅支持 http/https", detail=url[:120])
    _assert_public_host(parts.hostname or "")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=20) as r:
            if r.status != 200:
                raise BiliError("图片下载失败", code=r.status, detail=url[:120])
            data = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise BiliError("图片下载失败", code=e.code, detail=url[:120]) from None
    except (urllib.error.URLError, OSError) as e:
        raise BiliError("图片下载网络错误", detail=str(getattr(e, "reason", e))) from None
    if len(data) > max_bytes:
        raise BiliError("图片超过 12MB 上限，已跳过", detail=url[:120])
    if not data:
        raise BiliError("图片内容为空", detail=url[:120])
    ext, ct = sniff_image(data)
    return data, ext, ct


def normalize_image_url(url: str) -> str:
    """规范化 upcover 返回的图片地址。

    实测 upcover 会返回 ``http://article.biliimg.com/bfs/new_dyn/...``，
    直接写进正文会被草稿接口以 code 37130「内容包含非法图片链接」拒绝。
    草稿校验只接受 https 且域名在 B 站图床白名单内的地址。
    """
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    if not u.startswith("https://"):
        raise BiliError("图片上传返回了非预期地址", detail=u[:160])
    host = (urllib.parse.urlsplit(u).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in BILI_CDN_SUFFIXES):
        raise BiliError("图片上传返回了非 B 站图床域名", detail=u[:160])
    return u


class Client:
    # 扫码流程用 Client.__new__ 构造无登录态实例，类属性提供安全回退
    provider: AuthProvider | None = None

    def __init__(self, auth: dict[str, Any] | None = None, timeout: int = 30,
                 auth_path: Path | None = None, provider: AuthProvider | None = None):
        # 优先级：显式 provider > 显式 auth 字典 > BILI_AUTH_PROVIDER 环境变量 > 本地文件
        self.provider = provider or resolve_provider(auth_path=auth_path, auth=auth)
        self.auth = self.provider.load()
        self.cookies: dict[str, str] = dict(self.auth.get("cookies") or {})
        self.timeout = timeout
        if not self.cookies.get("bili_jct"):
            raise BiliError("登录态缺少 bili_jct（CSRF），请重新登录")

    @property
    def csrf(self) -> str:
        return self.cookies["bili_jct"]

    def _cookie_header(self, host: str) -> str:
        if host.lower() not in API_HOSTS:
            return ""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v)

    def _request(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, Any] | None = None,
        multipart: tuple[bytes, str] | None = None,
        referer: str = REFERER,
        with_cookie: bool = True,
        extra_headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, Any, bytes]:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host not in API_HOSTS:
            raise BiliError("请求主机不在白名单内，已阻止", detail=host)
        headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Origin": MEMBER,
        }
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form, encoding="utf-8").encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        elif multipart is not None:
            body, ctype = multipart
            headers["Content-Type"] = ctype
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        if with_cookie:
            ch = self._cookie_header(host)
            if ch:
                # unredirected header：配合 _NoRedirect，Cookie 不会随跳转外泄
                req.add_unredirected_header("Cookie", ch)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=timeout or self.timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise BiliError("网络请求失败", detail=str(getattr(e, "reason", e))) from None

    def _request_json(self, method: str, url: str, **kw) -> tuple[int, Any]:
        status, _, raw = self._request(method, url, **kw)
        text = raw.decode("utf-8", "replace")
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, text

    # ---------- 登录态 ----------

    def whoami(self) -> dict[str, Any]:
        status, obj = self._request_json("GET", NAV, referer="https://www.bilibili.com/")
        if not isinstance(obj, dict):
            raise BiliError("登录校验返回异常", code=status, detail=str(obj)[:160])
        if obj.get("code") != 0 or not (obj.get("data") or {}).get("isLogin"):
            raise BiliError(
                "登录态无效或已过期，请重新执行 login qr / login cookie",
                code=obj.get("code"),
                detail=str(obj.get("message", ""))[:160],
            )
        d = obj["data"]
        return {
            "mid": d.get("mid"),
            "uname": d.get("uname"),
            "level": d.get("level_info", {}).get("current_level"),
        }

    def probe(self) -> dict[str, Any]:
        """不写任何数据的连通性检查。"""
        result: dict[str, Any] = {"ok": False, "checks": []}
        try:
            status, obj = self._request_json(
                "GET", QR_GENERATE, with_cookie=False, referer="https://www.bilibili.com/"
            )
            result["checks"].append(
                {"name": "passport 连通", "ok": status == 200 and isinstance(obj, dict) and obj.get("code") == 0, "http": status}
            )
        except BiliError as e:
            result["checks"].append({"name": "passport 连通", "ok": False, "error": e.message})
        try:
            me = self.whoami()
            result["checks"].append({"name": "登录态", "ok": True, "user": me["uname"], "mid": me["mid"]})
            result["ok"] = all(c.get("ok") for c in result["checks"])
        except BiliError as e:
            result["checks"].append({"name": "登录态", "ok": False, "error": e.message, "code": e.code})
        return result

    # ---------- 扫码登录 ----------

    @staticmethod
    def qr_generate() -> dict[str, str]:
        """生成扫码 URL 与 key，不需要登录态。"""
        client = Client.__new__(Client)
        client.auth = {}
        client.cookies = {}
        client.timeout = 20
        status, obj = client._request_json(
            "GET", QR_GENERATE, with_cookie=False, referer="https://www.bilibili.com/"
        )
        if not isinstance(obj, dict) or obj.get("code") != 0:
            raise BiliError("获取二维码失败", code=(obj or {}).get("code") if isinstance(obj, dict) else status)
        data = obj.get("data") or {}
        if not data.get("url") or not data.get("qrcode_key"):
            raise BiliError("二维码响应缺少 url / qrcode_key")
        return {"url": data["url"], "key": data["qrcode_key"]}

    @staticmethod
    def qr_poll(key: str) -> tuple[dict[str, Any], dict[str, str]]:
        """返回 (响应对象, 本次响应下发的 Cookie)。"""
        client = Client.__new__(Client)
        client.auth = {}
        client.cookies = {}
        client.timeout = 20
        url = QR_POLL + "?" + urllib.parse.urlencode({"qrcode_key": key})
        status, headers, raw = client._request(
            "GET", url, with_cookie=False, referer="https://www.bilibili.com/"
        )
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            raise BiliError("扫码状态返回非 JSON", code=status, detail=raw[:160].decode("utf-8", "replace")) from None
        if not isinstance(obj, dict) or obj.get("code") != 0:
            raise BiliError("扫码状态接口异常", code=status, detail=str(obj)[:160])
        return obj.get("data") or {}, _parse_set_cookie(headers)

    @staticmethod
    def qr_finish(data: dict[str, Any], cookies: dict[str, str]) -> dict[str, str]:
        """扫码确认后访问跨域回调 URL，补齐 SESSDATA / bili_jct。"""
        merged = dict(cookies)
        cross = (data or {}).get("url") or ""
        if cross:
            host = (urllib.parse.urlsplit(cross).hostname or "").lower()
            if host in API_HOSTS:
                client = Client.__new__(Client)
                client.auth = {}
                client.cookies = {}
                client.timeout = 20
                try:
                    _, headers, _ = client._request(
                        "GET", cross, with_cookie=False, referer="https://www.bilibili.com/"
                    )
                    merged.update(_parse_set_cookie(headers))
                except BiliError:
                    pass
        # 部分版本的回调 URL 查询串里直接带凭据
        if cross and not merged.get("SESSDATA"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(cross).query)
            for k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5"):
                if qs.get(k):
                    merged[k] = qs[k][0]
        missing = [k for k in ("SESSDATA", "bili_jct") if not merged.get(k)]
        if missing:
            raise BiliError("扫码成功但未取得完整 Cookie", detail="缺少 " + ", ".join(missing))
        return merged

    # ---------- 图片 ----------

    def upload_image(self, data: bytes, filename: str, content_type: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "image.jpg"
        boundary = "----wbbili" + secrets.token_hex(12)
        head = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="binary"; filename="{safe}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'
        ).encode("utf-8")
        tail = (
            f'\r\n--{boundary}\r\n'
            f'Content-Disposition: form-data; name="csrf"\r\n\r\n{self.csrf}\r\n'
            f'--{boundary}--\r\n'
        ).encode("utf-8")
        body = head + data + tail
        status, obj = self._request_json(
            "POST",
            UPCOVER,
            multipart=(body, f"multipart/form-data; boundary={boundary}"),
            timeout=60,
        )
        if not isinstance(obj, dict) or obj.get("code") != 0 or not (obj.get("data") or {}).get("url"):
            msg = obj.get("message") if isinstance(obj, dict) else str(obj)[:120]
            raise BiliError("图片上传失败", code=obj.get("code") if isinstance(obj, dict) else status, detail=str(msg)[:160])
        return normalize_image_url(str(obj["data"]["url"]))


    def upload_file(self, path: Path) -> str:
        data = path.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            raise BiliError("本地图片超过 12MB 上限", detail=str(path))
        ext, ct = sniff_image(data)
        return self.upload_image(data, path.name, ct)

    def upload_url(self, url: str) -> str:
        data, ext, ct = fetch_image_bytes(url)
        return self.upload_image(data, f"remote.{ext}", ct)

    # ---------- 稿件 ----------

    def base_fields(self, title: str, content: str, **overrides) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "title": title,
            "content": content,
            "csrf": self.csrf,
            "category": "0",
            "list_id": "0",
            "reprint": "0",
            "original": "1",
            "media_id": "0",
            "spoiler": "0",
            "save": "0",
            "pgc_id": "0",
        }
        for k, v in overrides.items():
            if v is not None:
                fields[k] = v
        return fields

    def save_draft(self, base: dict[str, Any], tids=TID_CANDIDATES) -> tuple[str, str, list[dict[str, Any]]]:
        """保存草稿。返回 (aid, 生效的 tid, 尝试记录)。"""
        attempts: list[dict[str, Any]] = []
        last = ""
        for tid in tids:
            code = None
            body = dict(base, tid=tid)
            status, obj = self._request_json("POST", DRAFT_ADDUPDATE, form=body)
            if isinstance(obj, dict) and obj.get("code") == 0:
                aid = (obj.get("data") or {}).get("aid")
                if aid:
                    attempts.append({"tid": tid, "ok": True, "aid": aid})
                    return str(aid), tid, attempts
                last = "草稿保存成功但未返回 aid"
            else:
                code = obj.get("code") if isinstance(obj, dict) else None
                msg = str(obj.get("message", "") if isinstance(obj, dict) else obj)[:160]
                last = f"code={code} {msg} (HTTP {status})"
            attempts.append({"tid": tid, "ok": False, "detail": last})
            # 仅「分类/封面布局」类错误换 tid 重试；其它错误（登录、风控、参数）不重试
            if code != -17 and "分类" not in msg:
                break
        raise BiliError("保存草稿失败", detail=last)

    def submit(self, base: dict[str, Any], aid: str, tid: str) -> tuple[int, Any, bool]:
        """提交公开发布。返回 (http_status, 响应, 是否成功)。"""
        body = dict(base, tid=tid, aid=aid)
        status, obj = self._request_json("POST", ARTICLE_SUBMIT, form=body)
        ok = isinstance(obj, dict) and obj.get("code") == 0
        return status, obj, ok

    def list_drafts(self, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        url = DRAFT_LIST + "?" + urllib.parse.urlencode({"pn": page, "ps": limit})
        status, obj = self._request_json("GET", url)
        if not isinstance(obj, dict) or obj.get("code") not in (0, None):
            raise BiliError(
                "读取草稿列表失败",
                code=obj.get("code") if isinstance(obj, dict) else status,
                detail=str(obj.get("message", "") if isinstance(obj, dict) else obj)[:160],
            )
        artlist = (obj.get("data") or {}).get("artlist") or obj.get("artlist") or {}
        items = artlist.get("drafts") or []
        return [
            {
                "aid": x.get("id"),
                "title": x.get("title"),
                "edit_url": f"{MEMBER}/article-text/home?aid={x.get('id')}",
            }
            for x in items
            if isinstance(x, dict)
        ]


def render_qrcode(url: str, out: Path) -> dict[str, Any]:
    """尽力生成二维码 PNG。返回 {"method": ..., "path"/"url": ...}。

    优先 qrcode 库，其次系统 qrencode 命令；都不可用时返回原始 URL，
    由调用方引导用户手动复制或使用网页工具，不会因此中断登录流程。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import qrcode  # type: ignore

        img = qrcode.make(url)
        img.save(str(out))
        return {"method": "python-qrcode", "path": str(out)}
    except ImportError:
        pass
    except Exception:
        pass
    import shutil
    import subprocess

    if shutil.which("qrencode"):
        try:
            subprocess.run(
                ["qrencode", "-t", "PNG", "-o", str(out), url],
                check=True,
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if out.exists():
                return {"method": "qrencode", "path": str(out)}
        except Exception:
            pass
    return {"method": "url-only", "url": url}
