---
type: component
summary: FastAPI のバックエンド(設定・DB・スキャン・閲覧 API)。フロントと共有する API 契約と、蔵書レコードを消さない offline / missing の扱い
updated: 2026-11-09
related: [inbox, metadata-linking, overview, archive-reading, extraction-cache, server-setup, volume-split, organize, duplicate-detection, work-grouping, metadata-clients, metadata-sources, novel-reader, work-search, cover-thumbnails, bookmarks, follow, data-jobs, manga-reader]
---

# バックエンド

`python -m spinebox` で起動する単一プロセスの FastAPI アプリ。蔵書フォルダを走査して巻の候補を DB に登録し、
巻のページ一覧・ページ画像・読書位置を API で返す。`frontend/dist` があれば同じプロセスで静的配信する。

## 責務

- する: 設定の読み込み、スキャン(目次読みだけ)、巻を開いてページを返す、読書位置の保存、
  内側アーカイブの巻の分解と巻範囲の食い違いの検出([[volume-split]])、別スキャンの重複候補の検出(明示操作。[[duplicate-detection]])、
  巻の名前から作る作品の名寄せ(別名・著者・統合の根拠・欠け巻。[[work-grouping]])、小説(EPUB・テキスト)の区切りごとの無害化した本文([[novel-reader]])、作品の全文検索と絞り込み([[work-search]])
- しない: 蔵書への書き込み(読み取り専用。例外は整理処理 [[organize]] だけで、ユーザーの明示操作でだけ動く)、認証。外部メタデータの取得クライアント(`spinebox/metadata/`、[[metadata-clients]])は、作品・巻へ `spinebox/linking.py` で結線する([[metadata-linking]])。ネットワークに触るのは明示操作と設定した定期取得だけで、スキャン・閲覧・編集は通信しない
- アーカイブの中身の扱いは [[archive-reading]]、内側アーカイブの取り出し先は [[extraction-cache]]

## 入口となるファイル

| パス | 役割 |
| --- | --- |
| `spinebox/app.py:235 create_app` | API の全体。例外 → HTTP ステータスの対応もここ |
| `spinebox/app.py recover_interrupted_organize()` | 起動時(lifespan の先頭)に、実行・元に戻すの途中で止まった整理を戻す(#36。[[organize]])。失敗しても起動は止めない |
| `spinebox/__main__.py:17 main` | 起動。`--print-bind` は起動せず `host port` を出す(常駐ラッパーが孤児の検出に使う。[[server-setup]]) |
| `spinebox/buildinfo.py:14 current_commit` | `/healthz` が出す稼働中コミット。git → `.git` 直読みの順で、解決できなければ None |
| `spinebox/scanner.py:505 _scan_archive` | アーカイブを巻の候補に切り出す。開き直さない条件・失敗時の扱い |
| `spinebox/scanner.py:347 _scan_root` | ルートの走査と、見つからなくなったソースの missing 化。ソース単位の例外の捕まえ方もここ |
| `spinebox/scanner.py:319 _source_failed` | 想定外の例外・読めなかったソースの記録(`scan_issues`)。止めてよい例外は `_unexpected` |
| `spinebox/scanner.py:673 collect_issues` | `POST /api/scan` が返す「読めなかったもの」の組み立て |
| `spinebox/library.py:142 open` | 巻を開いてページを並べる(画像フォルダ / アーカイブ内フォルダ / 内側アーカイブ / 分解で作った子の巻) |
| `spinebox/library.py:389 analyze` | 巻への分解。登録と食い違いは `spinebox/analysis.py`、名前の巻番号の候補は `spinebox/naming.py:124 parse_number`([[volume-split]]) |
| `spinebox/duplicates.py:282 DuplicateScanner` | 重複候補。指紋は `spinebox/fingerprint.py:32 image_hash`、判定は `spinebox/duplicates.py:154 judge`、API は `spinebox/app.py` の `/api/duplicates`([[duplicate-detection]]) |
| `spinebox/linking.py:323 fetch_work()` / `spinebox/linking.py:637 apply_work()` | 公開 DB の結線([[metadata-linking]])。取得して連携を保存する `fetch_work`(唯一ネットワークに触る)と、連携から項目・別名・巻の書誌を導く `apply_work`(DB だけ) |
| `spinebox/follows.py:115 check_work()` | フォロー([[follow]])。続刊・欠けを調べる。一覧は `list_follows()` / `list_releases()`(DB だけ)、ジョブ・定期実行の入口は `check_many()` |
| `spinebox/works.py:780 sync` | 作品の名寄せ。割り当て・別名・統合の提案と自動統合、手動編集・付け替え・提案の承認/却下も同じファイル。API は `spinebox/app.py` の `/api/works`([[work-grouping]]) |
| `spinebox/search.py:130 prepare` | 作品の検索の索引(FTS5 trigram)を追いつかせる。引くのは `spinebox/search.py:156 match_ids`、作り直しは `python -m spinebox --rebuild-search`([[work-search]]) |
| `spinebox/scanner.py:61 LISTED_VOLUMES_WHERE` | 一覧に出る巻の条件。スキャンの返す件数と `GET /api/volumes` で共用。受け入れフォルダの巻はここで除く(`r.inbox = 0`。[[inbox]]) |
| `spinebox/config.py:354 load_settings` | 設定ファイルの探し方と相対パスの起点。`[metadata]`(外部メタデータ取得)は `spinebox/config.py:50 MetadataSettings` と `_metadata()` |
| `spinebox/db.py:40 MIGRATIONS` | スキーマ。`PRAGMA user_version` の連番で末尾に足していく |
| `spinebox/stats.py:25 collect()` | 管理の統計タブの数(#85)。数え方の決まりはファイル先頭の docstring |
| `spinebox/jobs.py:149 JobRunner` | データ管理のジョブ。スキャン・補完・表紙などを 1 本ずつ動かし、履歴を `jobs` 表に残す。中身は `spinebox/datajobs.py:93 DataJobs`([[data-jobs]]) |
| `spinebox/thumbs.py:102 Thumbnails` | 表紙のサムネイル。一覧からは `Library.open(extract=False)` で開ける巻だけ作る([[cover-thumbnails]]) |
| `spinebox/novel/books.py:59 Books` | 小説(`volumes.format` が `epub` / `text`)を開く。`Library.open` はページ画像の巻だけ([[novel-reader]]) |

## API 契約(フロントと共有)

フロント(`frontend/`)はこの形を前提にしている。変えるときはこのページと同じ PR で直す。

| メソッドとパス | 返すもの |
| --- | --- |
| `POST /api/scan` | `{"volumes": 件数, "offline_roots": [パス...], "issues": [{"path", "container_path", "entry_name", "reason"}], "issues_total": 件数}`。件数は一覧に出る巻の数(オフラインの巻を含む。分解済みの親は数えず子を数える)。`issues` は「読めなかったもの」(`scan_issues` の現在の内容。最大 200 件で、切った分も `issues_total` には数える)。ほかのジョブ(データ管理。[[data-jobs]])が実行中・待ち中なら待たずに 409。DB が使えないなど続行できない失敗は 500 + `{"detail": "スキャンに失敗しました: 型: メッセージ"}`(トレースバックはログ) |
| `GET /api/libraries` | 蔵書の切り替え(#85)の候補 `[{"id", "name", "path", "online", "volume_count"}]`。設定にある蔵書ルート(受け入れフォルダを除く)。`name` は最後のフォルダ名(Windows のパスも `\` で区切る)、`volume_count` は一覧に見えている巻の数。オフラインのルートも出す |
| `GET /api/volumes` | `[{"id", "title", "source_name", "format", "page_count", "offline", "progress", "parent_id", "parent_title", "volume_number", "findings", "can_analyze"}]`。`page_count` は内側アーカイブの巻だと開くまで null。`progress` は `{"page", "finished"}` か null。分解済みの親は出ず、子が `parent_id` 付きで親の位置に並ぶ。`volume_number` は名前から読んだ候補 `{"kind", "start", "end", "evidence"}` か null。`findings` は巻範囲の食い違い([[volume-split]])。`can_analyze` は未解析の内側アーカイブの巻。`duplicate_count` は未却下の重複候補の数(#38)。`work_id` は割り当てた作品(統合済みなら統合先。#43)。`bookmarked` は巻に印が付いているか([[bookmarks]])。`format` は `images`(ページ画像の巻)/ `epub` / `text`(小説。`page_count` は区切りの数で、開くまで null。#57)。クエリ `root`(`/api/libraries` の id)でその蔵書ルートの巻だけ(知らない id なら空。#85) |
| `GET /api/volumes/{id}` | `{"id", "title", "format", "page_count", "pages": [{"index", "name"}], "progress", "bookmarked", "parent_id", "volume_number", "next_volume", "findings"}`。内側アーカイブの巻はここで初めて取り出し、ついでに中の巻へ分解する。小説は `pages: []` で、`sections: [{"index", "length"}]`(`length` は全体の進み具合の重み)と `encoding`(テキストの文字コード)が付く。文字コードを判定できないテキストは 422。`next_volume` は同じ作品の一覧の並び(巻・話番号の順 → 番号なしは名前順)で次の巻 `{"id", "title", "volume_number", "offline"}` か null。同じ番号の重複は飛ばし、次の番号に重複があれば並びの 1 つ目。オフラインの巻も返す(リーダーが移動せず案内するため。#113) |
| `GET /api/volumes/{id}/sections/{index}` | 小説の区切り 1 つ `{"index", "html"}`。EPUB は無害化済み、テキストはエスケープしてから組み立てた HTML([[novel-reader]])。範囲外は 404、画像の巻は 422 |
| `GET /api/volumes/{id}/resources/{index}` | EPUB の中の画像(manifest の JPEG / PNG / GIF / WebP だけ。SVG は返さない)。`Content-Security-Policy: default-src 'none'; sandbox` 付き |
| `POST /api/volumes/{id}/analyze` | `{"id", "analyzed", "children": [{"id", "title", "page_count", "volume_number"}], "findings"}`。解析済みでも開き直す。分解の対象でない巻(フォルダ・子の巻)は `analyzed: false`。オフラインは 409 |
| `GET /api/works` | `{"works": [{"id", "title", "title_source", "title_manual", "author", "author_source", "author_manual", "aliases", "volume_count", "offline_count", "finished_count", "duplicate_slots", "max_volume", "missing_volumes", "missing_episodes", "pending_proposals", "merged", "reading_state", "kind", "publisher", "genres", "tags", "link_pending", "cover_volume_id"}], "total", "facets", "unassigned"}`。`cover_volume_id` は表紙にする巻(無ければ null。[[cover-thumbnails]])。一覧に見えている巻を持つ作品だけ(統合された側は出ない)。クエリ `q`(全文検索。空白区切りで AND)・`author` / `kind` / `genre` / `tag` / `publisher`(項目の値の一致。ジャンル・タグは `work_fields` の `genres` / `tags`)・`status`(`unread` / `reading` / `finished`)・`read_later=1`(後で読む)で絞る(組み合わせ可。未対応の `status` は 422)。`total` は絞る前の作品数、`facets` は絞る前の作品から作った絞り込みの候補 `{"author", "kind", "genre", "tag", "publisher"}`([[work-search]])。`root` は蔵書の切り替え(#85): その蔵書ルートに巻がある作品だけにし、巻数・欠け・読書状態・表紙もそのルートの巻で数える(`total`・`facets`・`unassigned` も同じ) |
| `GET /api/works/facets/{name}?root` | 著者・タグ・出版社などの一覧(#85 / #117)`{"name", "values": [{"value", "count", "starred"}]}`。`count` はその値を持つ作品の数、`starred` はスター(お気に入り)が付いているか、値の名前順。`name` は `author` / `kind` / `genre` / `tag` / `publisher`(他は 404) |
| `PUT /api/works/facets/{name}/star` | 著者・タグ・出版社などの値にスターを付ける / 外す(#117)。body `{"value": str, "starred": bool}` → `{"name", "value", "starred", "created_at"}`。表記の揺れは `search._facet_key()` で同じ値として扱い、付け直しても `created_at` は変えない。未対応の `name` は 404、空の `value` は 422 |
| `GET /api/stats?root` | 管理の統計タブ(#85)`{"root_id", "works", "volumes", "pages", "unassigned_volumes", "duplicate_candidates", "scan_issues", "cache": {"disk", "memory", "thumbnails"}}`。数え方は `spinebox/stats.py`。`scan_issues` とキャッシュは `root` で絞らない |
| `GET /api/settings` | 好みタブに出すサーバーの設定(読み取りだけ)`{"untranslated_terms", "cache_limit_bytes", "memory_cache_limit_bytes", "metadata_sources", "metadata_background"}` |
| `GET /api/works/{id}` / `PATCH` | 作品の詳細(`slots`・`unnumbered`・`numbers`・`proposals`・`merges`・`fields`(項目ごとの値・出所・手動・確信度・取得元)・`links`(取得元ごとの連携・候補・失敗)・`bookmarked`(作品の印。各巻にも `bookmarked`)付き)。PATCH は `{"title"?, "author"?, "kind"?, "publisher"?, "genres"?, "tags"?}` を手動で保存(空の作品名・不正な種別は 422)。統合で消えた id は 404 |
| `POST /api/works/{id}/fields/{field}/reset` | 手動の項目を自動に戻す(直す前の値、無ければ空にして公開 DB の値を入れ直す)。手動でない項目・直す前の名前が無い作品名は 409 |
| `POST /api/works/{id}/metadata/fetch` | その作品の公開 DB を取得して連携を更新し、詳細を返す(同期。取得元の失敗は 200 で `links[].failure` に出る)。取得元が無効なら 409 |
| `POST /api/works/{id}/links/{source}/choose` / `.../reset` | 確認待ちの候補を選ぶ `{"source_id"}`(null = 該当なし)/ 手動の選択を解除する。選んだ連携は手動で、再取得で変わらない。候補にない id は 422、取得元が違えば 404 |
| `PUT` / `DELETE /api/works/{id}/follow` | 作品をフォローする / 外す。作品のフォローの状態 `{"followed", "followed_at", "checked_at", "status", "failure", "releases"}` を返す(作品の詳細の `follow` も同じ形)。統合で消えた id は 404([[follow]]) |
| `GET /api/follows` | フォロー管理 `{"follows": [{"work_id", "title", "followed_at", "checked_at", "status", "failure", "unseen", "found"}], "state", "available", "background", "interval_hours"}`。`status` は `ok` / `unlinked` / `no_numbers` / `kind_mismatch` / `disabled` / `failed`。`available` は NDL が有効か |
| `GET /api/follows/releases?seen` | 続刊・欠けのある作品 `{"works": [{"work_id", "title", "checked_at", "status", "failure", "latest_released", "owned_max", "releases": [{"volume", "kind" (`new` / `gap`), "title", "volume_label", "released", "isbn", "url", "source", "found_at", "seen", "seen_at"}]}]}`。新しい発売日順(`spinebox/follows.py:292 _desc()`。年だけの発売日は同じ年の年月つきの後ろ)、蔵書に入った巻は出ない。`seen=true` で確認済みも含める |
| `POST /api/follows/releases/seen` / `POST /api/follows/check?wait` | 見つけた巻を確認済みにする `{"work_id", "volumes"?}` → `{"marked"}` / 続刊を調べる `{"work_ids"?}`(省くと全作品。フォローしていない作品は飛ばす)。既定は待ち行列に入れて 202 + 状態(`summary: {works, statuses}`。ジョブ `follow_check`。[[data-jobs]])、`wait` ならその場で動かし、ほかのジョブが動いていれば 409。NDL が無効なら 409 |
| `GET /api/works/metadata` / `POST /api/works/metadata/fetch?all&wait` | 全作品の取得の状態 `{"state", "sources", "background"}` / 取得が必要な作品(`all` で全作品)を待ち行列に入れて 202(`wait` なら同期)。取得元なしは 409、`wait` でほかのジョブが動いていれば 409。状態はジョブから作り、`current` は作品名・`job_id`・`status` 付き([[data-jobs]]) |
| `PATCH /api/volumes/{id}/metadata` / `POST .../metadata/{field}/reset` | 巻の書誌(`publisher` `label` `released` `isbn`)を手動で保存 / 自動に戻す。ISBN・発売日の形式違いは 422 |
| `POST /api/works/{id}/aliases` / `DELETE .../aliases/{alias_id}` | 別名を足す(手動)/ 外す(再計算で戻らない)。詳細を返す |
| `POST /api/works/{id}/merge` / `POST /api/works/assign` | 手動の統合 `{"other_id"}`(同じ作品は 409)/ 巻の付け替え `{"volume_ids", "work_id" \| null, "title"?}`(null は新しい作品へ分ける)。`{"work_id"}` を返す。どちらも手動の決定になる |
| `GET /api/works/proposals?status=open\|dismissed\|applied&q=` / `POST .../proposals/{id}/approve\|dismiss\|restore` | 統合の提案と、その承認・却下・戻す。`q`(#208)は組の両側の作品名・著者・別名への絞り込みで、空白区切りは AND、正規化は作品検索と同じ(`works.py _proposal_matches()`)。語は a・b を合わせて判定し、open / dismissed のみ(applied は無視)。確認の件数(`/api/review/counts`)は絞り込まない。無い id は 404、状態に合わない操作(確認待ちでない提案の承認・却下、却下されていない提案の `restore`)は 409。承認は手動の統合として扱う |
| `GET /api/review/counts` / `GET /api/review/links` | 確認の画面(#207)。件数 `{"links", "proposals", "duplicates", "total"}`(links は作品の数、duplicates は組の数。どれも画面の一覧に出るものだけ: 見えている作品・`list_proposals("open")`・両方が見えている重複の組)/ 公開 DB の候補が確認待ちの作品の詳細 `{"works": [<GET /api/works/{id} と同じ形>]}`(並びは作品の一覧と同じ)。選ぶのは既存の `.../links/{source}/choose` |
| `POST /api/works/refresh` | 名寄せの再計算(`{"works_created", "volumes_assigned", "merged", "proposals_open"}`)。起動時・スキャン・重複スキャンの終わり・巻の解析の後にも自動で走る。ほかのジョブが動いていれば 409 |
| `POST /api/duplicates/scan` | 見た目の指紋を作って重複候補を出す明示操作。既定は裏で動かして 202 + `{running, started_at, finished_at, done, total, summary, error}`。`?wait=true` は終わるまで待つ(ほかのジョブが動いていれば 409)。既定は待ち行列に入り、待ちの間も `running: true`(`status: "queued"`)。同じジョブが待ち・実行中なら新しく積まない。状態に `job_id`・`status` が付く([[data-jobs]]) |
| `GET /api/duplicates?status=open\|dismissed` | `{"scan": <上と同じ状態>, "candidates": [{"id", "rule" (`same_number` / `visual_only`), "matched_pages", "compared_pages", "evidence", "dismissed", "a", "b"}]}`。a・b は `{id, title, source_name, parent_title, page_count, offline, volume_number, progress}`。一覧に見えている巻どうしの組だけ。`status` が他なら 422 |
| `POST /api/duplicates/{id}/dismiss` / `restore` | 「重複ではない」と却下する / 戻す。`{"id", "dismissed"}`。無い id は 404。蔵書には触れない |
| `GET /api/volumes/{id}/thumbnail` | 巻の表紙(1 ページ目の縮小 JPEG)。`ETag` + `Cache-Control: private, no-cache`、一致で 304。取り出し・7-Zip が要る巻でまだ作っていなければ 404 `{"detail", "pending": true}`、小説・1 ページ目が画像でない巻は 404 `{"pending": false}`(どちらも `no-store`)。オフラインは 409。何も展開しない([[cover-thumbnails]]) |
| `GET /api/thumbnails` / `POST /api/thumbnails/build?wait` | 表紙の作成(明示操作)の状態 `{running, started_at, finished_at, done, total, summary: {total, made, existing, skipped}, error}` / 作品の表紙の巻のうち無いものを、内側アーカイブを取り出してでも作る(既定は待ち行列に入れて 202、`wait=true` は同期でほかのジョブが動いていれば 409。[[data-jobs]]) |
| `GET /api/bookmarks?sort=added\|title` | `{"sort", "items": [{"kind": "work", "created_at", "work": <GET /api/works の 1 件と同じ形>} \| {"kind": "volume", "created_at", "volume": {"id", "title", "parent_title", "format", "page_count", "offline", "progress", "volume_number", "work_id", "work_title"}}]}`。`added` は付けた新しい順、`title` は作品名の順。オフラインの巻も出る。他の `sort` は 422([[bookmarks]]) |
| `GET /api/page-marks?work_id=<巻 id>`、`POST` / `DELETE /api/page-marks` body `{"work_id", "page_number"}` | しおり(#136。[[manga-reader]])。漫画リーダー(manga-viewer)の `bookmarkApi` の形そのままで、`work_id` は**巻 id**(文字列の数字も受ける)、`page_number` は 1 始まり。GET は `{"success": true, "bookmarks": [{"page_number", "title"}]}`(ページ順、`title` は「ページN」を作って返す)、POST / DELETE は `{"success": true}`(既にある・無いものも 200)。失敗も `{"success": false, "error"}` で返す(ライブラリが `success` / `error` を読むため、FastAPI の `detail` にしない): 無い巻 404、範囲外のページ 422、1 巻 20 個を超える 409、形の違う本文・`work_id` 422。オフラインの巻でも読み書きできる |
| `PUT` / `DELETE /api/bookmarks/works/{id}`・`.../volumes/{id}` | 印を付ける / 外す。`{"work_id" か "volume_id", "bookmarked", "created_at"}`。付け直しても日時は変わらない。統合された作品の id は吸収先が対象。無い作品・巻は 404。オフラインの巻でも 200 |
| `GET /api/volumes/{id}/pages/{index}` | 画像のバイト列。`ETag` と `Cache-Control: private, max-age=3600`、`If-None-Match` 一致で 304 |
| `PUT /api/volumes/{id}/progress` | body `{"page": int, "finished": bool, "position"?: 0〜1}`(型は厳密。`"1"` は 422)。保存した値を返す。page はページ数が分かっていれば範囲外を 422。小説は page が区切りの番号、`position` が区切りの中の割合で、`progress` にも `position` が付く。画像の巻の `position` は捨てる |
| `POST /api/organize/plan` | body `{"volume_id"}` か `{"source_id"}`(どちらか一方)と、任意の `naming`(`"title"` 既定 / `"template"`: 出力先を命名テンプレートで決める)。その巻を含むソース全体の計画。整理の単位は見えている子を持たない巻(分解済みなら子の巻ごと)。問題があれば `state: "blocked"`・`op_id: null`(保存しない)。中の巻を調べていない内側アーカイブは `needs_analysis`(巻 id の配列)で返す。蔵書に書かない |
| `POST /api/organize/{op_id}/execute` / `.../undo` | 操作(`{"op_id", "state", "volumes": [{"volume_id", "output_rel_path", "new_volume_id", ...}], "warnings", "problems", "error", ...}`)。始められない・失敗(巻き戻し済み)は 409 で `{"detail", "op"}`。ジョブの実行中・待ち中も 409。実行時は `JobRunner.run_now` (`origin="button"`) で走り、タスクセンター(`GET /api/jobs`)の実行中・履歴(`organize_execute`, `organize_undo`)に記録される(#155) |
| `POST /api/organize/rename-plan` | body `{"work_id", "exclude_volume_ids"}`。作品の全巻をテンプレートの名前へ改名・移動する計画(`kind: "rename"`)。`items` に各巻の `status`(move / unchanged / excluded / conflict / skipped)・`reason`・`from_rel_path`・`output_rel_path`。動かす巻が無ければ `blocked`。実行・元に戻すは上と同じエンドポイント |
| `POST /api/organize/range-plan` | body `{"root_id", "path"?, "file_template"?, "folder_template"?, "exclude_volume_ids"?, "include_cbz"?}`。蔵書ルートの中の範囲(`path` は蔵書ルートからの相対。空ならルート全体、サブフォルダも対象)の巻を、ファイル名・フォルダーのテンプレートで改名・移動する計画(`kind: "range"`。両方空ならファイル名の整理だけ)。`include_cbz: true` なら 1 巻 = 1 CBZ でない元も CBZ 化する(#133。`items` の status に `cbz`、`conversion: true` と `cbz_sources` が付く)。`items` は `rename-plan` と同じ形で、整理した内容は各 item の `changes`。同じ名前の巻はファイル数が多い方を残し、少ない方は `skipped`(`superseded_by` に残した巻の id、`page_count` はファイル数。#143)。範囲の書き方が不正は 422、蔵書フォルダが無いのは 404、テンプレートが不正は 422。実行・元に戻すは `/api/organize/{op_id}/execute`・`undo`([[organize]]) |
| `GET /api/organize/range/options` | 整理画面の選択肢(読むだけ)。`{"roots": [{id, path, online}], "defaults": {file_template, folder_template}(設定の `name_template` を `{}` 記法へ読み替えたもの), "presets": [{label, file_template, folder_template}], "parameters": [{token, label}]}` |
| `POST /api/organize/range/example` | body `{"file_template", "folder_template", "path"}`。パスの例 `{"error": str \| null, "examples": [{label, path, reason}]}`。入力のたびに呼ぶので、テンプレートが不正でも 422 にせず `error` に理由を入れる |
| `GET /api/organize/range/folders?root_id&path` | 範囲を選ぶためのフォルダの直下のフォルダ名 `{root_id, path, parent, folders}`(読むだけ。作業場・退避と「.」で始まるものは出さない)。蔵書フォルダがオフラインは 422 |
| `GET /api/organize` | 実行を試みた操作の履歴(新しい順。計画だけのものは出さない)。各操作に `kind`(cbz / rename / range / intake)・`naming`・`template` |
| `GET /api/inbox` / `POST /api/inbox/sync` / `POST /api/inbox/preview` / `POST /api/inbox/approve` | 受け入れ([[inbox]])。`{"inboxes": [{path, library, problems, synced_at}], "items": [...]}`(sync は `volumes`・`offline_roots` も)。preview・approve の body は `{"items": [{"source_id", "work_id"?, "new_title"?, "numbers"?, "skip_volume_ids"?, "expect_outputs"?}]}`。approve は `{"results": [{source_id, ok, item, op?, error?, warning?}]}`(HTTP 200 でも項目ごとに失敗がありうる)。sync・approve はスキャンと同じロックで、実行中は 409。approve は `JobRunner.run_now` (`origin="button"`) で走り、タスクセンターの履歴(`inbox_approve`)に記録される(#155)。preview と一覧は何も書かない |
| `GET /api/jobs` / `GET /api/jobs/{id}` | データ管理([[data-jobs]])。`{"current": ジョブ \| null, "queue": [...], "history": [...]}`(履歴は新しい順・`limit` 既定 50)。ジョブは `{id, kind, label, origin (jobs / button / background), params, status (queued / running / succeeded / failed / cancelled), queued_at, started_at, finished_at, done, total, current, step, step_index, step_count, runs, summary, error, cancel_requested, cancellable}`(`step_index` / `step_count` は複数の段のジョブの今の段の番号と段の数。#207)。無い id は 404 |
| `GET /api/jobs/catalog` | 画面に並べるジョブ `{"kinds": [{kind, group, label, description, count, count_text, available, unavailable_reason, options}]}`。件数を数えられなければ null |
| `POST /api/jobs` / `POST /api/jobs/{id}/cancel` | body `{"kind", "params"?: {"all"?: bool}(`metadata`)/ {"work_ids"?: [int]}(`follow_check`)}` を待ち行列に入れて 202 + ジョブ(同じジョブが待ち・実行中ならそれ)。知らない種類・指定は 422、取得元が無効・受け入れフォルダが無いなど動かせない設定は 409。中止は待ちなら取り下げ、実行中は区切りで止める。終わったジョブ・裏の定期処理は 409 |

すべての応答に `Content-Security-Policy`(`spinebox/app.py:49 APP_CSP`。インラインのスクリプトと外部の読み込みを止める)と `X-Content-Type-Options: nosniff` を付ける。
画面に外部のスクリプト・画像・フォントを足すときは、この CSP を同じ PR で直す([[novel-reader]] の安全方針の 3 段目)。

`GET /healthz`(`/api` の外、フロントは使わない)は稼働確認用で、`{"ok": true, "commit": 短い SHA か null}`。
DB を開けなければ 503 + `ok: false`(異常を 200 で隠さない)。`commit` はプロセスが起動時に読んだコミット。使い方は [[server-setup]]。

エラーは `{"detail": "..."}`。404 = 巻・ページが無い(missing を含む)、**409 = オフライン**(`"offline": true` 付き。
再スキャン前でもルートが見えなければ 409)、422 = 読めない巻(未対応形式・壊れた・展開上限・キャッシュ上限超え)。

## レコードを消さない(offline / missing / enabled)

蔵書レコード・読書位置・ブックマーク([[bookmarks]])は DELETE しない(`CLAUDE.md`「蔵書を扱う不変条件」)。見えなくなったものは印を付けるだけ。

- **offline**: ルートが存在しない・読めない(外付けドライブ未接続など)。`library_roots.online = 0` と
  `sources.offline = 1` を立てるだけ。一覧には `offline: true` で出続け、再接続後のスキャンでそのまま戻る
- **missing**: ルートは見えるのにソース(ファイル・フォルダ)が無い、またはアーカイブを読み直したら巻が無くなった。
  一覧から外すが行は残す。同じ相対パスに戻れば同じ巻 id に戻り、読書位置もつながる
- **enabled = 0**: 設定の `library_roots` から外したルート。一覧から外すだけで、戻せば復活する
- **organized_op_id**: 整理処理で CBZ に置き換えた元の巻。一覧・閲覧から外すだけで、元に戻せば印が外れる([[organize]])
- 走査中に読めなかったサブフォルダ(`os.walk` の onerror)の配下は missing にしない。一時的な権限エラーで
  大量の巻を一覧から落とさないため

ファイルを移動・改名すると、今は「旧パスが missing・新パスが新しい巻」になり読書位置は引き継がれない。
同一性の判定(中身のハッシュ等)はフェーズ 2 の重複検出と一緒に考える。

## 落とし穴

- **スキャンは目次しか読まない。** `ZipFile.open` も 7z の取り出しも展開キャッシュも呼ばない(テスト `test_scan_never_extracts_inner_archives`、
  `test_scan_lists_7z_and_rar_without_extracting_anything`)。
  内側アーカイブのページ数を出したくなっても、スキャンで開いてはいけない(GOAL.md フェーズ 1 の完了条件)
- **開き直さない判定はサイズ・mtime・`SCAN_VERSION`。** 巻の切り出し方を変えたら `spinebox/scanner.py:54 SCAN_VERSION` を上げる。
  上げ忘れると既存 DB の巻は古い切り出し方のまま残る
- **巻の切り出しでは、非コンテンツディレクトリ(`__MACOSX`・`META-INF`・`.DS_Store`)の中の画像・内側アーカイブを巻にしない。**
  表紙画像だけを含むフォルダ(すべての画像が表紙)も巻にしない。拡張子に関わらず `META-INF/container.xml` を含むアーカイブは
  EPUB 小説として扱う(`.zip` に包まれた EPUB)。いずれも `spinebox/scanner.py:167 archive_volume_specs()` /
  `spinebox/scanner.py:505 _scan_archive()` / `spinebox/scanner.py:468 _scan_folder()` で行う
- **読めなかったアーカイブはサイズ・mtime を記録しない。** 次のスキャンで再挑戦させるため(7-Zip が見つからないときの 7z / rar も
  毎回試す。開く前に弾くので軽い。`sevenzip_path` を直して再スキャンすれば拾える)。既存の巻もそのまま残す
- `scan_issues` は再導出できる記録なので、ソースを読み直すたびに置き換える(DELETE してよい唯一の表)
- **想定外の例外はソース単位で捕まえ、止めない(#48)。** 想定した失敗(`ArchiveError` 系)は読む側が理由つきで捕まえるが、
  それ以外(壊れた形のアーカイブ・DB に入らない名前・書き込みの制約違反など)も、1 件で全体を止めると画面は「スキャンに失敗しました」だけになり、
  それまでの結果も返らない。ソース(ファイル・フォルダ)ごとに `Exception` を捕まえ、型とメッセージを `scan_issues` に、トレースバックをログ(`.err.log`)に残して次へ進む。
  プログラムの誤りを隠さないため、ログには必ず出す。**例外は `sqlite3.OperationalError`(ロック・ディスク満杯・SQL の誤り)だけ止める**:
  全ソースが同じ理由で失敗するだけで、記録そのものもできないため
- **失敗したソースの既存の巻・サイズ・mtime には触れない。** 古い巻は残り(missing にならず、読書位置も残る)、サイズ・mtime を更新しないので次のスキャンで再挑戦する。
  ソース名が DB に入らない(孤立サロゲートなど。`safe_text` でエスケープして表示する)ときは `source_id` が NULL の行になる。
  その行の `entry_name` は走査中のパス(ルート付き)で、スキャンのたびに作り直す。読めなかったサブフォルダ・祖先へ戻るリンクもここに入る
- **走査を最後まで終えられなかったら、見つからなかったソースを missing にしない。** `os.walk` 自体の例外や相対パスを作れなかったときは
  「見えなかっただけ」の可能性があるので `complete = False` にして missing 化を飛ばす(読めないサブフォルダ配下を missing にしないのと同じ理由)
- **`os.walk(followlinks=False)` は Windows のジャンクションを辿る。** 祖先を指すジャンクション(循環)は `_links_to_ancestor` で止める
  (辿ると同じ本が深さ違いの別ソースとして、パスが長すぎて読めなくなるまで登録される)。祖先を指さないジャンクションは今までどおり辿る
- 同名のエントリを持つ zip では、同じ名前の内側アーカイブを 1 つの巻にして残りは `scan_issues` に残す(`(source, container_path, entry_prefix)` が一意索引なので、
  そのまま INSERT すると `IntegrityError` でスキャンが止まっていた)。巻にできるものが無いアーカイブ(画像も内側のアーカイブも無い zip)も理由を残す
- **スキャンの巻の突き合わせ(`_replace_volumes`)は親を持たない巻だけを見る。** 子の巻まで見ると、スキャンが切り出さない子を毎回 missing にしてしまう
- **`volumes_locator`(一意索引)は `inner_path` を含む。** 子の巻は親と同じ (source, container_path, entry_prefix) を持ちうる(親の内側アーカイブ直下の単位は `inner_path = "[]"`、親は NULL)
- 相対パスは設定ファイルの場所が起点。サービスとして起動したときのカレントに左右されないため。
  例外は `organize_dir`(整理処理の作業場・退避フォルダ)で、相対なら各蔵書ルートが起点(蔵書と同じドライブに置くため)
- `[organize] name_template`(命名テンプレート)は読み込み時に検証し、`..`・絶対パス・ドライブ文字・知らない変数は `ConfigError` で起動させない
  (規則は [[organize]])
- スキャンは整理処理の置き場(既定 `<ルート>/.spinebox`)を走査しない。退避した元・作業場の CBZ を巻として拾わないため
- SQLite はリクエストごとに接続を開く(FastAPI の同期エンドポイントはスレッドプールで動くため)。WAL を使う

## 経緯

- #136 で漫画リーダーのしおりを足した([[manga-reader]])。マイグレーション 16(`page_marks`。巻 id とページ(0 始まり)で 1 行、題は持たない)。整理では移し、巻への分解では子へ写す。蔵書がオフラインでも行は消さない
- #117 で著者・タグ・出版社などの一覧の値ごとにスター(お気に入り)を付け外しできるようにした。マイグレーション 15(`facet_stars`。#83 のマイグレーション 14 の後ろ)。キーは `search._facet_key(name, value)` で正規化し、表記揺れのまとめと同じ単位で持つ。蔵書がオフラインになってもレコードは消さない
- #87 でフォロー([[follow]])を足した。マイグレーション 13(`follows` / `follow_releases`。#86 のマイグレーション 12 の後ろ)。設定 `[follow]`(`background` 既定は無効・`interval_hours`・`background_interval_seconds`・`response_ttl_days`)。
  続刊の確認は #83 のジョブ(`follow_check`)として 1 本ずつ動く(下)
- #83 でデータ管理のジョブを足した([[data-jobs]])。マイグレーション 14(`jobs`。ブックマークの 12・フォローの 13 の後ろ)。個別のロックと状態(`duplicate_lock` / `metadata_lock` / `thumb_lock` と `*_state`)を `JobRunner` にまとめ、`scan_lock` はジョブと整理処理の共有のロックとして残した。
  既存の API の応答形は保ち、状態に `job_id`・`status` を足した。変わったのは、裏で動かす API が実行中に 409 ではなく待ち行列に入ること、その場で結果を返す API(名寄せの取り直し・作品ページの取得を含む)がほかのジョブの実行中に 409 になること、失敗の `error` が `型: メッセージ` になったこと
- #66 で受け入れ([[inbox]])を足した。マイグレーション 10(`library_roots.inbox`)。設定 `inbox_dirs`(受け入れフォルダと書庫の組)。
  受け入れフォルダは蔵書と同じスキャナーで走査するが `inbox = 1` の行として分け、一覧・作品・検索・重複・結線の対象外にする
- #57 で小説(EPUB・テキスト)を足した([[novel-reader]])。マイグレーション 8(`volumes.format`・`reading_progress.position`)。
  小説のソースは 1 ファイルなので `sources.kind` は `archive`(CHECK を作り直さないため)。新しい種類のソースなので `SCAN_VERSION` は上げていない
- #56 で作品の全文検索と絞り込みを足した([[work-search]])。マイグレーション 9(`work_search_dirty` と、索引が古くなる変更で印を付けるトリガー)。
  索引の表 `work_search` はマイグレーションではなく起動時・一覧の前に `spinebox/search.py` が作る(SQLite の trigram の有無で形が変わるため)

- #48 でスキャンの想定外の例外を、ソース単位で記録して続けるようにした。サーバー機の実蔵書で画面が「スキャンに失敗しました」のまま止まった件で、
  サーバー機のトレースバックは未取得(利用者に依頼中)。合成データで確かめた再現は、同名の内側アーカイブを持つ zip(`IntegrityError`)と、
  孤立サロゲート・不正バイトを含むファイル名(`UnicodeEncodeError`)で、どちらも修正前は 500 でスキャン全体が止まった。
  応答に `issues`・`issues_total` を足し(契約は上の表)、画面で「読めなかったもの N 件」と理由を見せる。マイグレーションは足していない
- #82 であらすじを廃止した([[metadata-linking]] の「あらすじの廃止」)。PATCH の `synopsis` と設定 `english_synopsis` を外し(設定に残っていても無視して警告するだけ)、
  作品の詳細の `fields` にも出さない。マイグレーション 11 はあらすじの行を持つ作品に索引し直しの印を立てるだけで、行は消さない
- #74 で公開 DB の英語の語彙を日本語にして反映するようにした([[metadata-linking]] の「日本語での表示」)。設定 `[metadata]` に `untranslated_terms`(`hide` / `keep`)・`english_synopsis`(`fold` / `hide`。#82 で廃止)。
  `create_app` が起動時に `linking.configure()` してから反映するので、入っている英語の値は起動で日本語になる。マイグレーションは足していない(取得した元の値は `work_links.item` にあるので、反映し直すだけで済む)
- #50 で公開 DB の結線を足した([[metadata-linking]])。マイグレーション 7(`work_links` / `volume_fields`)。設定 `[metadata]` に `background`(定期取得、既定は無効)・`background_interval_seconds`・`recheck_days`・`retry_hours`。
  `SearchQuery.limit` を足した(NDL のみ。連携は 100 件引いて巻を揃える)。`merge_works` は種別・出版社・ジャンルなども手動優先で引き継ぎ、整理で巻が作り直されても書誌は `transfer_volume_fields` で引き継ぐ
- #43 で作品の名寄せを足した([[work-grouping]])。マイグレーション 6(`works` / `work_fields` / `work_aliases` / `work_volumes` / `work_merge_proposals`)。
  `_参考/サンプル本` の実起動で、スキャンの直後は 3 作品、重複スキャンの後にお気楽領主が 1 作品(8 巻・自動統合・確信度 0.989)になることを確かめた

- #38 で別スキャンの重複候補を足した([[duplicate-detection]])。マイグレーション 4(`volume_fingerprints`・`duplicate_candidates`)、依存に Pillow。
  `_参考/サンプル本` の実起動で、お気楽領主の 1・2 巻だけが候補に出ることを確かめた

- #30 で巻への分解と巻範囲の食い違いを足した([[volume-split]])。マイグレーション 2(`volumes` に親子・巻番号の候補・解析の版の列)。
  `SCAN_VERSION` を 2 にしたので、既存 DB は次のスキャンで全アーカイブの目次を読み直す(取り出しはしない)

- #31 で整理処理を足した([[organize]])。マイグレーション 3(`organize_ops` / `organize_steps` / `organize_volumes` と `volumes.organized_op_id`)。
  クラウドで作ったブランチは版 2 だったが、#30 が先にマージされたため 3 番へ付け替えた(未リリースなので版番号の付け替えは可)。
  `LISTED_VOLUMES_WHERE` に `organized_op_id IS NULL` を足し、一覧・スキャンの件数が整理済みの巻を数えないようにしている
- #19 で `/healthz` と常駐・LAN 公開の運用([[server-setup]])を足した。`host` の既定は 127.0.0.1 のまま、127.0.0.1 以外にしたときは起動時に注意を出す
- #14 で骨格を作った。zip / cbz だけを対象にした
- #16 で 7z / rar を足した(7-Zip CLI。[[archive-reading]])。巻を開く経路 `spinebox/library.py:370 _expand` は入れ子を再帰で開く。
  実データ(`_参考/サンプル本`)で、スキャンが 10 巻を一覧に出し、キャッシュは空のまま、全巻の全ページが 200 で返ることを確かめた
