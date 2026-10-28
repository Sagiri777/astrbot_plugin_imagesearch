import asyncio
import logging

import httpx

from .config import Settings
from .images import ImageLoader, SearchImage
from .models import GENERAL_PLATFORMS, PLATFORMS, ProviderResult, SearchError
from .providers import search_api, search_general

logger = logging.getLogger(__name__)


class SearchService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            proxy=settings.proxy or None,
            timeout=settings.timeout,
            trust_env=False,
            headers={"User-Agent": "AstrBot-ImageSearch/1.0"},
        )
        # Local companion requests must not go through an outbound proxy.
        self.local_client = httpx.AsyncClient(timeout=settings.soutubot_timeout, trust_env=False)
        self.loader = ImageLoader(self.client, settings.max_download_mb * 1024 * 1024)

    async def close(self):
        await self.client.aclose()
        await self.local_client.aclose()

    def platforms(self, platform: str) -> tuple[str, ...]:
        if platform == "general":
            return self.settings.default_platforms
        if platform not in PLATFORMS:
            raise SearchError(
                "invalid_platform", "未知平台，可选 general、yandex、saucenao、ascii2d、soutubot、avscan。"
            )
        return (platform,)

    def configuration_error(self, platform: str) -> SearchError | None:
        if not self.settings.enabled[platform]:
            return SearchError("disabled", "该平台已在插件配置中关闭。")
        if platform == "saucenao" and not self.settings.saucenao_api_key:
            return SearchError("missing_config", "请先在插件配置中填写 SauceNAO API Key。")
        if platform == "soutubot" and not self.settings.soutubot_base_url:
            return SearchError("missing_config", "请先配置 SoutuBot 服务地址。")
        return None

    async def _one(self, platform: str, image: SearchImage, limit: int, strict: bool) -> ProviderResult:
        error = self.configuration_error(platform)
        if error:
            return ProviderResult.failed(platform, error)
        timeout = self.settings.soutubot_timeout if platform == "soutubot" else self.settings.timeout
        try:
            if platform in GENERAL_PLATFORMS:
                task = search_general(platform, image, self.settings, limit)
            else:
                client = self.local_client if platform == "soutubot" else self.client
                task = search_api(platform, image, self.settings, client, limit, strict)
            return await asyncio.wait_for(task, timeout=timeout)
        except SearchError as exc:
            return ProviderResult.failed(platform, exc)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            error = SearchError("timeout", "搜索超时，请稍后再试。")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 429:
                error = SearchError("rate_limited", "平台请求频率受限，请稍后再试。")
            elif code in (401, 403):
                error = SearchError("verification_required", "平台拒绝访问或需要验证码，请检查代理和凭据。")
            else:
                error = SearchError("upstream_error", f"平台返回 HTTP {code}。")
        except httpx.HTTPError:
            error = SearchError("network_error", "无法连接平台，请检查网络、代理或伴随服务。")
        except Exception as exc:
            # Exception text may contain request URLs, tokens or uploaded content.
            logger.warning("Image search provider %s failed (%s)", platform, type(exc).__name__)
            error = SearchError("invalid_response", "平台响应无法解析，接口可能发生变化。")
        return ProviderResult.failed(platform, error)

    async def search(
        self, image: SearchImage, platform: str = "general", limit: int | None = None, strict: bool = False
    ) -> list[ProviderResult]:
        platforms = self.platforms(platform)
        if not platforms:
            raise SearchError("missing_config", "默认来源为空，请在配置中选择通用搜索平台。")
        limit = max(1, min(10, limit if limit is not None else self.settings.max_results))
        return list(await asyncio.gather(*(self._one(p, image, limit, strict) for p in platforms)))

    async def status(self) -> str:
        lines = ["以图搜图 · 配置状态"]
        for platform in PLATFORMS:
            error = self.configuration_error(platform)
            lines.append(f"{platform}：{str(error) if error else '已配置'}")
        lines.append("默认来源：" + ("、".join(self.settings.default_platforms) or "无"))
        if not self.configuration_error("soutubot"):
            headers = (
                {"Authorization": f"Bearer {self.settings.soutubot_token}"}
                if self.settings.soutubot_token
                else {}
            )
            if self.settings.soutubot_base_url.rstrip("/") == "https://soutubot.moe":
                lines.append("SoutuBot：直连模式，跳过伴随服务检查")
            else:
                try:
                    response = await self.local_client.get(
                        self.settings.soutubot_base_url.rstrip("/") + "/healthz", headers=headers, timeout=5
                    )
                    response.raise_for_status()
                    health = response.json()
                    lines.append(
                        "浏览器服务："
                        + ("就绪" if health.get("status") == "ok" else "未就绪，请检查服务或完成人工验证")
                    )
                except (httpx.HTTPError, ValueError, AttributeError):
                    lines.append("浏览器服务：不可达或鉴权失败，请检查服务地址和 Token")
        lines.append("此检查未上传图片；已配置不代表上游网络可达。")
        return "\n".join(lines)
