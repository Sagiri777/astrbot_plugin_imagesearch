import asyncio
import json

import httpx

from search import service as service_module
from search.config import Settings
from search.images import prepare_image
from search.models import ProviderResult, SearchItem
from search.service import SearchService


async def test_aggregate_concurrent_and_partial_failure(monkeypatch, png):
    started = set()
    both_started = asyncio.Event()

    async def search(platform, image, settings, limit):
        started.add(platform)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 1)
        if platform == "ascii2d":
            raise RuntimeError("SECRET-API-KEY")
        return ProviderResult(platform, items=[SearchItem(platform, "Found")])

    monkeypatch.setattr(service_module, "search_general", search)
    service = SearchService(Settings())
    try:
        results = await service.search(prepare_image(png))
    finally:
        await service.close()
    assert [r.platform for r in results] == ["yandex", "saucenao", "ascii2d"]
    assert [r.status for r in results] == ["ok", "error", "error"]
    assert results[1].error["code"] == "missing_config"
    assert "SECRET" not in json.dumps([r.to_dict() for r in results])


async def test_provider_deadline(monkeypatch, png):
    async def stall(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(service_module, "search_general", stall)
    settings = Settings()
    settings.timeout = 0.01
    service = SearchService(settings)
    try:
        result = (await service.search(prepare_image(png), "yandex"))[0]
        assert result.error["code"] == "timeout"
    finally:
        await service.close()


async def test_status_is_get_only_and_disabled_provider(png):
    service = SearchService(Settings())
    service.settings.enabled["avscan"] = False
    calls = []

    def response(request):
        calls.append(request.method)
        return httpx.Response(200, json={"status": "degraded"})

    await service.local_client.aclose()
    service.local_client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        status = await service.status()
        result = (await service.search(prepare_image(png), "avscan"))[0]
        assert "未就绪" in status
        assert result.error["code"] == "disabled"
        assert calls == ["GET"]
    finally:
        await service.close()


def test_settings_match_schema_defaults():
    from pathlib import Path

    schema = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text())
    configured = Settings.from_mapping({key: value["default"] for key, value in schema.items()})
    assert configured == Settings()
    assert Settings.from_mapping(
        {"default_platforms": ["soutubot", "yandex", "yandex"], "max_results": 999}
    ).default_platforms == ("yandex",)
    assert Settings.from_mapping({"timeout": "invalid"}).timeout == 30
