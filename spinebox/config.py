"""設定の読み込み。

TOML を環境変数 `SPINEBOX_CONFIG` のパスから、無ければカレントの `config.local.toml` から読む。
どちらも無ければ既定値(蔵書フォルダ 0 件)で動く。相対パスは設定ファイルのあるフォルダを起点に解決する
(サービスとして起動したときのカレントに左右されないため)。
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV = "SPINEBOX_CONFIG"
DEFAULT_CONFIG_NAME = "config.local.toml"
GIB = 1024 ** 3


class ConfigError(ValueError):
    """設定ファイルの値が不正。"""


@dataclass(frozen=True)
class ExtractionLimits:
    max_total_bytes: int = 64 * GIB
    max_entry_bytes: int = 2 * GIB
    max_entries: int = 100_000
    max_depth: int = 1


@dataclass(frozen=True)
class Settings:
    library_roots: tuple[Path, ...] = ()
    data_dir: Path = Path("data")
    cache_limit_bytes: int = 2 * GIB
    host: str = "127.0.0.1"
    port: int = 8000
    sevenzip_path: str = "7z"
    limits: ExtractionLimits = field(default_factory=ExtractionLimits)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spinebox.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def _int(table: dict, key: str, default: int, *, minimum: int = 0) -> int:
    value = table.get(key, default)
    # bool は int の派生なので明示的に弾く
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} は整数で指定してください: {value!r}")
    if value < minimum:
        raise ConfigError(f"{key} は {minimum} 以上で指定してください: {value!r}")
    return value


def _str(table: dict, key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} は空でない文字列で指定してください: {value!r}")
    return value


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path)


def parse_settings(data: dict, base_dir: Path) -> Settings:
    roots = data.get("library_roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) and r for r in roots):
        raise ConfigError("library_roots は空でない文字列の配列で指定してください")
    limits_table = data.get("limits", {})
    if not isinstance(limits_table, dict):
        raise ConfigError("[limits] はテーブルで指定してください")
    d = ExtractionLimits()
    limits = ExtractionLimits(
        max_total_bytes=_int(limits_table, "max_total_bytes", d.max_total_bytes, minimum=1),
        max_entry_bytes=_int(limits_table, "max_entry_bytes", d.max_entry_bytes, minimum=1),
        max_entries=_int(limits_table, "max_entries", d.max_entries, minimum=1),
        max_depth=_int(limits_table, "max_depth", d.max_depth, minimum=0),
    )
    defaults = Settings()
    return Settings(
        library_roots=tuple(_resolve(base_dir, r) for r in roots),
        data_dir=_resolve(base_dir, _str(data, "data_dir", str(defaults.data_dir))),
        cache_limit_bytes=_int(data, "cache_limit_bytes", defaults.cache_limit_bytes, minimum=1),
        host=_str(data, "host", defaults.host),
        port=_int(data, "port", defaults.port, minimum=1),
        sevenzip_path=_str(data, "sevenzip_path", defaults.sevenzip_path),
        limits=limits,
    )


def config_path() -> Path | None:
    """読む設定ファイル。環境変数で指定されていれば、存在しなくてもそれを返す(誤設定を黙殺しない)。"""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    local = Path.cwd() / DEFAULT_CONFIG_NAME
    return local if local.is_file() else None


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    if path is None:
        return parse_settings({}, Path.cwd())
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"設定ファイルが見つかりません: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"設定ファイルを解釈できません: {path}: {e}") from e
    return parse_settings(data, Path(path).resolve().parent)
