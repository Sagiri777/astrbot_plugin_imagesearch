import asyncio
import base64
import binascii
import io
import ipaddress
import socket
import warnings
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import SearchError


@dataclass(frozen=True)
class SearchImage:
    data: bytes
    filename: str
    mime: str


def prepare_image(data: bytes, upload_limit: int = 4 * 1024 * 1024) -> SearchImage:
    """Decode once; use a metadata-free JPEG/PNG upload accepted by all providers."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 40_000_000:
                    raise SearchError("invalid_image", "图片像素过大，请缩小后再试。")
                original.seek(0)
                image = ImageOps.exif_transpose(original).convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                background.alpha_composite(image)
                image = background.convert("RGB")
        image.thumbnail((2048, 2048))
        for quality in (90, 80, 65, 50):
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality)
            if output.tell() <= upload_limit:
                return SearchImage(output.getvalue(), "search.jpg", "image/jpeg")
        raise SearchError("image_too_large", "压缩后图片仍过大，请裁剪后再试。")
    except SearchError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise SearchError("invalid_image", "无法读取图片，请提供有效的图片文件。") from exc


async def _public_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise SearchError("invalid_url", "图片地址必须是 HTTP(S) URL。")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError) as exc:
        raise SearchError("download_failed", "无法解析图片地址。") from exc
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise SearchError("invalid_url", "AI 工具的图片 URL 必须指向公网地址。")


class ImageLoader:
    def __init__(self, client: httpx.AsyncClient, max_bytes: int):
        self.client = client
        self.max_bytes = max_bytes

    async def download(self, url: str, public_only: bool = False) -> bytes:
        # Handle redirects explicitly so the AI URL restriction also covers their targets.
        for _ in range(6):
            if public_only:
                await _public_url(url)
            if urlsplit(url).scheme not in ("http", "https"):
                raise SearchError("invalid_url", "图片地址必须是 HTTP(S) URL。")
            try:
                async with self.client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise SearchError("download_failed", "图片重定向地址无效。")
                        url = str(response.url.join(location))
                        continue
                    response.raise_for_status()
                    try:
                        length = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        length = 0
                    if length > self.max_bytes:
                        raise SearchError("image_too_large", "图片超过下载大小上限。")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.max_bytes:
                            raise SearchError("image_too_large", "图片超过下载大小上限。")
                    return bytes(data)
            except httpx.TimeoutException as exc:
                raise SearchError("timeout", "下载图片超时。") from exc
            except httpx.HTTPError as exc:
                raise SearchError("download_failed", "图片下载失败，请重发图片或更换地址。") from exc
        raise SearchError("download_failed", "图片地址重定向次数过多。")

    async def reference(self, value: str) -> bytes:
        if value.startswith(("http://", "https://")):
            return await self.download(value)
        if value.startswith(("base64://", "data:image/")):
            encoded = value[9:] if value.startswith("base64://") else value.split(",", 1)[-1]
            if len(encoded) > (self.max_bytes + 2) // 3 * 4:
                raise SearchError("image_too_large", "图片超过大小上限。")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SearchError("invalid_image", "图片 Base64 数据无效。") from exc
            if len(data) > self.max_bytes:
                raise SearchError("image_too_large", "图片超过大小上限。")
            return data
        if value.startswith("file://"):
            parsed = urlsplit(value)
            if parsed.netloc not in ("", "localhost"):
                raise SearchError("invalid_image", "不支持远程文件路径。")
            value = unquote(parsed.path)
        path = Path(value)

        def read():
            try:
                # Accept adapter paths relative to AstrBot too; never read FIFOs/devices.
                if not path.is_file():
                    raise SearchError("invalid_image", "消息适配器未提供可读取的图片文件。")
                with path.open("rb") as file:
                    data = file.read(self.max_bytes + 1)
            except OSError as exc:
                raise SearchError("download_failed", "无法读取消息中的图片文件。") from exc
            if len(data) > self.max_bytes:
                raise SearchError("image_too_large", "图片超过大小上限。")
            return data

        return await asyncio.to_thread(read)

    async def component(self, component) -> SearchImage:
        errors = []
        for value in dict.fromkeys(getattr(component, key, None) for key in ("url", "file", "path")):
            if not value:
                continue
            try:
                data = await self.reference(str(value))
                return await asyncio.to_thread(prepare_image, data)
            except SearchError as exc:
                errors.append(exc)
        if errors:
            raise errors[-1]
        raise SearchError("missing_image", "消息中没有可读取的图片。")

    async def from_url(self, url: str) -> SearchImage:
        data = await self.download(url, public_only=True)
        return await asyncio.to_thread(prepare_image, data)
