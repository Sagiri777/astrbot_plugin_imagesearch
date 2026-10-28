import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from proxy.app import BrowserSession, VerificationRequired, create_app


@pytest.fixture
def session():
    return type(
        "Session",
        (),
        {
            "warm": AsyncMock(),
            "close": AsyncMock(),
            "health": AsyncMock(return_value={"status": "degraded", "manual_verification": True}),
            "search": AsyncMock(return_value={"results": []}),
        },
    )()


async def test_health_and_upload_contract(session, png):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(session)), base_url="http://test"
    ) as client:
        assert (await client.get("/healthz")).json()["status"] == "degraded"
        result = await client.post("/api/search", files={"file": ("a.png", png)}, data={"factor": "1.4"})
        assert result.json() == {"results": []}
        assert session.search.call_args.args == (png, "1.4", "image/png")


async def test_auth_and_invalid_uploads(session, png, monkeypatch):
    monkeypatch.setenv("SOUTUBOT_TOKEN", "secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(session)), base_url="http://test"
    ) as client:
        assert (await client.get("/healthz")).status_code == 401
        client.headers["Authorization"] = "Bearer secret"
        assert (await client.get("/healthz")).status_code == 200
        invalid = await client.post("/api/search", files={"file": ("a.png", b"not an image")})
        assert invalid.status_code == 400
        invalid_factor = await client.post(
            "/api/search", files={"file": ("a.png", png)}, data={"factor": "8"}
        )
        assert invalid_factor.status_code == 400
        large = await client.post("/api/search", files={"file": ("a.png", b"x" * (8 * 1024 * 1024 + 1))})
        assert large.status_code == 413
        session.search.assert_not_called()


async def test_verification_error_and_recovery(session, png):
    session.search.side_effect = VerificationRequired()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(session)), base_url="http://test"
    ) as client:
        response = await client.post("/api/search", files={"file": ("a.png", png)})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "verification_required"
        session.search.side_effect = None
        assert (await client.post("/api/search", files={"file": ("a.png", png)})).status_code == 200


async def test_browser_search_serializes_and_reuses_page():
    session = BrowserSession()
    active = 0
    max_active = 0

    class Page:
        url = "https://soutubot.moe/"

        def is_closed(self):
            return False

        async def evaluate(self, script, args=None):
            nonlocal active, max_active
            if args is None:
                return True
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"status": 200, "body": '{"results": []}'}

    session.page = Page()
    await asyncio.gather(*(session.search(b"image", "1.2", "image/jpeg") for _ in range(3)))
    assert max_active == 1
    assert (await session.health())["status"] == "ok"


async def test_companion_lifecycle(session):
    app = create_app(session)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        session.warm.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_cancel_closes_browser_fetch_before_unlocking():
    session = BrowserSession()
    started = asyncio.Event()

    class Page:
        url = "https://soutubot.moe/"
        close = AsyncMock()

        def is_closed(self):
            return False

        async def evaluate(self, script, args=None):
            if args is None:
                return True
            started.set()
            await asyncio.Event().wait()

    page = session.page = Page()
    task = asyncio.create_task(session.search(b"image", "1.2", "image/jpeg"))
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    page.close.assert_awaited_once()
    assert session.page is None and not session.ready and not session.lock.locked()
