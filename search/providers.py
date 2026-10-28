import math
import re
from urllib.parse import quote, urljoin, urlsplit

import httpx
from PicImageSearch import Ascii2D, Yandex
from PicImageSearch.exceptions import ParsingError
from PicImageSearch.model import SauceNAOResponse

from .config import Settings
from .images import SearchImage
from .models import ProviderResult, SearchError, SearchItem


def web_url(value, base: str = "") -> str:
    value = urljoin(base, str(value or "")) if value else ""
    return value if urlsplit(value).scheme in ("http", "https") else ""


def score(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 100 else None
    except (ValueError, TypeError):
        return None


def json_payload(response: httpx.Response) -> dict:
    status = response.status_code
    if status in (401, 403):
        raise SearchError("verification_required", "站点拒绝访问或需要验证，请检查凭据、代理或浏览器服务。")
    if status == 429:
        raise SearchError("rate_limited", "平台请求频率受限，请稍后再试。")
    if status >= 400:
        # The browser companion returns stable codes. Never expose arbitrary upstream HTML.
        try:
            detail = response.json().get("detail", {})
        except (ValueError, AttributeError):
            detail = {}
        if isinstance(detail, dict) and detail.get("code") == "verification_required":
            raise SearchError(
                "verification_required", "SoutuBot 需要人工验证，请打开伴随服务的浏览器完成验证。"
            )
        if isinstance(detail, dict) and detail.get("code") == "timeout":
            raise SearchError("timeout", "浏览器搜索超时，请稍后再试。")
        if isinstance(detail, dict) and detail.get("code") == "browser_unavailable":
            raise SearchError("network_error", "浏览器服务不可用，请检查 Chromium 与会话目录。")
        raise SearchError("upstream_error", f"平台返回 HTTP {status}，请稍后再试。")
    try:
        payload = response.json()
    except ValueError as exc:
        if any(word in response.text.lower() for word in ("captcha", "cloudflare", "turnstile")):
            raise SearchError("verification_required", "站点需要验证，请检查浏览器服务或代理配置。") from exc
        raise SearchError("invalid_response", "平台返回了无法识别的响应。") from exc
    if not isinstance(payload, dict):
        raise SearchError("invalid_response", "平台响应结构发生变化。")
    return payload


async def search_general(platform: str, image: SearchImage, settings: Settings, limit: int) -> ProviderResult:
    last_url = ""

    async def check_response(response: httpx.Response):
        nonlocal last_url
        last_url = str(response.url)
        if response.is_redirect:
            return
        response.raise_for_status()
        await response.aread()
        text = response.text.lower()
        if len(text) < 20_000 and "service" in text and "under construction" in text:
            raise SearchError("unavailable", "平台当前处于维护状态，请稍后再试。")
        if (
            "showcaptcha" in response.url.path
            or "cf-chl-" in text
            or "<title>just a moment" in text
            or 'id="challenge-form"' in text
        ):
            raise SearchError("verification_required", "平台要求验证码，请更换代理或稍后再试。")

    # A fresh client isolates cookies and checks challenges before the HTML parser runs.
    async with httpx.AsyncClient(
        proxy=settings.proxy or None,
        timeout=settings.timeout,
        trust_env=False,
        follow_redirects=True,
        event_hooks={"response": [check_response]},
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        },
    ) as client:
        if platform == "yandex":
            engine = Yandex(client=client)
        elif platform == "ascii2d":
            engine = Ascii2D(bovw=settings.ascii2d_feature_search, client=client)
        try:
            if platform == "saucenao":
                # PHP's POST form keeps the key out of HTTPX request URL logs.
                raw = await client.post(
                    "https://saucenao.com/search.php",
                    data={
                        "api_key": settings.saucenao_api_key,
                        "output_type": "2",
                        "db": "999",
                        "numres": str(limit),
                        "minsim": "0",
                    },
                    files={"file": (image.filename, image.data, image.mime)},
                )
                payload = json_payload(raw)
                payload["status_code"] = raw.status_code
                response = SauceNAOResponse(payload, str(raw.url))
            else:
                response = await engine.search(file=image.data)
        except ParsingError as exc:
            # The pinned parser raises for a valid Yandex response with no matched sites.
            if platform == "yandex" and "Failed to extract search results from 'data-state'" in str(exc):
                return ProviderResult(platform, status="empty", result_url=web_url(last_url))
            raise
    if platform == "saucenao":
        if getattr(response, "short_remaining", None) == 0 or getattr(response, "long_remaining", None) == 0:
            if not response.raw:
                raise SearchError("rate_limited", "SauceNAO 配额已用尽，请稍后再试。")
        status = getattr(response, "status", 0)
        if int(status or 0) < 0:
            raise SearchError("upstream_error", "SauceNAO 拒绝了请求，请检查 API Key 和配额。")
    result = ProviderResult(platform, result_url=web_url(getattr(response, "url", "")))
    for row in response.raw:
        if platform == "saucenao":
            links = [web_url(link) for link in getattr(row, "ext_urls", [])]
            url = web_url(getattr(row, "url", "")) or next((link for link in links if link), "")
            similarity = score(getattr(row, "similarity", None))
        else:
            links = []
            url = web_url(getattr(row, "url", ""))
            similarity = None
        title = str(getattr(row, "title", "") or "")
        if not url and not title:
            continue
        result.items.append(
            SearchItem(
                source=platform,
                title=title or "未提供标题",
                url=url,
                thumbnail=web_url(getattr(row, "thumbnail", "")),
                similarity=similarity,
                extra={"author": str(getattr(row, "author", "") or ""), "links": [u for u in links if u]},
            )
        )
        if len(result.items) >= limit:
            break
    # A challenge page can otherwise look identical to a legitimate empty search.
    origin = str(getattr(response, "origin", "") or "").lower()
    if not result.items and any(word in origin for word in ("showcaptcha", "turnstile", "cf-chl-")):
        raise SearchError("verification_required", "平台要求验证码，请更换代理或稍后再试。")
    result.status = "ok" if result.items else "empty"
    return result


SOUTU_ORIGIN = "https://soutubot.moe"
SOUTU_SOURCES = {
    "nhentai": "https://nhentai.net",
    "ehentai": "https://e-hentai.org",
    "e-hentai": "https://e-hentai.org",
    "panda": "https://panda.chaika.moe",
}


def parse_soutubot(payload: dict, limit: int) -> ProviderResult:
    result_id = str(payload.get("result_id") or payload.get("id") or "")
    result = ProviderResult(
        "soutubot", result_url=(f"{SOUTU_ORIGIN}/results/{quote(result_id, safe='')}" if result_id else "")
    )
    items = []
    if isinstance(payload.get("results"), list):
        for row in payload["results"]:
            for segment in row.get("path_segments") or []:
                meta = segment.get("metadata") or {}
                source_meta = meta.get("source") or {}
                family = str(segment.get("source_family") or source_meta.get("key") or "unknown")
                title_meta = meta.get("title") or {}
                title = title_meta.get("primary") if isinstance(title_meta, dict) else str(title_meta)
                book_url = web_url(
                    segment.get("source_url") or source_meta.get("url"), SOUTU_SOURCES.get(family, "")
                )
                page_url = web_url(segment.get("page_url"), SOUTU_SOURCES.get(family, ""))
                item = SearchItem(
                    source=family,
                    title=title or f"{family} #{segment.get('external_id', '')}",
                    url=page_url or book_url,
                    thumbnail=web_url(segment.get("thumbnail_url"), SOUTU_ORIGIN),
                    similarity=score(row.get("score")),
                    extra={
                        "page": segment.get("page_no"),
                        "language": segment.get("language") or "",
                        "book_url": book_url,
                    },
                )
                key = book_url or str(
                    segment.get("external_id") or segment.get("normalized_path") or title or page_url
                )
                items.append((key, item))
    elif isinstance(payload.get("data"), list):
        for row in payload["data"]:
            family = str(row.get("source") or "unknown").lower()
            book_url = web_url(row.get("subjectPath"), SOUTU_SOURCES.get(family, ""))
            page_url = web_url(row.get("pagePath"), SOUTU_SOURCES.get(family, ""))
            item = SearchItem(
                source=family,
                title=str(row.get("title") or "未提供标题"),
                url=page_url or book_url,
                thumbnail=web_url(row.get("previewImageUrl"), SOUTU_ORIGIN),
                similarity=score(row.get("similarity")),
                extra={"page": row.get("page"), "language": row.get("language") or "", "book_url": book_url},
            )
            items.append((book_url or item.title or page_url, item))
    else:
        raise SearchError("invalid_response", "SoutuBot 响应结构发生变化。")
    items.sort(key=lambda pair: pair[1].similarity if pair[1].similarity is not None else -1, reverse=True)
    seen = set()
    for key, item in items:
        identity = (item.source, key)
        if identity not in seen:
            seen.add(identity)
            result.items.append(item)
        if len(result.items) >= limit:
            break
    result.status = "ok" if result.items else "empty"
    return result


def parse_avscan(payload: dict, base_url: str, limit: int) -> ProviderResult:
    if not isinstance(payload.get("results"), list):
        raise SearchError("invalid_response", "AVScan 响应结构发生变化。")
    result = ProviderResult("avscan", result_url=base_url)
    grouped: dict[str, SearchItem] = {}
    for row in payload["results"]:
        code = str(row.get("video_code") or "")
        if not code:
            continue
        item = grouped.setdefault(
            code,
            SearchItem(
                "avscan",
                code,
                similarity=score(row.get("best_similarity")),
                extra={"video_code": code, "frames": []},
            ),
        )
        value = score(row.get("best_similarity"))
        if value is not None:
            item.similarity = max(item.similarity or 0, value)
        for frame in row.get("frames") or []:
            name = str(frame.get("image_name") or "").rsplit("/", 1)[-1]
            match = re.search(r"_(\d{2,}-\d{2}-\d{2})\.[^.]+$", name)
            thumb = (
                f"{base_url.rstrip('/')}/thumb/{quote(code, safe='')}/{quote(name.rsplit('.', 1)[0], safe='')}.webp"
                if name
                else ""
            )
            if name and all(f["image_name"] != name for f in item.extra["frames"]):
                item.extra["frames"].append(
                    {
                        "image_name": name,
                        "timestamp": match[1].replace("-", ":") if match else "",
                        "similarity": score(frame.get("similarity")),
                        "thumbnail": thumb,
                    }
                )
            if not item.thumbnail:
                item.thumbnail = thumb
    result.items = sorted(
        grouped.values(),
        key=lambda item: item.similarity if item.similarity is not None else -1,
        reverse=True,
    )[:limit]
    result.status = "ok" if result.items else "empty"
    return result


async def search_api(
    platform: str, image: SearchImage, settings: Settings, client: httpx.AsyncClient, limit: int, strict: bool
) -> ProviderResult:
    if platform == "soutubot":
        headers = {"Authorization": f"Bearer {settings.soutubot_token}"} if settings.soutubot_token else {}
        response = await client.post(
            settings.soutubot_base_url.rstrip("/") + "/api/search",
            files={"file": (image.filename, image.data, image.mime)},
            data={"factor": "1.4" if strict else "1.2"},
            headers=headers,
            timeout=settings.soutubot_timeout,
        )
        return parse_soutubot(json_payload(response), limit)
    response = await client.post(
        settings.avscan_base_url.rstrip("/") + "/search",
        files={"file": (image.filename, image.data, image.mime)},
        timeout=settings.timeout,
    )
    return parse_avscan(json_payload(response), settings.avscan_base_url, limit)
