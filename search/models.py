from dataclasses import asdict, dataclass, field
from typing import Any

PLATFORMS = ("yandex", "saucenao", "ascii2d", "soutubot", "avscan")
GENERAL_PLATFORMS = PLATFORMS[:3]
LABELS = {
    "yandex": "Yandex",
    "saucenao": "SauceNAO",
    "ascii2d": "ascii2d",
    "soutubot": "SoutuBot",
    "avscan": "AVScan",
}


class SearchError(Exception):
    """A public error with a stable code and no upstream credentials."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class SearchItem:
    source: str
    title: str
    url: str = ""
    thumbnail: str = ""
    similarity: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    platform: str
    status: str = "ok"
    items: list[SearchItem] = field(default_factory=list)
    result_url: str = ""
    error: dict[str, str] | None = None

    @classmethod
    def failed(cls, platform: str, error: SearchError):
        return cls(
            platform=platform,
            status="error",
            error={"code": error.code, "message": str(error)},
        )

    def to_dict(self):
        return asdict(self)
