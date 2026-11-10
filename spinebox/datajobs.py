"""データ管理のジョブの中身と、画面に並べるジョブの一覧(#83。wiki/concepts/data-jobs.md)。

ジョブはどれも蔵書を読むだけ(スキャン・解析・指紋・表紙・公開 DB の取得・受け入れフォルダの同期)。
整理処理(CBZ 化・改名・受け入れの承認)は蔵書を動かすので、ここには入れない(プレビューを見てから明示的に実行する)。
実行の順番・中止・履歴は spinebox/jobs.py。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass
from typing import Callable

from . import db, follows, linking, works
from .cache import CacheTooLarge
from .config import Settings
from .duplicates import DuplicateScanner
from .jobs import JobContext, JobFn
from .library import Library, VolumeNotFound, VolumeOffline, VolumeUnreadable
from . import analysis
from .metadata import MetadataService
from .scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, Scanner, ScanResult
from .thumbs import Thumbnails

log = logging.getLogger(__name__)


class Unavailable(Exception):
    """設定などの理由で、そのジョブを今は動かせない(取得元が無効・受け入れフォルダが無い)。"""


@dataclass(frozen=True)
class Kind:
    kind: str
    group: str
    label: str
    description: str
    # 画面の一覧に出すか。出さない種類も API からは依頼でき、履歴には名前つきで残る
    listed: bool = True


# 画面に並べる順。group は画面の見出しで、先頭の「いつもの更新」だけを開いて見せ、残りは畳む(#207)
# 公開 DB をまとめて取るとき、何作品ごとに名寄せを挟むか(#288)
SYNC_EVERY = 25

GROUP_DAILY = "いつもの更新"
GROUP_OTHER = "個別の処理"
KINDS: list[Kind] = [
    Kind("daily_scan", GROUP_DAILY, "新しいファイルを取り込む",
         "新しいファイルをスキャンして登録します。変わっていないファイルは読み直さず、作品の情報・手で直した値はそのままです。"),
    Kind("daily_metadata", GROUP_DAILY, "足りない作品情報を補う",
         "公開データベースの情報がまだ無い(または取り直しの時期になった)作品だけを取得します。"),
    Kind("full_sync", GROUP_DAILY, "ライブラリ全体を同期",
         "全ファイルを確かめ、足りない情報(中の巻の解析・作品の名寄せ・公開データベース・表紙)を順に補います。"
         "入れ子のアーカイブを取り出すので時間がかかります。"),
    Kind("maintenance", GROUP_OTHER, "全ファイルを確かめ直す",
         "全ファイルの目次を確かめ直します。中の巻の解析・表紙・指紋は使い回します。見えなくなったものは消さず、"
         "見つからない・オフラインの印を付けるだけです。"),
    # 「新しいファイルを取り込む」と同じ処理。一覧に 2 つ並べると違いを探して迷うので出さない(#207)
    Kind("scan", GROUP_OTHER, "ファイルのスキャンのみ",
         "ファイルの検出とページ数の読み取り(巻の一覧の「スキャン」と同じ)。内側のアーカイブは取り出しません。", listed=False),
    Kind("metadata_thumbs", GROUP_OTHER, "情報と表紙を補完", "公開データベースの情報(補完待ちの作品)と、まだ無い表紙を補います。"),
    Kind("metadata", GROUP_OTHER, "作品情報のみ補完", "公開データベースの情報だけを補います。全作品の取り直しも選べます。"),
    Kind("thumbnails", GROUP_OTHER, "表紙のみ補完",
         "作品の表紙をまだ無いものだけ作ります(入れ子のアーカイブの巻も取り出して作ります)。"),
    Kind("analyze", GROUP_OTHER, "中の巻を調べる", "まだ調べていない内側のアーカイブを全部開いて、中の巻に分けます。"),
    Kind("duplicates", GROUP_OTHER, "重複を調べる", "見た目の指紋を作り、別スキャンの重複の候補を出します。何も消しません。"),
    Kind("follow_check", GROUP_OTHER, "フォロー中の作品の続刊を調べる",
         "フォローした作品の続刊・欠けを、国立国会図書館サーチで調べます(本の入手はしません)。結果はフォローの画面に出ます。"),
    Kind("works_refresh", GROUP_OTHER, "作品の名寄せを計算し直す",
         "巻を作品にまとめ直し、統合の提案を取り直します。手で決めたことは変えません。"),
    Kind("inbox_sync", GROUP_OTHER, "受け入れフォルダを同期",
         "受け入れフォルダを走査して新しい本を検出し、整理案を作ります(承認は受け入れの画面で)。"),
]
KIND_BY_NAME = {k.kind: k for k in KINDS}
# 画面の一覧には出さないが、履歴に残る種類(作品ページの「情報を取得」・整理・受け入れの承認・裏の定期処理)
EXTRA_LABELS = {"work_metadata": "作品の情報を取得", "background_analysis": "裏の解析(中の巻を調べる)",
                "background_metadata": "裏の公開データベースの取得",
                "background_follow": "裏の続刊の確認",
                "organize_execute": "整理を実行",
                "organize_undo": "整理を元に戻す",
                "inbox_approve": "受け入れを承認"}


def work_title(c: sqlite3.Connection, work_id: int) -> str:
    """進み具合・履歴に出す作品名(無ければ id)。"""
    r = c.execute("SELECT value FROM work_fields WHERE work_id = ? AND field = ?", (work_id, works.FIELD_TITLE)).fetchone()
    return r[0] if r and r[0] else f"作品 {work_id}"


def kind_label(kind: str) -> str:
    k = KIND_BY_NAME.get(kind)
    return k.label if k else EXTRA_LABELS.get(kind, kind)


class DataJobs:
    """ジョブの関数を組み立てる。各関数は自分で DB の接続を開いて閉じる(ジョブはリクエストの外のスレッドで動く)。"""

    def __init__(self, settings: Settings, library: Library, thumbs: Thumbnails,
                 metadata_service: Callable[[sqlite3.Connection], MetadataService],
                 enabled_sources: tuple[str, ...], sync_works: Callable[..., None],
                 follow_service: Callable[[sqlite3.Connection], MetadataService] | None = None):
        self.settings = settings
        self.library = library
        self.thumbs = thumbs
        self.metadata_service = metadata_service
        self.enabled_sources = enabled_sources
        self.sync_works = sync_works
        self.follow_service = follow_service or metadata_service

    # --- 組み立て --------------------------------------------------------------------

    def build(self, kind: str, params: dict) -> JobFn:
        """ジョブの関数。動かせない設定なら Unavailable、知らない種類なら KeyError。"""
        if kind in ("daily_metadata", "metadata", "metadata_thumbs", "work_metadata") and not self.enabled_sources:
            raise Unavailable("公開データベースの取得元が設定で無効になっています")
        if kind in ("follow_check", "background_follow") and follows.SOURCE not in self.enabled_sources:
            raise Unavailable("国立国会図書館サーチが設定で無効になっています")
        if kind == "inbox_sync" and not self.settings.inboxes:
            raise Unavailable("受け入れフォルダが設定されていません")
        steps: dict[str, Callable[[JobContext, sqlite3.Connection], dict]] = {
            "daily_scan": lambda ctx, c: self._scan_and_group(ctx, c),
            "scan": lambda ctx, c: self._scan_and_group(ctx, c),
            "maintenance": lambda ctx, c: self._scan_and_group(ctx, c, recheck=True),
            "full_sync": self._full_sync,
            "daily_metadata": lambda ctx, c: self._metadata(ctx, c, all_works=False),
            "metadata": lambda ctx, c: self._metadata(ctx, c, all_works=bool(params.get("all"))),
            "metadata_thumbs": self._metadata_and_thumbs,
            "thumbnails": self._thumbnails,
            "analyze": self._analyze,
            "duplicates": self._duplicates,
            "follow_check": lambda ctx, c: self._follow_check(ctx, c, params.get("work_ids")),
            "works_refresh": lambda ctx, c: asdict(works.sync(c)),
            "inbox_sync": self._inbox_sync,
            "work_metadata": lambda ctx, c: self._metadata(ctx, c, ids=[int(params["work_id"])]),
        }
        step = steps[kind]

        def run(ctx: JobContext) -> dict:
            c = db.connect(self.settings.db_path)
            try:
                return step(ctx, c)
            finally:
                c.close()
        return run

    # --- 個々の処理 ------------------------------------------------------------------

    def scan(self, ctx: JobContext, c: sqlite3.Connection, *, recheck: bool = False,
             inbox: bool = False) -> ScanResult:
        count = 0

        def on_source(rel: str) -> None:
            nonlocal count
            count += 1
            ctx.job.done, ctx.job.current = count, rel  # 中止は should_stop で見る(ここでは投げない)

        scanner = Scanner(c, self.settings, recheck=recheck, should_stop=ctx.cancelled, on_source=on_source)
        result = scanner.scan(inbox=inbox)
        return result

    @staticmethod
    def scan_summary(result: ScanResult) -> dict:
        return {"volumes": result.volumes, "offline_roots": result.offline_roots, "issues_total": result.issues_total}

    def _scan_and_group(self, ctx: JobContext, c, *, recheck: bool = False) -> dict:
        ctx.step("ファイルのスキャン")
        result = self.scan(ctx, c, recheck=recheck)
        self.sync_works(c)  # 途中で止めても、そこまでに見つけた巻は作品に割り当てる(名前の解析だけ)
        ctx.check()  # 止めたスキャンは中止として残す(scanner は残りのソースを読まずに返っている)
        return self.scan_summary(result)

    def _analyze(self, ctx: JobContext, c) -> dict:
        ctx.step("中の巻を調べる")
        ids = self.library.pending_analysis(c)  # 解析で新しい未解析の巻は増えないので、一度だけ引く
        out = {"analyzed": 0, "errors": 0, "skipped": 0}
        try:
            for i, volume_id in enumerate(ids):
                title = c.execute("SELECT title FROM volumes WHERE id = ?", (volume_id,)).fetchone()
                ctx.progress(i, len(ids), title[0] if title else str(volume_id))
                try:
                    self.library.analyze(c, volume_id)
                    out["analyzed"] += 1
                except (VolumeOffline, VolumeNotFound):
                    out["skipped"] += 1  # 見えなくなった巻は記録しない(再接続後にまた候補になる)
                except (VolumeUnreadable, CacheTooLarge) as e:
                    analysis.record_error(c, volume_id, str(e))  # 7-Zip が無い・壊れた・上限超え
                    out["errors"] += 1
            ctx.progress(len(ids), len(ids))
        finally:
            if out["analyzed"]:
                self.sync_works(c)  # 中の巻(子)が現れた
        return out

    def work_ids_needing_fetch(self, c) -> list[int]:
        return linking.works_needing_fetch(c, self.settings.metadata, self.enabled_sources)

    def _metadata(self, ctx: JobContext, c, *, all_works: bool = False, ids: list[int] | None = None) -> dict:
        """ids を渡すと(作品ページの「情報を取得」)、その作品を全取得元で調べる。渡さなければ全作品をまとめて取る
        経路で、連携したところで残りの取得元を引かない軽い取得にする(`linking.fetch_work(quick=True)`。#288)。"""
        ctx.step("公開データベースの取得")
        quick = ids is None
        if ids is None:
            ids = ([w["id"] for w in works.list_works(c)["works"]] if all_works else self.work_ids_needing_fetch(c))
        if not ids:
            return {"works": 0, "sources": {}}
        service = self.metadata_service(c)
        counts: dict[str, int] = {}
        fetched = 0
        try:
            for i, work_id in enumerate(ids):
                ctx.progress(i, len(ids), work_title(c, work_id))
                try:
                    result = linking.fetch_work(c, service, work_id, quick=quick)
                except works.WorkNotFound:
                    if len(ids) == 1:
                        raise
                    continue  # 取得の途中で別の作品に統合された
                fetched += 1
                for status in result.sources.values():
                    counts[status] = counts.get(status, 0) + 1
                # 名寄せは全作品を見直すので(本番 1,102 作品で 1 回 5 秒)、作品ごとには走らせない。
                # 途中の名寄せは、取得で入った別名で統合された作品を残りの取得から外すため(外れなくても、
                # その作品を余分に取得するだけで、最後の名寄せが連携を統合後の作品へ移す)
                if (i + 1) % SYNC_EVERY == 0 and i + 1 < len(ids):
                    self.sync_works(c, apply_links=False)
            ctx.progress(len(ids), len(ids))
        finally:
            self.sync_works(c)  # 中止・失敗でも、取得できた分の連携を統合後の作品へ反映する
        return {"works": len(ids), "fetched": fetched, "sources": counts}

    def _follow_check(self, ctx: JobContext, c, work_ids: list[int] | None) -> dict:
        """フォロー中の作品(work_ids があればその中のフォロー中のもの)の続刊・欠けを 1 作品ずつ調べる。
        失敗は作品ごとに follows.failure へ記録して続ける。中止は 1 作品ごとの区切り(調べた分は記録済み)。"""
        ctx.step("続刊の確認")
        followed = follows.followed_ids(c)
        ids = followed if work_ids is None else [i for i in work_ids if i in set(followed)]
        if not ids:
            return {"works": 0, "statuses": {}}
        try:
            counts = follows.check_many(c, self.follow_service(c), ids,
                                        progress=lambda done, total, cur: ctx.progress(
                                            done, total, work_title(c, cur) if cur is not None else None))
        finally:
            self.sync_works(c)  # 中止・失敗でも、取得できた連携を統合後の作品へ反映する
        return {"works": len(ids), "statuses": counts}

    def thumbnail_targets(self, c) -> list[int]:
        return [w["cover_volume_id"] for w in works.list_works(c)["works"] if w["cover_volume_id"] is not None]

    def _thumbnails(self, ctx: JobContext, c) -> dict:
        ctx.step("表紙の作成")
        ids = self.thumbnail_targets(c)
        return asdict(self.thumbs.build(c, ids, lambda done, total: ctx.progress(done, total)))

    def _duplicates(self, ctx: JobContext, c) -> dict:
        ctx.step("重複を調べる")
        summary = DuplicateScanner(c, self.library, lambda done, total: ctx.progress(done, total)).run()
        self.sync_works(c)  # 重複候補の証拠で、作品の統合を提案・実行する
        return asdict(summary)

    def _inbox_sync(self, ctx: JobContext, c) -> dict:
        ctx.step("受け入れフォルダの同期")
        result = self.scan(ctx, c, inbox=True)
        ctx.check()
        return self.scan_summary(result)

    def _metadata_and_thumbs(self, ctx: JobContext, c) -> dict:
        ctx.plan(2)
        return {"metadata": self._metadata(ctx, c), "thumbnails": self._thumbnails(ctx, c)}

    def _full_sync(self, ctx: JobContext, c) -> dict:
        # スキャン → 中の巻 → 名寄せ →(取得元が有効なら)公開データベース → 表紙
        ctx.plan(5 if self.enabled_sources else 4)
        out = {"scan": self._scan_and_group(ctx, c, recheck=True), "analyze": self._analyze(ctx, c)}
        ctx.step("作品の名寄せ")
        out["works"] = asdict(works.sync(c))
        if self.enabled_sources:
            out["metadata"] = self._metadata(ctx, c)
        out["thumbnails"] = self._thumbnails(ctx, c)
        return out

    # --- 裏の定期処理(1 回に 1 件) --------------------------------------------------

    def background_analysis(self, ctx: JobContext) -> dict | None:
        c = db.connect(self.settings.db_path)
        try:
            volume_id = self.library.analyze_next(c)
            return None if volume_id is None else {"analyzed": 1}
        finally:
            c.close()

    def background_metadata(self, ctx: JobContext) -> dict | None:
        c = db.connect(self.settings.db_path)
        try:
            ids = self.work_ids_needing_fetch(c)
            if not ids:
                return None
            ctx.progress(current=work_title(c, ids[0]))
            linking.fetch_work(c, self.metadata_service(c), ids[0], quick=True)
            self.sync_works(c)
            return {"works": 1}
        finally:
            c.close()

    def background_follow(self, ctx: JobContext) -> dict | None:
        """設定で有効にしたとき、interval_hours 以上調べていないフォロー中の作品を 1 回に 1 作品調べる。"""
        c = db.connect(self.settings.db_path)
        try:
            ids = follows.works_due(c, self.settings.follow.interval_hours)
            if not ids:
                return None
            ctx.progress(current=work_title(c, ids[0]))
            follows.check_many(c, self.follow_service(c), ids[:1])
            self.sync_works(c)
            return {"works": 1}
        finally:
            c.close()

    # --- 画面の一覧(対象の件数つき) --------------------------------------------------

    def catalog(self, c: sqlite3.Connection) -> list[dict]:
        def safely(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001 (件数は補助の表示。数えられなくても一覧は出す)
                log.exception("ジョブの対象を数えられませんでした")
                return None

        volumes = safely(lambda: c.execute("SELECT COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE).fetchone()[0])
        work_list = safely(lambda: works.list_works(c)["works"])
        needing = safely(lambda: len(self.work_ids_needing_fetch(c))) if self.enabled_sources else None
        unanalyzed = safely(lambda: len(self.library.pending_analysis(c)))
        no_cover = safely(lambda: self._count_missing_covers(c, work_list or []))
        unprinted = safely(lambda: len(DuplicateScanner(c, self.library).pending(set())))
        inbox = safely(lambda: c.execute(
            """SELECT COUNT(DISTINCT s.id) FROM sources s JOIN library_roots r ON r.id = s.root_id
               JOIN volumes v ON v.source_id = s.id
               WHERE r.inbox = 1 AND r.enabled = 1 AND s.missing = 0 AND v.missing = 0
                 AND v.organized_op_id IS NULL""").fetchone()[0]) if self.settings.inboxes else None
        n_works = len(work_list) if work_list is not None else None
        n_follow = safely(lambda: len(follows.followed_ids(c))) if follows.SOURCE in self.enabled_sources else None

        def text(n, unit_text):
            return None if n is None else unit_text.format(n=n)

        counts = {
            "daily_scan": (volumes, text(volumes, "登録済み {n} 巻")),
            "daily_metadata": (needing, text(needing, "登録済みの {n} 作品が補完待ち")),
            "maintenance": (volumes, text(volumes, "{n} 巻を確かめ直します")),
            "full_sync": (None, None),
            "scan": (volumes, text(volumes, "登録済み {n} 巻")),
            "metadata_thumbs": (None, "・".join(t for t in (text(needing, "補完待ち {n} 作品"),
                                                         text(no_cover, "表紙なし {n} 作品")) if t) or None),
            "metadata": (needing, text(needing, "補完待ち {n} 作品")),
            "thumbnails": (no_cover, text(no_cover, "表紙なし {n} 作品")),
            "analyze": (unanalyzed, text(unanalyzed, "未解析 {n} 巻")),
            "duplicates": (unprinted, text(unprinted, "指紋が未作成 {n} 巻")),
            "follow_check": (n_follow, text(n_follow, "フォロー中 {n} 作品")),
            "works_refresh": (n_works, text(n_works, "{n} 作品")),
            "inbox_sync": (inbox, text(inbox, "受け入れ待ち {n} 件")),
        }
        out = []
        for k in KINDS:
            if not k.listed:
                continue
            try:
                self.build(k.kind, {})
                unavailable = None
            except Unavailable as e:
                unavailable = str(e)
            count, count_text = counts[k.kind]
            out.append({"kind": k.kind, "group": k.group, "label": k.label, "description": k.description,
                        "count": count, "count_text": count_text, "available": unavailable is None,
                        "unavailable_reason": unavailable,
                        "options": [{"name": "all", "label": "全作品を取り直す"}] if k.kind == "metadata" else []})
        return out

    def _count_missing_covers(self, c, work_list: list[dict]) -> int:
        """表紙の巻があるのに、まだ表紙が作られていない作品を数える。
        表紙の JPEG は読まない(読むと容量上限の LRU の順が変わる)。巻の行は IN でまとめて引く。
        見えない・オフライン・元ファイルが無い巻は数えない(表紙を作るジョブもその巻を飛ばすため)。"""
        cover_ids = [w["cover_volume_id"] for w in work_list if w["cover_volume_id"] is not None]
        built = self.thumbs.are_built(c, cover_ids, include_unavailable=False)
        return sum(1 for vid in cover_ids if built.get(vid) is False)
