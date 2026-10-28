import asyncio
import importlib
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import docstring_parser
import pytest

from search.models import ProviderResult, SearchItem


@pytest.fixture
def framework(monkeypatch):
    """Minimal AstrBot boundary; search and command implementations remain real."""

    class Image:
        def __init__(self, file="", **kwargs):
            self.file = file
            self.__dict__.update(kwargs)

        @staticmethod
        def fromBytes(data):
            return Image(data)

    class Reply:
        def __init__(self, chain=None):
            self.chain = chain

    class Event:
        def __init__(self, text="/搜图", images=None, user="a", origin="group1"):
            self.text = text
            self.images = images or []
            self.user = user
            self.unified_msg_origin = origin
            self.sent = []
            self.stopped = False

        def get_messages(self):
            return self.images

        def get_message_str(self):
            return self.text

        def get_sender_id(self):
            return self.user

        def stop_event(self):
            self.stopped = True

        def plain_result(self, text):
            return text

        def chain_result(self, chain):
            return chain

        async def send(self, message):
            self.sent.append(message)

    class Star:
        def __init__(self, context):
            pass

    def decorator(*args, **kwargs):
        def register(func):
            func.registration = (args, kwargs)
            return func

        return register

    waiters = {}

    def session_waiter(timeout):
        def decorate(callback):
            async def wait(event, session_filter):
                future = asyncio.get_running_loop().create_future()
                key = session_filter.filter(event)
                waiters[key] = (session_filter, callback, future)
                try:
                    await asyncio.wait_for(future, timeout)
                finally:
                    waiters.pop(key, None)

            return wait

        return decorate

    async def deliver(event):
        for key, (session_filter, callback, future) in list(waiters.items()):
            if session_filter.filter(event) == key:
                await callback(SimpleNamespace(stop=lambda: future.set_result(None)), event)

    modules = {
        "astrbot": {},
        "astrbot.api": {"AstrBotConfig": dict, "logger": logging.getLogger("test")},
        "astrbot.api.event": {
            "AstrMessageEvent": Event,
            "filter": SimpleNamespace(command=decorator, llm_tool=decorator),
        },
        "astrbot.api.message_components": {"Image": Image, "Reply": Reply},
        "astrbot.api.star": {"Star": Star, "Context": object, "register": decorator},
        "astrbot.core": {},
        "astrbot.core.utils": {},
        "astrbot.core.utils.session_waiter": {"SessionFilter": object, "session_waiter": session_waiter},
    }
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    package = types.ModuleType("plugin_under_test")
    package.__path__ = [str(Path(__file__).resolve().parents[1])]
    monkeypatch.setitem(sys.modules, "plugin_under_test", package)
    # Every test gets new module globals, just like a plugin reload.
    for name in list(sys.modules):
        if name.startswith("plugin_under_test."):
            monkeypatch.delitem(sys.modules, name)
    module = importlib.import_module("plugin_under_test.main")
    return SimpleNamespace(
        module=module, Event=Event, Image=Image, Reply=Reply, deliver=deliver, waiters=waiters
    )


async def test_commands_share_search_and_text_survives_thumbnail_failure(framework):
    plugin = framework.module.ImageSearchPlugin(object(), {})
    plugin.service.loader.component = AsyncMock(return_value=object())
    plugin.service.loader.from_url = AsyncMock(side_effect=RuntimeError("thumbnail failed"))
    plugin.service.search = AsyncMock(
        return_value=[
            ProviderResult(
                "soutubot", items=[SearchItem("nhentai", "Found", thumbnail="https://example.org/a")]
            )
        ]
    )
    event = framework.Event("/搜本子 严格", [framework.Image("a"), framework.Image("b")])
    try:
        await plugin.doujin_command(event)
        assert "第一张" in event.sent[0]
        assert any("Found" in message for message in event.sent)
        assert plugin.service.search.call_args.kwargs["strict"] is True
        assert plugin.service.search.call_args.args[1] == "soutubot"
    finally:
        await plugin.terminate()


async def test_tool_registration_json_and_no_direct_messages(framework):
    plugin = framework.module.ImageSearchPlugin(object(), {})
    plugin.service.loader.component = AsyncMock(return_value=object())
    plugin.service.search = AsyncMock(
        return_value=[ProviderResult("avscan", items=[SearchItem("avscan", "TEST-001")])]
    )
    event = framework.Event(images=[framework.Reply([framework.Image("a")])])
    try:
        assert plugin.image_search.registration[1]["name"] == "image_search"
        params = docstring_parser.parse(plugin.image_search.__doc__).params
        assert [(p.arg_name, p.type_name) for p in params] == [
            ("image_url", "string"),
            ("platform", "string"),
            ("limit", "number"),
            ("strict", "boolean"),
        ]
        result = json.loads(await plugin.image_search(event, platform="avscan"))
        assert result["providers"][0]["items"][0]["title"] == "TEST-001"
        assert not event.sent
        assert not framework.waiters
        missing = json.loads(await plugin.image_search(framework.Event()))
        assert missing["error"]["code"] == "missing_image"
        invalid = json.loads(await plugin.image_search(event, limit=0))
        assert invalid["error"]["code"] == "invalid_argument"
    finally:
        await plugin.terminate()


async def test_waiting_isolates_user_and_session_and_cleans_up(framework):
    plugin = framework.module.ImageSearchPlugin(object(), {})
    event = framework.Event()
    task = asyncio.create_task(plugin._wait_image(event))
    try:
        for _ in range(3):
            await asyncio.sleep(0)
        await framework.deliver(framework.Event(images=[framework.Image("b")], user="b"))
        await framework.deliver(framework.Event(images=[framework.Image("a")], origin="group2"))
        assert not task.done()
        await framework.deliver(framework.Event(text="hello"))
        assert not task.done()
        correct = framework.Event(images=[framework.Image("a")])
        await framework.deliver(correct)
        result = await task
        assert result[0] is correct
        assert correct.stopped
        assert not framework.waiters and not plugin._waiting
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await plugin.terminate()


async def test_wait_timeout_and_unload_cancel(framework):
    plugin = framework.module.ImageSearchPlugin(object(), {})
    plugin.settings.wait_timeout = 0.01
    event = framework.Event()
    assert await plugin._wait_image(event) is None
    assert any("超时" in message for message in event.sent)
    plugin.settings.wait_timeout = 60
    waiting = asyncio.create_task(plugin.image_command(framework.Event()))
    for _ in range(4):
        await asyncio.sleep(0)
    await plugin.terminate()
    assert waiting.cancelled()
    assert not framework.waiters and not plugin._waiting


async def test_help_status_and_bad_arguments_do_not_search(framework):
    plugin = framework.module.ImageSearchPlugin(object(), {})
    plugin.service.search = AsyncMock()
    plugin.service.status = AsyncMock(return_value="status")
    try:
        for text in ("/搜图 帮助", "/搜图 状态", "/搜图 unknown"):
            event = framework.Event(text)
            await plugin.image_command(event)
            assert event.sent
        plugin.service.search.assert_not_called()
        assert not framework.waiters
    finally:
        await plugin.terminate()
