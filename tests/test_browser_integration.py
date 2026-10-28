"""Opt-in real Chromium check against a local fixture server; no upstream uploads."""

import asyncio
import importlib
import json
import os
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

proxy_app = importlib.import_module("proxy.app")
pytestmark = pytest.mark.skipif(
    not os.getenv("IMAGESEARCH_BROWSER_EXECUTABLE"),
    reason="Set IMAGESEARCH_BROWSER_EXECUTABLE for real-browser QA",
)


async def test_browser_upload_verification_and_persistent_cookies(monkeypatch, tmp_path, png):
    requests = []

    async def handle(reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
            length = next(
                (
                    int(line.split(":", 1)[1])
                    for line in head.splitlines()
                    if line.lower().startswith("content-length:")
                ),
                0,
            )
            body = await reader.readexactly(length)
            requests.append((head, body))
            is_search = head.startswith("POST /api/search ")
            content = (
                json.dumps({"results": []}).encode()
                if is_search
                else b"<html><title>Ready</title><body>Search</body></html>"
            )
            mime = "application/json" if is_search else "text/html"
            response = (
                f"HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Length: {len(content)}\r\nSet-Cookie: session=verified; Max-Age=3600; Path=/\r\nConnection: close\r\n\r\n".encode()
                + content
            )
            writer.write(response)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(proxy_app, "ORIGIN", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("SOUTUBOT_BROWSER_DATA", str(tmp_path / "profile"))
    monkeypatch.setenv("SOUTUBOT_HEADLESS", "true")
    monkeypatch.delenv("SOUTUBOT_BROWSER_PROXY", raising=False)

    class Launcher:
        async def start(self):
            playwright = await async_playwright().start()

            async def launch(*args, **kwargs):
                kwargs["executable_path"] = os.environ["IMAGESEARCH_BROWSER_EXECUTABLE"]
                return await playwright.chromium.launch_persistent_context(*args, **kwargs)

            return SimpleNamespace(
                chromium=SimpleNamespace(launch_persistent_context=launch), stop=playwright.stop
            )

    monkeypatch.setattr(proxy_app, "async_playwright", Launcher)
    session = proxy_app.BrowserSession()
    try:
        await session.warm()
        assert (await session.health())["status"] == "ok"
        assert await session.search(png, "1.4", "image/png") == {"results": []}
        head, body = next(request for request in requests if request[0].startswith("POST"))
        assert "session=verified" in head
        assert png in body and b'name="factor"\r\n\r\n1.4' in body
        await session.page.set_content("<title>Just a moment...</title><body id='challenge-form'></body>")
        with pytest.raises(proxy_app.VerificationRequired):
            await session.search(png, "1.2", "image/png")
        await session.page.set_content("<title>Ready</title><body>Verified by operator</body>")
        assert await session.search(png, "1.2", "image/png") == {"results": []}
        await session.close()
        # A new persistent browser context must load the cookie saved on shutdown.
        previous_requests = len(requests)
        await session.warm()
        resumed_get = next(head for head, _ in requests[previous_requests:] if head.startswith("GET / "))
        assert "session=verified" in resumed_get
        assert (await session.context.cookies())[0]["value"] == "verified"
    finally:
        await session.close()
        server.close()
        await server.wait_closed()
