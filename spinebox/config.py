"""設定の読み込み。

TOML を環境変数 `SPINEBOX_CONFIG` のパスから、無ければカレントの `config.local.toml` から読む。
どちらも無ければ既定値(蔵書フォルダ 0 件)で動く。相対パスは設定ファイルのあるフォルダを起点に解決する
(サービスとして起動したときのカレントに左右されないため)。
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .organize.fs import is_within
from .organize.template import DEFAULT_NAME_TEMPLATE, NameTemplate, TemplateError

log = logging.getLogger(__name__)

CONFIG_ENV = "SPINEBOX_CONFIG"
DEFAULT_CONFIG_NAME = "config.local.toml"
MIB = 1024 ** 2
GIB = 1024 ** 3
DEFAULT_ORGANIZE_DIR = ".spinebox"


class ConfigError(ValueError):
    """設定ファイルの値が不正。"""


@dataclass(frozen=True)
class ExtractionLimits:
    max_total_bytes: int = 64 * GIB
    max_entry_bytes: int = 2 * GIB
    max_entries: int = 100_000
    max_depth: int = 3


# 公開 DB クライアント(spinebox/metadata/)の既定。最小間隔の根拠は wiki/concepts/metadata-sources.md
DEFAULT_METADATA_SOURCES = ("ndl", "anilist", "mangaupdates")
# nhentai / ehentai は同人誌用で、既定の sources には入れない(利用者が設定で有効にする)。
# nHentai の匿名の制限は検索 10 回/分(= 6 秒に 1 回)。E-Hentai は「4〜5 回連続したら 5 秒待つ」程度の記述しかない
DEFAULT_MIN_INTERVALS = {"ndl": 1.0, "anilist": 2.0, "mangaupdates": 1.0, "nhentai": 6.5, "ehentai": 4.0}


UNTRANSLATED_TERMS = ("hide", "keep")
# 廃止した [metadata] の項目。古い設定ファイルに残っていても起動は止めず、無視してログで知らせる
RETIRED_METADATA_KEYS = {"english_synopsis": "あらすじは廃止した(#82)"}


@dataclass(frozen=True)
class MetadataSettings:
    """外部メタデータ取得(ベストエフォート)の設定。"""
    sources: tuple[str, ...] = DEFAULT_METADATA_SOURCES  # 有効にする取得元。空なら取得しない
    cache_ttl_days: float = 30.0       # 保存済みの生応答を使い回す期間
    timeout_seconds: float = 10.0
    max_retries: int = 2               # 5xx・タイムアウト・接続不可の再試行回数(429 は再試行しない)
    candidate_limit: int = 20          # 1 回の検索で取る候補の上限
    detail_limit: int = 3              # MangaUpdates・nHentai で詳細を引き直す候補の数
    contact: str = ""                  # User-Agent に付ける連絡先(任意)
    # 作品への結線(#50)。background: 設定で有効にしたときだけ、裏で 1 作品ずつ公開 DB の情報を取りに行く
    background: bool = False
    background_interval_seconds: int = 120  # 裏の取得の間隔(1 回に 1 作品)
    recheck_days: float = 30.0         # 取得済みの作品を取り直す間隔
    retry_hours: float = 6.0           # 取得に失敗した作品を取り直すまでの間隔
    min_interval_seconds: dict = field(default_factory=lambda: dict(DEFAULT_MIN_INTERVALS))
    # 日本語での表示(#74)。英語のジャンル・タグは対応表で日本語にする。対応が無い語を hide(出さない)か keep(英語のまま)か
    untranslated_terms: str = "hide"
    # 将来の取得元(楽天ブックスなど)の API キー。設定ファイル(リポジトリ外)にだけ置く。ログ・保存物には出さない
    keys: dict = field(default_factory=dict, repr=False)

    def interval(self, source: str) -> float:
        return float(self.min_interval_seconds.get(source, DEFAULT_MIN_INTERVALS.get(source, 1.0)))


@dataclass(frozen=True)
class FollowSettings:
    """フォロー(#87)。続刊の確認は明示操作が基本で、自動の定期実行は background で有効にしたときだけ。"""
    background: bool = False
    interval_hours: float = 24.0             # 定期実行で、同じ作品を調べ直すまでの間隔
    background_interval_seconds: int = 300   # 定期実行の見回りの間隔(1 回に 1 作品)
    # 続刊の確認で、保存済みの生応答を使い回す期間。公開 DB の補完([metadata] cache_ttl_days)より短くして新刊を拾う
    response_ttl_days: float = 1.0


@dataclass(frozen=True)
class InboxSetting:
    """受け入れフォルダと、承認で本を入れる書庫(`library_roots` のうちの 1 つ)。"""
    path: Path
    library: Path


@dataclass(frozen=True)
class Settings:
    library_roots: tuple[Path, ...] = ()
    # 受け入れフォルダ(`inbox_dirs`)。蔵書には数えない。同期で検出し、承認した分だけ書庫へ入れる
    inboxes: tuple[InboxSetting, ...] = ()
    data_dir: Path = Path("data")
    cache_limit_bytes: int = 2 * GIB
    # RAM 層(ページのバイト列と 7z / rar の目次)の上限。0 なら RAM 層を使わない。サーバー機では 8 GiB まで上げてよい
    memory_cache_limit_bytes: int = 256 * MIB
    # 表紙のサムネイル(`data_dir/thumbs/`)の上限。1 枚 20〜40 KB ほど
    thumb_cache_limit_bytes: int = 256 * MIB
    host: str = "127.0.0.1"
    port: int = 8000
    sevenzip_path: str = "7z"
    # 整理処理(CBZ 化)の作業場・退避フォルダの置き場。相対パスは各蔵書ルートが起点(設定ファイルの場所ではない)。
    # 作業場は蔵書と同じドライブに置く必要があるため(別ドライブになる構成は実行前に拒否する)
    organize_dir: str = DEFAULT_ORGANIZE_DIR
    # 命名テンプレート(`[organize] name_template`)。規則は spinebox/organize/template.py
    name_template: str = DEFAULT_NAME_TEMPLATE
    limits: ExtractionLimits = field(default_factory=ExtractionLimits)
    # 内側アーカイブの巻を裏で少しずつ解析して巻に分解する(既定は無効。開いたとき・明示操作では常に解析する)
    background_analysis: bool = False
    background_analysis_interval: int = 60  # 秒。1 回に 1 巻
    metadata: MetadataSettings = field(default_factory=MetadataSettings)
    follow: FollowSettings = field(default_factory=FollowSettings)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spinebox.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    def inbox_for(self, path: Path | str) -> InboxSetting | None:
        """path(DB に記録した受け入れフォルダ)の設定。設定から外されていれば None。"""
        key = _path_key(path)
        return next((i for i in self.inboxes if _path_key(i.path) == key), None)

    def organize_base(self, root: Path) -> Path:
        """root の整理処理の置き場(作業場 work/ と退避フォルダ trash/ の親)。"""
        path = Path(self.organize_dir).expanduser()
        return path if path.is_absolute() else Path(root) / path


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _inboxes(data: dict, roots: tuple[Path, ...], base_dir: Path) -> tuple[InboxSetting, ...]:
    """`inbox_dirs`: 文字列(書庫が 1 つだけのとき)か {path, library} の配列。

    受け入れフォルダと書庫は重ならない(書庫の中に受け入れフォルダがあると、スキャンが受け入れ待ちの本を蔵書に数える)。
    同じドライブかどうかは、ドライブが見えない間もあるので設定の読み込みでは見ず、同期・承認のときに確かめる。
    """
    raw = data.get("inbox_dirs", [])
    if not isinstance(raw, list):
        raise ConfigError("inbox_dirs は配列で指定してください")
    out: list[InboxSetting] = []
    for entry in raw:
        if isinstance(entry, str):
            path_text, library_text = entry, None
        elif isinstance(entry, dict):
            path_text, library_text = entry.get("path"), entry.get("library")
            if set(entry) - {"path", "library"}:
                raise ConfigError(f"inbox_dirs の項目は path と library だけです: {entry!r}")
            if library_text is not None and not isinstance(library_text, str):
                raise ConfigError(f"inbox_dirs の library は文字列で指定してください: {library_text!r}")
        else:
            raise ConfigError(f"inbox_dirs の項目は文字列か {{ path, library }} で指定してください: {entry!r}")
        if not isinstance(path_text, str) or not path_text:
            raise ConfigError(f"inbox_dirs の path は空でない文字列で指定してください: {entry!r}")
        path = _resolve(base_dir, path_text)
        if library_text:
            library = _resolve(base_dir, library_text)
            if _path_key(library) not in {_path_key(r) for r in roots}:
                raise ConfigError(f"inbox_dirs の library は library_roots のどれかを指定してください: {library_text!r}")
        elif len(roots) == 1:
            library = roots[0]
        else:
            raise ConfigError(f"書庫(library)が決まりません。library_roots が複数あるので、inbox_dirs の library で"
                              f"指定してください: {path_text!r}")
        out.append(InboxSetting(path, library))
    for i, a in enumerate(out):
        for r in roots:
            if is_within(a.path, r) or is_within(r, a.path):
                raise ConfigError(f"受け入れフォルダと蔵書フォルダは重ねられません: {a.path} / {r}")
        for b in out[i + 1:]:
            if is_within(a.path, b.path) or is_within(b.path, a.path):
                raise ConfigError(f"受け入れフォルダどうしは重ねられません: {a.path} / {b.path}")
    return tuple(out)


def _int(table: dict, key: str, default: int, *, minimum: int = 0) -> int:
    value = table.get(key, default)
    # bool は int の派生なので明示的に弾く
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} は整数で指定してください: {value!r}")
    if value < minimum:
        raise ConfigError(f"{key} は {minimum} 以上で指定してください: {value!r}")
    return value


def _bool(table: dict, key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} は true / false で指定してください: {value!r}")
    return value


def _str(table: dict, key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} は空でない文字列で指定してください: {value!r}")
    return value


def _organize_dir(data: dict) -> str:
    value = _str(data, "organize_dir", DEFAULT_ORGANIZE_DIR)
    path = Path(value).expanduser()
    # 相対指定でルートの外へ出る値は、作業場が蔵書の外(別ドライブかもしれない)になるので拒む
    if not path.is_absolute() and (".." in path.parts or str(path) in ("", ".")):
        raise ConfigError(f"organize_dir の相対パスは蔵書ルートの中のフォルダを指してください: {value!r}")
    return value


def _name_template(data: dict) -> str:
    table = data.get("organize", {})
    if not isinstance(table, dict):
        raise ConfigError("[organize] はテーブルで指定してください")
    value = table.get("name_template", DEFAULT_NAME_TEMPLATE)
    try:
        NameTemplate(value)
    except TemplateError as e:
        raise ConfigError(str(e)) from e
    return value.strip()


def _float(table: dict, key: str, default: float, *, minimum: float = 0.0) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} は数値で指定してください: {value!r}")
    if value < minimum:
        raise ConfigError(f"{key} は {minimum} 以上で指定してください: {value!r}")
    return float(value)


def _metadata(data: dict) -> MetadataSettings:
    table = data.get("metadata", {})
    if not isinstance(table, dict):
        raise ConfigError("[metadata] はテーブルで指定してください")
    d = MetadataSettings()
    sources = table.get("sources", list(d.sources))
    if not isinstance(sources, list) or not all(isinstance(s, str) and s for s in sources):
        raise ConfigError("metadata.sources は空でない文字列の配列で指定してください")
    intervals = table.get("min_interval_seconds", {})
    if not isinstance(intervals, dict):
        raise ConfigError("[metadata.min_interval_seconds] はテーブルで指定してください")
    merged = dict(d.min_interval_seconds)
    for name in intervals:
        merged[name] = _float(intervals, name, merged.get(name, 1.0), minimum=0.0)
    keys = table.get("keys", {})
    if not isinstance(keys, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in keys.items()):
        raise ConfigError("[metadata.keys] は文字列だけのテーブルで指定してください")
    contact = table.get("contact", d.contact)
    if not isinstance(contact, str):
        raise ConfigError(f"metadata.contact は文字列で指定してください: {contact!r}")
    untranslated = table.get("untranslated_terms", d.untranslated_terms)
    if untranslated not in UNTRANSLATED_TERMS:
        raise ConfigError(f"metadata.untranslated_terms は {' / '.join(UNTRANSLATED_TERMS)} のどれかで指定してください: "
                          f"{untranslated!r}")
    for key, reason in RETIRED_METADATA_KEYS.items():
        if key in table:
            log.warning("設定 metadata.%s は使われなくなったので無視します(%s)。設定ファイルから消してかまいません",
                        key, reason)
    return MetadataSettings(
        sources=tuple(sources),
        cache_ttl_days=_float(table, "cache_ttl_days", d.cache_ttl_days, minimum=0.0),
        timeout_seconds=_float(table, "timeout_seconds", d.timeout_seconds, minimum=0.1),
        max_retries=_int(table, "max_retries", d.max_retries, minimum=0),
        candidate_limit=_int(table, "candidate_limit", d.candidate_limit, minimum=1),
        detail_limit=_int(table, "detail_limit", d.detail_limit, minimum=0),
        contact=contact.strip(),
        background=_bool(table, "background", d.background),
        background_interval_seconds=_int(table, "background_interval_seconds", d.background_interval_seconds,
                                         minimum=10),
        recheck_days=_float(table, "recheck_days", d.recheck_days, minimum=1.0),
        retry_hours=_float(table, "retry_hours", d.retry_hours, minimum=0.1),
        min_interval_seconds=merged,
        untranslated_terms=untranslated,
        keys=dict(keys),
    )


def _follow(data: dict) -> FollowSettings:
    table = data.get("follow", {})
    if not isinstance(table, dict):
        raise ConfigError("[follow] はテーブルで指定してください")
    d = FollowSettings()
    return FollowSettings(
        background=_bool(table, "background", d.background),
        interval_hours=_float(table, "interval_hours", d.interval_hours, minimum=1.0),
        background_interval_seconds=_int(table, "background_interval_seconds", d.background_interval_seconds,
                                         minimum=10),
        response_ttl_days=_float(table, "response_ttl_days", d.response_ttl_days, minimum=0.0),
    )


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
    library_roots = tuple(_resolve(base_dir, r) for r in roots)
    return Settings(
        library_roots=library_roots,
        inboxes=_inboxes(data, library_roots, base_dir),
        data_dir=_resolve(base_dir, _str(data, "data_dir", str(defaults.data_dir))),
        cache_limit_bytes=_int(data, "cache_limit_bytes", defaults.cache_limit_bytes, minimum=1),
        memory_cache_limit_bytes=_int(data, "memory_cache_limit_bytes", defaults.memory_cache_limit_bytes, minimum=0),
        thumb_cache_limit_bytes=_int(data, "thumb_cache_limit_bytes", defaults.thumb_cache_limit_bytes, minimum=1),
        host=_str(data, "host", defaults.host),
        port=_int(data, "port", defaults.port, minimum=1),
        sevenzip_path=_str(data, "sevenzip_path", defaults.sevenzip_path),
        organize_dir=_organize_dir(data),
        name_template=_name_template(data),
        limits=limits,
        background_analysis=_bool(data, "background_analysis", defaults.background_analysis),
        background_analysis_interval=_int(data, "background_analysis_interval",
                                          defaults.background_analysis_interval, minimum=1),
        metadata=_metadata(data),
        follow=_follow(data),
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
