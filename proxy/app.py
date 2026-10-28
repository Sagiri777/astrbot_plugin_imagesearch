import asyncio
import base64
import hmac
import io
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

ORIGIN = "https://soutubot.moe"
MAX_UPLOAD = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


class VerificationRequired(Exception):
    pass


class BrowserSession:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.playwright = None
        self.context = None
        self.page = None
        self.ready = False

    async def _start(self):
        if self.page is not None and not self.page.is_closed():
            return
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        options = {
            "headless": os.getenv("SOUTUBOT_HEADLESS", "true").lower() == "true",
            "viewport": {"width": 1280, "height": 900},
        }
        browser_proxy = os.getenv("SOUTUBOT_BROWSER_PROXY", "")
        if browser_proxy:
            options["proxy"] = {"server": browser_proxy}
        profile = Path(os.getenv("SOUTUBOT_BROWSER_DATA", "./browser-data")).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        self.context = await self.playwright.chromium.launch_persistent_context(str(profile), **options)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.set_default_timeout(15_000)
        try:
            await self.page.goto(ORIGIN, wait_until="domcontentloaded", timeout=60_000)
        except (BrowserError, asyncio.CancelledError):
            await self.page.close()
            self.page = None
            raise
        self.ready = await self._is_ready()

    async def _is_ready(self):
        if self.page is None or self.page.is_closed() or not self.page.url.startswith(ORIGIN + "/"):
            return False
        # Do not click or attempt to solve challenges. The operator uses the same page.
        return await self.page.evaluate("""() => {
            const title = document.title.toLowerCase();
            return !!document.body &&
                !title.includes('just a moment') && !title.includes('attention required') &&
                !document.querySelector('#challenge-running, #challenge-form, .cf-turnstile');
        }""")

    async def warm(self):
        async with self.lock:
            try:
                await self._start()
            except Exception as exc:
                self.ready = False
                logger.warning("Browser startup unavailable (%s)", type(exc).__name__)

    async def health(self):
        if not self.lock.locked() and self.page is not None:
            try:
                self.ready = await self._is_ready()
            except BrowserError:
                self.ready = False
        return {
            "status": "ok" if self.ready else "degraded",
            "busy": self.lock.locked(),
            "manual_verification": not self.ready,
        }

    async def search(self, data: bytes, factor: str, mime: str):
        async with self.lock:
            await self._start()
            self.ready = await self._is_ready()
            if not self.ready:
                raise VerificationRequired()
            try:
                response = await self.page.evaluate(
                    """async ({image, factor, mime}) => {
                const bytes = Uint8Array.from(atob(image), c => c.charCodeAt(0));
                const form = new FormData();
                form.append('file', new Blob([bytes], {type: mime}), 'search-image');
                form.append('factor', factor);
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 110000);
                try {
                    const response = await fetch('/api/search', {
                        method: 'POST', body: form, credentials: 'same-origin',
                        signal: controller.signal
                    });
                    return {status: response.status, body: await response.text()};
                } finally { clearTimeout(timer); }
            }""",
                    {"image": base64.b64encode(data).decode(), "factor": factor, "mime": mime},
                )
            except asyncio.CancelledError:
                # Cancel the browser-side fetch before the lock admits the next upload.
                await self.page.close()
                self.page = None
                self.ready = False
                raise
            status = response["status"]
            if status in (401, 403) or any(
                marker in response["body"].lower() for marker in ("cf-chl-", "<title>just a moment")
            ):
                self.ready = False
                # Show the actual verification page in the operator's browser.
                await self.page.goto(ORIGIN, wait_until="domcontentloaded", timeout=30_000)
                raise VerificationRequired()
            if status == 429:
                raise HTTPException(429, detail={"code": "rate_limited"})
            if status != 200:
                raise HTTPException(502, detail={"code": "upstream_error", "upstream_status": status})
            import json

            try:
                payload = json.loads(response["body"])
            except ValueError as exc:
                raise HTTPException(502, detail={"code": "invalid_response"}) from exc
            if not isinstance(payload, dict) or not any(
                isinstance(payload.get(key), list) for key in ("results", "data")
            ):
                raise HTTPException(502, detail={"code": "invalid_response"})
            return payload

    async def close(self):
        if self.context is not None:
            await self.context.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.page = self.context = self.playwright = None
        self.ready = False


async def authorize(authorization: str | None = Header(default=None)):
    token = os.getenv("SOUTUBOT_TOKEN", "")
    if token and not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, detail={"code": "unauthorized"})


def validate_image(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("too many pixels")
            mime = Image.MIME.get(image.format)
            image.verify()
        if not mime:
            raise ValueError("unknown format")
        return mime
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, detail={"code": "invalid_image"}) from exc


def create_app(session: BrowserSession | None = None):
    session = session or BrowserSession()

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(session.warm())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await session.close()

    app = FastAPI(title="SoutuBot Browser Companion", lifespan=lifespan, dependencies=[Depends(authorize)])

    @app.get("/healthz")
    async def health():
        return await session.health()

    @app.post("/api/search")
    async def search(file: UploadFile = File(...), factor: str = Form("1.2")):
        if factor not in ("1.2", "1.4"):
            raise HTTPException(400, detail={"code": "invalid_factor"})
        try:
            data = await file.read(MAX_UPLOAD + 1)
        finally:
            await file.close()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, detail={"code": "image_too_large"})
        mime = await asyncio.to_thread(validate_image, data)
        try:
            return await asyncio.wait_for(session.search(data, factor, mime), timeout=115)
        except VerificationRequired as exc:
            raise HTTPException(503, detail={"code": "verification_required"}) from exc
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, detail={"code": "timeout"}) from exc
        except (BrowserError, OSError) as exc:
            logger.warning("Browser request failed (%s)", type(exc).__name__)
            raise HTTPException(503, detail={"code": "browser_unavailable"}) from exc

    return app


app = create_app()
