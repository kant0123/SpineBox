"""公開 DB(国立国会図書館サーチ・メディア芸術データベース・Bangumi)のクライアント。

入口は `MetadataService(settings.metadata, conn).search(SearchQuery(...))`。取得元ごとの `SearchOutcome`
(候補の列・確からしさ・失敗の表現)を返す。DB の作品・巻への結線はこのパッケージの外(後続)で行う。
詳細は wiki/components/metadata-clients.md。
"""
from .model import (  # noqa: F401
    KIND_MANGA, KIND_NOVEL, SCOPE_VOLUME, SCOPE_WORK, Candidate, Failure, Match, MetadataItem, ResponseRef,
    SearchOutcome, SearchQuery,
)
from .service import SOURCE_CLASSES, MetadataService  # noqa: F401
