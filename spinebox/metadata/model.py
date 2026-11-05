"""公開 DB クライアントが共有する型。

取得元(国立国会図書館サーチ・AniList・MangaUpdates)の応答は、ここの `MetadataItem` に正規化して返す。
DB の作品・巻への結線、取得元どうしの優先順位マージ、手動編集の保護はこのパッケージの外(後続)で行う。
ここは「候補の列」と「候補ごとの確からしさ(数値と根拠)」を返すところまで。
"""
from __future__ import annotations

from dataclasses import dataclass, field

KIND_MANGA = "manga"
KIND_NOVEL = "novel"

SCOPE_VOLUME = "volume"  # 1 冊(巻)の書誌。国立国会図書館サーチ
SCOPE_WORK = "work"      # 作品全体の情報。AniList / MangaUpdates

# 取得失敗の種別(`Failure.kind`)
FAIL_TIMEOUT = "timeout"
FAIL_CONNECT = "connect"
FAIL_HTTP = "http_status"          # 4xx / 5xx(リトライを使い切った後も含む)
FAIL_RATE_LIMITED = "rate_limited"  # 429、または待つべき時間が長すぎて要求を出さなかった
FAIL_INVALID = "invalid_response"   # JSON / XML として解釈できない、想定した形でない
FAIL_TOO_LARGE = "too_large"
FAIL_DISABLED = "disabled"
FAIL_INTERNAL = "internal"          # このコードの不具合(例外は外へ出さずここに畳む)


@dataclass(frozen=True)
class SearchQuery:
    """検索の入力。title だけが必須。author・volume・kind・isbn は候補の確からしさを上げる手がかり。"""
    title: str
    author: str | None = None
    volume: int | None = None
    kind: str | None = None   # KIND_MANGA / KIND_NOVEL
    isbn: str | None = None   # 国立国会図書館サーチだけが使う(巻の書誌を直接引く)
    limit: int | None = None  # 国立国会図書館サーチだけが使う、取る件数の上限(巻が多い作品で全巻を揃えたいとき)。省略は設定の candidate_limit

    def __post_init__(self) -> None:
        if not self.title or not self.title.strip():
            raise ValueError("title は空にできません")


@dataclass(frozen=True)
class MetadataItem:
    """取得元の 1 件を正規化した項目。取れなかった項目は None / 空のタプル。

    scope が volume のものは巻ごとの書誌(volume・isbn・released が巻の値)、
    work のものは作品全体の情報(total_volumes・タグ・別名)。あらすじは読み取らない(#82 で廃止)。
    genres / tags は取得元の言語のまま(AniList・MangaUpdates は英語)。
    """
    source: str
    source_id: str
    scope: str
    title: str
    source_url: str | None = None
    subtitle: str | None = None
    reading: str | None = None          # 作品名の読み(カタカナ)。国立国会図書館サーチ
    romaji: str | None = None           # ローマ字名
    aliases: tuple[str, ...] = ()       # 別名(title・romaji と重複しないもの)
    authors: tuple[str, ...] = ()
    volume: int | None = None
    volume_label: str | None = None     # 巻次の表記そのまま(「上」「1-2」など数値にできないものも残す)
    total_volumes: int | None = None
    publisher: str | None = None
    label: str | None = None            # シリーズ・出版元(「ガルドコミックス」など)
    released: str | None = None         # YYYY / YYYY-MM / YYYY-MM-DD
    isbn: str | None = None             # ハイフン無しの 13 桁または 10 桁
    kind: str | None = None             # KIND_MANGA / KIND_NOVEL / None(判断できない)
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def names(self) -> tuple[str, ...]:
        """この項目の作品名の候補すべて(確からしさの計算が名前を突き合わせる)。"""
        seen: list[str] = []
        for name in (self.title, self.romaji, *self.aliases):
            if name and name not in seen:
                seen.append(name)
        return tuple(seen)


@dataclass(frozen=True)
class Match:
    """検索した名前・著者・巻番号との一致度。score は 0.0〜1.0。

    components は評価できた観点(title / author / volume / kind)ごとの 0.0〜1.0、
    reasons はその根拠(`title:exact` `author:match` `volume:mismatch` など)。
    比べようのない観点(巻の情報が無い、著者の表記体系が違う)は components に入れず reasons に残す。
    自動で採用するかどうかの判断は、この数値を見る側(後続の結線)が決める。
    """
    score: float
    components: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    item: MetadataItem
    match: Match
    # この候補を取り出した要求の鍵(`metadata_responses.request_key`)。生応答を引き直す手がかり
    response_key: str | None = None


@dataclass(frozen=True)
class Failure:
    """取得失敗の表現。message に URL・クエリ・秘匿情報は入れない。"""
    kind: str
    message: str
    status: int | None = None
    retry_after: float | None = None  # 秒。429 のときだけ


@dataclass(frozen=True)
class ResponseRef:
    """検索で使った生応答 1 件の出所。"""
    request_key: str
    label: str
    fetched_at: str
    from_cache: bool
    stale: bool = False  # 有効期限切れの保存済み応答を、取得失敗の代わりに使った


@dataclass(frozen=True)
class SearchOutcome:
    """1 つの取得元に対する検索の結果。例外は投げず、失敗は failure に入れて返す。

    failure が None なら検索は成功で、candidates が 0 件でも「見つからなかった」という成功。
    候補は確からしさの高い順。巻ごとの補足(MangaUpdates の詳細)が取れなかった候補は
    notes に理由を残し、検索結果の項目だけで返す。
    """
    source: str
    candidates: tuple[Candidate, ...] = ()
    failure: Failure | None = None
    responses: tuple[ResponseRef, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failure is None

    @property
    def from_cache(self) -> bool:
        return bool(self.responses) and all(r.from_cache for r in self.responses)
