import html
import json

import httpx
import pytest

from search import providers
from search.config import Settings
from search.images import prepare_image
from search.models import SearchError
from search.providers import json_payload, parse_avscan, parse_soutubot, search_api, search_general


def test_soutubot_v2_sort_and_deduplicate_books():
    def row(book, page, similarity):
        return {
            "score": similarity,
            "path_segments": [
                {
                    "source_family": "nhentai",
                    "external_id": book,
                    "source_url": f"/g/{book}/",
                    "page_url": f"/g/{book}/{page}/",
                    "page_no": page,
                    "thumbnail_url": "/thumb/a.jpg",
                    "metadata": {"title": {"primary": f"Book {book}"}},
                }
            ],
        }

    result = parse_soutubot({"result_id": "abc", "results": [row(1, 2, 20), row(2, 1, 80), row(1, 5, 90)]}, 3)
    assert [item.title for item in result.items] == ["Book 1", "Book 2"]
    assert result.items[0].extra["page"] == 5
    assert result.items[0].url == "https://nhentai.net/g/1/5/"
    assert result.items[0].thumbnail == "https://soutubot.moe/thumb/a.jpg"
    assert result.result_url.endswith("/results/abc")


def test_soutubot_legacy_and_empty():
    result = parse_soutubot(
        {
            "data": [
                {
                    "source": "ehentai",
                    "title": "Example",
                    "similarity": "85.2",
                    "page": 3,
                    "subjectPath": "/g/1/hash",
                    "pagePath": "/s/hash/1-3",
                }
            ]
        },
        3,
    )
    assert result.items[0].url == "https://e-hentai.org/s/hash/1-3"
    assert result.items[0].similarity == 85.2
    assert parse_soutubot({"data": []}, 3).status == "empty"
    with pytest.raises(SearchError):
        parse_soutubot({"changed": []}, 3)


def test_soutubot_missing_optional_fields_do_not_collapse_distinct_titles():
    result = parse_soutubot({"data": [{"title": "A"}, {"title": "B"}]}, 3)
    assert len(result.items) == 2


def test_avscan_groups_frames_and_timestamps():
    result = parse_avscan(
        {
            "results": [
                {
                    "video_code": "TEST-001",
                    "best_similarity": 91,
                    "frames": [{"image_name": "TEST-001_02-01-28.jpg", "similarity": 91}],
                },
                {
                    "video_code": "TEST-001",
                    "best_similarity": 93,
                    "frames": [{"image_name": "TEST-001_00-00-01.jpg", "similarity": 93}],
                },
                {"video_code": "TEST-002", "frames": []},
            ]
        },
        "https://avscan.cc",
        3,
    )
    assert len(result.items) == 2
    item = result.items[0]
    assert item.similarity == 93
    assert [f["timestamp"] for f in item.extra["frames"]] == ["02:01:28", "00:00:01"]
    assert item.thumbnail == "https://avscan.cc/thumb/TEST-001/TEST-001_02-01-28.webp"
    assert result.items[1].similarity is None


@pytest.mark.parametrize(
    "status,payload,code",
    [
        (429, {}, "rate_limited"),
        (403, {}, "verification_required"),
        (503, {"detail": {"code": "verification_required"}}, "verification_required"),
        (504, {"detail": {"code": "timeout"}}, "timeout"),
        (200, [1], "invalid_response"),
    ],
)
def test_api_errors(status, payload, code):
    with pytest.raises(SearchError) as error:
        json_payload(httpx.Response(status, json=payload))
    assert error.value.code == code


async def test_soutubot_upload_factor_and_auth(png):
    def respond(request):
        assert request.url.path == "/api/search"
        assert request.headers["authorization"] == "Bearer secret"
        assert b'name="factor"\r\n\r\n1.4' in request.content
        assert b'name="file"; filename="search.jpg"' in request.content
        return httpx.Response(200, json={"results": []})

    settings = Settings(soutubot_token="secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await search_api("soutubot", prepare_image(png), settings, client, 3, True)
    assert result.status == "empty"


@pytest.fixture
def mock_general(monkeypatch):
    original = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(
            providers.httpx,
            "AsyncClient",
            lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
        )

    return install


async def test_yandex_real_parser(mock_general, png):
    site = {
        "url": "https://example.org/work",
        "title": "Example",
        "thumb": {"url": "//example.org/a.jpg"},
        "domain": "example.org",
        "description": "test",
        "originalImage": {"width": 80, "height": 60},
    }
    state = {"initialState": {"cbirSites": {"sites": [site]}}}
    page = (
        '<html><body><div class="Root" id="ImagesApp-test" data-state="'
        + html.escape(json.dumps(state), quote=True)
        + '"></div></body></html>'
    )
    mock_general(lambda request: httpx.Response(200, text=page))
    result = await search_general("yandex", prepare_image(png), Settings(), 3)
    assert result.items[0].title == "Example"
    assert result.items[0].similarity is None
    assert result.items[0].thumbnail == "https://example.org/a.jpg"


async def test_yandex_empty_real_parser(mock_general, png):
    state = html.escape(json.dumps({"initialState": {"cbirSites": {"sites": []}}}), quote=True)
    mock_general(
        lambda request: httpx.Response(
            200,
            text=f'<html><body><div class="Root" id="ImagesApp-test" data-state="{state}"></div></body></html>',
        )
    )
    result = await search_general("yandex", prepare_image(png), Settings(), 3)
    assert result.status == "empty"


async def test_ascii2d_real_parser_skips_query_item(mock_general, png):
    page = """<div class="row item-box"><img src="/query.jpg"><small>80x60</small></div>
    <div class="row item-box"><img src="/a.jpg"><small>80x60</small>
    <div class="detail-box gray-link"><h6><a href="https://example.org/work">Artwork</a></h6>
    <small>pixiv</small><a href="https://example.org/author">Author</a></div></div>"""
    mock_general(lambda request: httpx.Response(200, text=page))
    result = await search_general("ascii2d", prepare_image(png), Settings(), 3)
    assert len(result.items) == 1
    assert result.items[0].title == "Artwork"


async def test_saucenao_real_parser_and_no_key_in_result(mock_general, png, caplog):
    payload = {
        "header": {"status": 0, "query_image_display": "/query.jpg"},
        "results": [
            {
                "header": {
                    "similarity": "98.20",
                    "thumbnail": "https://example.org/a.jpg",
                    "index_id": 5,
                    "index_name": "Pixiv",
                },
                "data": {"title": "Artwork", "ext_urls": ["https://example.org/work"], "creator": "Author"},
            }
        ],
    }

    def response(request):
        assert "SECRET" not in str(request.url)
        assert b'name="api_key"\r\n\r\nSECRET' in request.content
        return httpx.Response(200, json=payload)

    caplog.set_level("INFO", logger="httpx")
    mock_general(response)
    result = await search_general("saucenao", prepare_image(png), Settings(saucenao_api_key="SECRET"), 3)
    assert result.items[0].similarity == 98.2
    assert "SECRET" not in json.dumps(result.to_dict())
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize(
    "status,text,code",
    [(200, "<title>Just a moment...</title>", "verification_required"), (429, "limit", "http_status")],
)
async def test_general_verification_and_rate_limits(mock_general, png, status, text, code):
    mock_general(lambda request: httpx.Response(status, text=text))
    with pytest.raises((SearchError, httpx.HTTPStatusError)) as error:
        await search_general("yandex", prepare_image(png), Settings(), 3)
    if code != "http_status":
        assert error.value.code == code


async def test_yandex_maintenance_page(mock_general, png):
    mock_general(lambda request: httpx.Response(200, text="<html>The service is under construction.</html>"))
    with pytest.raises(SearchError) as error:
        await search_general("yandex", prepare_image(png), Settings(), 3)
    assert error.value.code == "unavailable"


async def test_general_follows_redirect_before_parsing(mock_general, png):
    def response(request):
        if request.url.path == "/search/file":
            return httpx.Response(302, headers={"location": "/search/color/result"})
        return httpx.Response(200, text="<html><body>No matches</body></html>")

    mock_general(response)
    result = await search_general("ascii2d", prepare_image(png), Settings(), 3)
    assert result.status == "empty"
