from collections.abc import Mapping
from dataclasses import dataclass, field

from .models import GENERAL_PLATFORMS, PLATFORMS


@dataclass
class Settings:
    enabled: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(PLATFORMS, True))
    default_platforms: tuple[str, ...] = GENERAL_PLATFORMS
    saucenao_api_key: str = ""
    proxy: str = ""
    soutubot_base_url: str = "http://127.0.0.1:8787"
    soutubot_token: str = ""
    avscan_base_url: str = "https://avscan.cc"
    timeout: int = 30
    soutubot_timeout: int = 120
    wait_timeout: int = 60
    max_results: int = 3
    max_download_mb: int = 10
    show_thumbnails: bool = True
    ascii2d_feature_search: bool = False

    @classmethod
    def from_mapping(cls, config: Mapping):
        defaults = cls()
        settings = cls(
            enabled={p: bool(config.get(f"enable_{p}", True)) for p in PLATFORMS},
            default_platforms=tuple(
                dict.fromkeys(
                    p for p in config.get("default_platforms", GENERAL_PLATFORMS) if p in GENERAL_PLATFORMS
                )
            ),
        )
        for key in ("saucenao_api_key", "proxy", "soutubot_base_url", "soutubot_token", "avscan_base_url"):
            setattr(settings, key, str(config.get(key, getattr(defaults, key))).strip())
        for key, lower, upper in (
            ("timeout", 5, 180),
            ("soutubot_timeout", 10, 300),
            ("wait_timeout", 1, 300),
            ("max_results", 1, 10),
            ("max_download_mb", 1, 30),
        ):
            try:
                value = int(config.get(key, getattr(defaults, key)))
            except (TypeError, ValueError):
                value = getattr(defaults, key)
            setattr(settings, key, max(lower, min(upper, value)))
        for key in ("show_thumbnails", "ascii2d_feature_search"):
            setattr(settings, key, bool(config.get(key, getattr(defaults, key))))
        return settings
