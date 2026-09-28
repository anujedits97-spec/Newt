"""Cookie-free-first TeraBox public-share resolver.

The resolver starts from the public share page, extracts the shorturl and
page-generated jsToken/dp-logid values, then calls the public share/list API.
No cookie is required by this module.  A caller-supplied Cookie header may be
used as an optional fallback, but it is never required at startup.
"""
from __future__ import annotations

import html
import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

import requests

logger = logging.getLogger("terabox_resolver")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

SUPPORTED_HOST_RE = re.compile(
    r"(^|\.)(terabox(?:\.com|\.app)|1024terabox\.com|terasharelink\.com|"
    r"teraboxapp\.com|nephobox\.com|4funbox\.com|mirrobox\.com|momerybox\.com)$",
    re.I,
)

PAGE_HOSTS = (
    "www.terabox.com",
    "www.terabox.app",
    "www.1024terabox.com",
    "www.terasharelink.com",
)

LIST_HOSTS = (
    "www.terabox.com",
    "www.terabox.app",
    "www.1024terabox.com",
)


def is_terabox_link(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return bool(SUPPORTED_HOST_RE.search(host))
    except Exception:
        return False


def _clean_url(url: str) -> str:
    url = html.unescape(url.strip())
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _extract_surl(value: str) -> str | None:
    p = urlparse(value)
    q = parse_qs(p.query)
    for key in ("surl", "shorturl", "shortUrl"):
        if q.get(key):
            return q[key][0].strip()

    # Normal share URLs are /s/<short-id>. Some redirects use /sharing/link?surl=.
    m = re.search(r"/(?:s|share)/([^/?#]+)", p.path, re.I)
    if m:
        token = m.group(1)
        if token and token.lower() not in {"list", "download", "share"}:
            return token
    return None


def _extract_first(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            value = html.unescape(m.group(1)).strip()
            if value:
                return value
    return None


def _extract_page_tokens(text: str) -> tuple[str | None, str | None, str | None]:
    # Different TeraBox frontends have used several serializations over time.
    js = _extract_first([
        r"window\.jsToken\s*=\s*['\"]([^'\"]+)['\"]",
        r"window\.jsToken\s*=\s*%22([^%]+)%22",
        r"jsToken\\?['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r"fn%28%22([^%]+)%22%29",
        r"fn\(\"([^\"]+)\"\)",
        r"[?&]jsToken=([^&\"']+)",
    ], text)
    dp = _extract_first([
        r"dp-logid\s*[=:]\s*['\"]?([0-9]+)",
        r"dp-logid=([0-9]+)",
    ], text)
    bdstoken = _extract_first([
        r"bdstoken\\?['\"]?\s*:\s*['\"]([^'\"]+)['\"]",
        r"bdstoken\s*=\s*['\"]([^'\"]+)['\"]",
    ], text)
    return js, dp, bdstoken


def _headers(origin: str, referer: str | None = None) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Referer": referer or origin + "/",
        "Origin": origin,
        "Connection": "keep-alive",
    }


def _json_response(response: requests.Response) -> dict:
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise RuntimeError(f"TeraBox returned non-JSON response (HTTP {response.status_code})")


def _request_share_page(session: requests.Session, url: str) -> tuple[requests.Response, str | None]:
    last_error = None
    for host in PAGE_HOSTS:
        p = urlparse(url)
        candidate = f"https://{host}{p.path}"
        if p.query:
            candidate += "?" + p.query
        try:
            r = session.get(candidate, headers=_headers(f"https://{host}"), timeout=20, allow_redirects=True)
            if r.status_code < 500 and len(r.text) > 200:
                surl = _extract_surl(r.url) or _extract_surl(candidate)
                if surl:
                    return r, surl
        except requests.RequestException as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Unable to open TeraBox share page: {last_error or 'no valid share page'}")


def _share_list(session: requests.Session, surl: str, js_token: str | None,
                dp_logid: str | None, referer: str) -> tuple[dict, str]:
    errors = []
    base_params = {
        "app_id": "250528",
        "web": "1",
        "channel": "0",
        "page": "1",
        "num": "100",
        "by": "name",
        "order": "asc",
        "site_referer": "",
        "shorturl": surl,
        "root": "1",
    }
    if js_token:
        base_params["jsToken"] = js_token
    if dp_logid:
        base_params["dp-logid"] = dp_logid

    # Keep jsToken first-class, but try the same public endpoint on several
    # current TeraBox frontend hosts. This avoids binding the resolver to one
    # regional hostname.
    for host in LIST_HOSTS:
        endpoint = f"https://{host}/share/list"
        params = dict(base_params)
        try:
            r = session.get(endpoint, params=params,
                            headers=_headers(f"https://{host}", referer),
                            timeout=20)
            data = _json_response(r)
            errno = data.get("errno", 0)
            if str(errno) in {"0", "None"} and isinstance(data.get("list"), list):
                return data, endpoint
            errors.append({"host": host, "errno": errno, "message": data.get("errmsg") or data.get("show_msg")})
        except Exception as exc:
            errors.append({"host": host, "error": str(exc)})

    # Some deployments expose a simpler endpoint that omits web/channel
    # fields. It is still public-page-derived and needs no stored cookie.
    for host in LIST_HOSTS:
        endpoint = f"https://{host}/share/list"
        params = {"app_id": "250528", "shorturl": surl, "root": "1"}
        if js_token:
            params["jsToken"] = js_token
        try:
            r = session.get(endpoint, params=params,
                            headers=_headers(f"https://{host}", referer), timeout=20)
            data = _json_response(r)
            errno = data.get("errno", 0)
            if str(errno) in {"0", "None"} and isinstance(data.get("list"), list):
                return data, endpoint
            errors.append({"host": host, "simple": True, "errno": errno, "message": data.get("errmsg") or data.get("show_msg")})
        except Exception as exc:
            errors.append({"host": host, "simple": True, "error": str(exc)})

    # Preserve useful upstream diagnostics while avoiding giant HTML dumps.
    raise TeraBoxUpstreamError(errors)


class TeraBoxUpstreamError(RuntimeError):
    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        errno = next((a.get("errno") for a in attempts if a.get("errno") not in (None, 0, "0")), None)
        if errno is not None:
            super().__init__(f"TeraBox upstream rejected the public share request (errno {errno}).")
        else:
            super().__init__("TeraBox public share API could not be resolved.")


def _walk_files(items: list[dict], prefix: str = "") -> list[dict]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("server_filename") or item.get("name") or "unknown"
        path = f"{prefix}/{name}" if prefix else name
        isdir = item.get("isdir", item.get("is_dir", 0))
        if str(isdir) == "1" or isdir is True:
            children = item.get("list") or item.get("children") or []
            out.extend(_walk_files(children, path))
        else:
            copy = dict(item)
            copy["_path"] = path
            out.append(copy)
    return out


def _format_size(size: int | float | str | None) -> str | None:
    if size in (None, ""):
        return None
    try:
        n = float(size)
    except (TypeError, ValueError):
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.2f} {units[i]}"


def _category(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "ts", "m3u8"}:
        return "Video"
    if ext in {"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus"}:
        return "Audio"
    if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic"}:
        return "Image"
    if ext in {"zip", "rar", "7z", "tar", "gz", "bz2", "xz"}:
        return "Archive"
    if ext in {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt"}:
        return "Document"
    return "File"


def _resolve_direct(session: requests.Session, dlink: str, referer: str) -> str | None:
    if not dlink:
        return None
    try:
        r = session.head(dlink, headers=_headers(urlparse(dlink).scheme + "://" + (urlparse(dlink).netloc or ""), referer),
                         timeout=15, allow_redirects=True)
        if r.url and r.url != dlink:
            return r.url
    except requests.RequestException:
        pass
    return dlink


def resolve_terabox(url: str, cookie: str | None = None) -> dict:
    url = _clean_url(url)
    if not is_terabox_link(url):
        raise ValueError("Invalid TeraBox share URL")

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    if cookie:
        # Optional only. The resolver never requires it.
        session.headers["Cookie"] = cookie

    page, surl = _request_share_page(session, url)
    js_token, dp_logid, bdstoken = _extract_page_tokens(page.text)
    if not surl:
        raise ValueError("Could not extract TeraBox share ID (surl)")

    data, endpoint = _share_list(session, surl, js_token, dp_logid, page.url)
    files = _walk_files(data.get("list") or [])
    if not files:
        raise RuntimeError("TeraBox share opened successfully, but no files were returned")

    result_files = []
    for item in files:
        name = item.get("server_filename") or item.get("name") or "unknown"
        size_raw = item.get("size")
        try:
            size_bytes = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size_bytes = None
        thumb = (item.get("thumbs") or {}).get("url3") or (item.get("thumbs") or {}).get("url2") or item.get("thumbnail")
        dlink = item.get("dlink") or item.get("download_url")
        direct = _resolve_direct(session, dlink, page.url) if dlink else None
        result_files.append({
            "name": name,
            "filename": name,
            "path": item.get("_path"),
            "size_bytes": size_bytes,
            "size": _format_size(size_bytes),
            "extension": name.rsplit(".", 1)[-1].lower() if "." in name else None,
            "category": _category(name),
            "thumbnail": thumb,
            "download_url": direct or dlink,
            "dlink": dlink,
            "fs_id": item.get("fs_id") or item.get("fid") or item.get("fsid"),
            "is_dir": False,
        })

    first = result_files[0]
    return {
        "title": first["name"] if len(result_files) == 1 else f"{len(result_files)} files",
        "surl": surl,
        "share_url": page.url,
        "thumbnail": first.get("thumbnail"),
        "size": first.get("size"),
        "size_bytes": first.get("size_bytes"),
        "extension": first.get("extension"),
        "category": first.get("category"),
        "download_url": first.get("download_url"),
        "dlink": first.get("dlink"),
        "fs_id": first.get("fs_id"),
        "files": result_files,
        "resolver": {
            "cookie_required": False,
            "cookie_used": bool(cookie),
            "js_token_found": bool(js_token),
            "dp_logid_found": bool(dp_logid),
            "endpoint": endpoint,
            "bdstoken_found": bool(bdstoken),
        },
    }
