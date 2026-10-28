import base64
import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from search.images import ImageLoader, prepare_image
from search.models import SearchError


def test_prepare_image_transparency_and_dimensions():
    source = io.BytesIO()
    Image.new("RGBA", (3000, 1000), (0, 0, 0, 0)).save(source, "PNG")
    result = prepare_image(source.getvalue())
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.width == 2048
        assert min(image.getpixel((0, 0))) >= 250
        assert not image.getexif()
    assert result.mime == "image/jpeg"


def test_invalid_image():
    with pytest.raises(SearchError, match="有效"):
        prepare_image(b"<html>not an image</html>")


async def test_component_local_uri_base64_and_fallback(png, tmp_path):
    path = tmp_path / "a b.png"
    path.write_bytes(png)
    async with httpx.AsyncClient() as client:
        loader = ImageLoader(client, 100_000)
        for ref in (str(path), path.as_uri(), "base64://" + base64.b64encode(png).decode()):
            image = await loader.component(SimpleNamespace(file=ref))
            assert image.data.startswith(b"\xff\xd8")
        image = await loader.component(SimpleNamespace(file="onebot-hash.image", path=str(path)))
        assert image.data


async def test_stream_size_limit(png):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=png))
    ) as client:
        with pytest.raises(SearchError) as error:
            await ImageLoader(client, 10).download("https://images.example/a")
        assert error.value.code == "image_too_large"


async def test_ai_rejects_local_and_private_urls():
    async with httpx.AsyncClient() as client:
        loader = ImageLoader(client, 10_000)
        for url in ("/etc/passwd", "file:///tmp/a.png", "http://127.0.0.1/a", "http://[::1]/a"):
            with pytest.raises(SearchError) as error:
                await loader.from_url(url)
            assert error.value.code == "invalid_url"


async def test_ai_checks_redirect_targets(monkeypatch):
    from search import images

    async def resolve(host, port, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1" if host == "private.example" else "8.8.8.8", port))]

    monkeypatch.setattr(images.asyncio.get_running_loop(), "getaddrinfo", resolve)
    calls = []

    def response(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://private.example/a"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(SearchError) as error:
            await ImageLoader(client, 100_000).from_url("https://public.example/a")
        assert error.value.code == "invalid_url"
    assert calls == ["https://public.example/a"]


async def test_download_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("sensitive-url", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(SearchError) as error:
            await ImageLoader(client, 100).download("https://example.org/a")
        assert error.value.code == "timeout"
        assert "sensitive" not in str(error.value)
