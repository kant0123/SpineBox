---
type: component
summary: FastAPI のバックエンド(設定・DB・スキャン・閲覧 API)。フロントと共有する API 契約と、蔵書レコードを消さない offline / missing の扱い
updated: 2026-11-02
related: [metadata-linking, overview, archive-reading, extraction-cache, server-setup, volume-split, organize, duplicate-detection, work-grouping, metadata-clients, metadata-sources]
---

# バックエンド

`python -m spinebox` で起動する単一プロセスの FastAPI アプリ。蔵書フォルダを走査して巻の候補を DB に登録し、
巻のページ一覧・ページ画像・読書位置を API で返す。`frontend/dist` があれば同じプロセスで静的配信する。

## 責務

- する: 設定の読み込み、スキャン(目次読みだけ)、巻を開いてページを返す、読書位置の保存、
  内側アーカイブの巻の分解と巻範囲の食い違いの検出([[volume-split]])、別スキャンの重複候補の検出(明示操作。[[duplicate-detection]])、
  巻の名前から作る作品の名寄せ(別名・著者・統合の根拠・欠け巻。[[work-grouping]])
- しない: 蔵書への書き込み(読み取り専用。例外は整理処理 [[organize]] だけで、ユーザーの明示操作でだけ動く)、認証。外部メタデータの取得クライアント(`spinebox/metadata/`、[[metadata-clients]])は、作品・巻へ `spinebox/linking.py` で結線する([[metadata-linking]])。ネットワークに触るのは明示操作と設定した定期取得だけで、スキャン・閲覧・編集は通信しない
- アーカイブの中身の扱いは [[archive-reading]]、内側アーカイブの取り出し先は [[extraction-cache]]

## 入口となるファイル

| パス | 役割 |
| --- | --- |
| `spinebox/app.py:143 create_app` | API の全体。例外 → HTTP ステータスの対応もここ |
| `spinebox/__main__.py:15 main` | 起動。`--print-bind` は起動せず `host port` を出す(常駐ラッパーが孤児の検出に使う。[[server-setup]]) |
| `spinebox/buildinfo.py:14 current_commit` | `/healthz` が出す稼働中コミット。git → `.git` 直読みの順で、解決できなければ None |
| `spinebox/scanner.py:357 _scan_archive` | アーカイブを巻の候補に切り出す。開き直さない条件・失敗時の扱い |
| `spinebox/scanner.py:243 _scan_root` | ルートの走査と、見つからなくなったソースの missing 化。ソース単位の例外の捕まえ方もここ |
| `spinebox/scanner.py:215 _source_failed` | 想定外の例外・読めなかったソースの記録(`scan_issues`)。止めてよい例外は `_unexpected` |
| `spinebox/scanner.py:444 collect_issues` | `POST /api/scan` が返す「読めなかったもの」の組み立て |
| `spinebox/library.py:120 open` | 巻を開いてページを並べる(画像フォルダ / アーカイブ内フォルダ / 内側アーカイブ / 分解で作った子の巻) |
| `spinebox/library.py:344 analyze` | 巻への分解。登録と食い違いは `spinebox/analysis.py`、名前の巻番号の候補は `spinebox/naming.py:92 parse_number`([[volume-split]]) |
| `spinebox/duplicates.py:283 DuplicateScanner` | 重複候補。指紋は `spinebox/fingerprint.py:32 image_hash`、判定は `spinebox/duplicates.py:154 judge`、API は `spinebox/app.py` の `/api/duplicates`([[duplicate-detection]]) |
| `spinebox/linking.py:299 fetch_work()` / `spinebox/linking.py:601 apply_work()` | 公開 DB の結線([[metadata-linking]])。取得して連携を保存する `fetch_work`(唯一ネットワークに触る)と、連携から項目・別名・巻の書誌を導く `apply_work`(DB だけ) |
| `spinebox/works.py:392 sync` | 作品の名寄せ。割り当て・別名・統合の提案と自動統合、手動編集・付け替え・提案の承認/却下も同じファイル。API は `spinebox/app.py` の `/api/works`([[work-grouping]]) |
| `spinebox/scanner.py:45 LISTED_VOLUMES_WHERE` | 一覧に出る巻の条件。スキャンの返す件数と `GET /api/volumes` で共用 |
| `spinebox/config.py:237 load_settings` | 設定ファイルの探し方と相対パスの起点。`[metadata]`(外部メタデータ取得)は `spinebox/config.py:38 MetadataSettings` と `_metadata()` |
| `spinebox/db.py:16 MIGRATIONS` | スキーマ。`PRAGMA user_version` の連番で末尾に足していく |

## API 契約(フロントと共有)

フロント(`frontend/`)はこの形を前提にしている。変えるときはこのページと同じ PR で直す。

| メソッドとパス | 返すもの |
| --- | --- |
| `POST /api/scan` | `{"volumes": 件数, "offline_roots": [パス...], "issues": [{"path", "container_path", "entry_name", "reason"}], "issues_total": 件数}`。件数は一覧に出る巻の数(オフラインの巻を含む。分解済みの親は数えず子を数える)。`issues` は「読めなかったもの」(`scan_issues` の現在の内容。最大 200 件で、切った分も `issues_total` には数える)。スキャン中の再実行は 409。DB が使えないなど続行できない失敗は 500 + `{"detail": "スキャンに失敗しました: 型: メッセージ"}`(トレースバックはログ) |
| `GET /api/volumes` | `[{"id", "title", "source_name", "page_count", "offline", "progress", "parent_id", "parent_title", "volume_number", "findings", "can_analyze"}]`。`page_count` は内側アーカイブの巻だと開くまで null。`progress` は `{"page", "finished"}` か null。分解済みの親は出ず、子が `parent_id` 付きで親の位置に並ぶ。`volume_number` は名前から読んだ候補 `{"kind", "start", "end", "evidence"}` か null。`findings` は巻範囲の食い違い([[volume-split]])。`can_analyze` は未解析の内側アーカイブの巻。`duplicate_count` は未却下の重複候補の数(#38)。`work_id` は割り当てた作品(統合済みなら統合先。#43) |
| `GET /api/volumes/{id}` | `{"id", "title", "page_count", "pages": [{"index", "name"}], "progress", "parent_id", "volume_number", "findings"}`。内側アーカイブの巻はここで初めて取り出し、ついでに中の巻へ分解する |
| `POST /api/volumes/{id}/analyze` | `{"id", "analyzed", "children": [{"id", "title", "page_count", "volume_number"}], "findings"}`。解析済みでも開き直す。分解の対象でない巻(フォルダ・子の巻)は `analyzed: false`。オフラインは 409 |
| `GET /api/works` | `{"works": [{"id", "title", "title_source", "title_manual", "author", "author_source", "author_manual", "aliases", "volume_count", "offline_count", "finished_count", "duplicate_slots", "max_volume", "missing_volumes", "missing_episodes", "pending_proposals", "merged"}], "unassigned"}`。一覧に見えている巻を持つ作品だけ(統合された側は出ない) |
| `GET /api/works/{id}` / `PATCH` | 作品の詳細(`slots`・`unnumbered`・`numbers`・`proposals`・`merges`・`fields`(項目ごとの値・出所・手動・確信度・取得元)・`links`(取得元ごとの連携・候補・失敗)付き)。PATCH は `{"title"?, "author"?, "kind"?, "publisher"?, "synopsis"?, "genres"?, "tags"?}` を手動で保存(空の作品名・不正な種別は 422)。統合で消えた id は 404 |
| `POST /api/works/{id}/fields/{field}/reset` | 手動の項目を自動に戻す(直す前の値、無ければ空にして公開 DB の値を入れ直す)。手動でない項目・直す前の名前が無い作品名は 409 |
| `POST /api/works/{id}/metadata/fetch` | その作品の公開 DB を取得して連携を更新し、詳細を返す(同期。取得元の失敗は 200 で `links[].failure` に出る)。取得元が無効なら 409 |
| `POST /api/works/{id}/links/{source}/choose` / `.../reset` | 確認待ちの候補を選ぶ `{"source_id"}`(null = 該当なし)/ 手動の選択を解除する。選んだ連携は手動で、再取得で変わらない。候補にない id は 422、取得元が違えば 404 |
| `GET /api/works/metadata` / `POST /api/works/metadata/fetch?all&wait` | 全作品の取得の状態 `{"state", "sources", "background"}` / 取得が必要な作品(`all` で全作品)を裏で取得して 202(`wait` なら同期)。実行中・取得元なしは 409 |
| `PATCH /api/volumes/{id}/metadata` / `POST .../metadata/{field}/reset` | 巻の書誌(`publisher` `label` `released` `isbn`)を手動で保存 / 自動に戻す。ISBN・発売日の形式違いは 422 |
| `POST /api/works/{id}/aliases` / `DELETE .../aliases/{alias_id}` | 別名を足す(手動)/ 外す(再計算で戻らない)。詳細を返す |
| `POST /api/works/{id}/merge` / `POST /api/works/assign` | 手動の統合 `{"other_id"}`(同じ作品は 409)/ 巻の付け替え `{"volume_ids", "work_id" \| null, "title"?}`(null は新しい作品へ分ける)。`{"work_id"}` を返す。どちらも手動の決定になる |
| `GET /api/works/proposals?status=open\|dismissed\|applied` / `POST .../proposals/{id}/approve\|dismiss\|restore` | 統合の提案と、その承認・却下・戻す。無い id は 404、状態に合わない操作(確認待ちでない提案の承認・却下、却下されていない提案の `restore`)は 409。承認は手動の統合として扱う |
| `POST /api/works/refresh` | 名寄せの再計算(`{"works_created", "volumes_assigned", "merged", "proposals_open"}`)。起動時・スキャン・重複スキャンの終わり・巻の解析の後にも自動で走る |
| `POST /api/duplicates/scan` | 見た目の指紋を作って重複候補を出す明示操作。既定は裏で動かして 202 + `{running, started_at, finished_at, done, total, summary, error}`。`?wait=true` は終わるまで待つ。実行中・ライブラリのスキャン中は 409 |
| `GET /api/duplicates?status=open\|dismissed` | `{"scan": <上と同じ状態>, "candidates": [{"id", "rule" (`same_number` / `visual_only`), "matched_pages", "compared_pages", "evidence", "dismissed", "a", "b"}]}`。a・b は `{id, title, source_name, parent_title, page_count, offline, volume_number, progress}`。一覧に見えている巻どうしの組だけ。`status` が他なら 422 |
| `POST /api/duplicates/{id}/dismiss` / `restore` | 「重複ではない」と却下する / 戻す。`{"id", "dismissed"}`。無い id は 404。蔵書には触れない |
| `GET /api/volumes/{id}/pages/{index}` | 画像のバイト列。`ETag` と `Cache-Control: private, max-age=3600`、`If-None-Match` 一致で 304 |
| `PUT /api/volumes/{id}/progress` | body `{"page": int, "finished": bool}`(型は厳密。`"1"` は 422)。保存した値を返す。page はページ数が分かっていれば範囲外を 422 |
| `POST /api/organize/plan` | body `{"volume_id"}` か `{"source_id"}`(どちらか一方)と、任意の `naming`(`"title"` 既定 / `"template"`: 出力先を命名テンプレートで決める)。その巻を含むソース全体の計画。整理の単位は見えている子を持たない巻(分解済みなら子の巻ごと)。問題があれば `state: "blocked"`・`op_id: null`(保存しない)。中の巻を調べていない内側アーカイブは `needs_analysis`(巻 id の配列)で返す。蔵書に書かない |
| `POST /api/organize/{op_id}/execute` / `.../undo` | 操作(`{"op_id", "state", "volumes": [{"volume_id", "output_rel_path", "new_volume_id", ...}], "warnings", "problems", "error", ...}`)。始められない・失敗(巻き戻し済み)は 409 で `{"detail", "op"}`。スキャン中も 409 |
| `POST /api/organize/rename-plan` | body `{"work_id", "exclude_volume_ids"}`。作品の全巻をテンプレートの名前へ改名・移動する計画(`kind: "rename"`)。`items` に各巻の `status`(move / unchanged / excluded / conflict / skipped)・`reason`・`from_rel_path`・`output_rel_path`。動かす巻が無ければ `blocked`。実行・元に戻すは上と同じエンドポイント |
| `GET /api/organize` | 実行を試みた操作の履歴(新しい順。計画だけのものは出さない)。各操作に `kind`(cbz / rename)・`naming`・`template` |

`GET /healthz`(`/api` の外、フロントは使わない)は稼働確認用で、`{"ok": true, "commit": 短い SHA か null}`。
DB を開けなければ 503 + `ok: false`(異常を 200 で隠さない)。`commit` はプロセスが起動時に読んだコミット。使い方は [[server-setup]]。

エラーは `{"detail": "..."}`。404 = 巻・ページが無い(missing を含む)、**409 = オフライン**(`"offline": true` 付き。
再スキャン前でもルートが見えなければ 409)、422 = 読めない巻(未対応形式・壊れた・展開上限・キャッシュ上限超え)。

## レコードを消さない(offline / missing / enabled)

蔵書レコードと読書位置は DELETE しない(`CLAUDE.md`「蔵書を扱う不変条件」)。見えなくなったものは印を付けるだけ。

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
- **開き直さない判定はサイズ・mtime・`SCAN_VERSION`。** 巻の切り出し方を変えたら `spinebox/scanner.py:38 SCAN_VERSION` を上げる。
  上げ忘れると既存 DB の巻は古い切り出し方のまま残る
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

- #48 でスキャンの想定外の例外を、ソース単位で記録して続けるようにした。サーバー機の実蔵書で画面が「スキャンに失敗しました」のまま止まった件で、
  サーバー機のトレースバックは未取得(利用者に依頼中)。合成データで確かめた再現は、同名の内側アーカイブを持つ zip(`IntegrityError`)と、
  孤立サロゲート・不正バイトを含むファイル名(`UnicodeEncodeError`)で、どちらも修正前は 500 でスキャン全体が止まった。
  応答に `issues`・`issues_total` を足し(契約は上の表)、画面で「読めなかったもの N 件」と理由を見せる。マイグレーションは足していない
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
- #16 で 7z / rar を足した(7-Zip CLI。[[archive-reading]])。巻を開く経路 `spinebox/library.py:325 _expand` は入れ子を再帰で開く。
  実データ(`_参考/サンプル本`)で、スキャンが 10 巻を一覧に出し、キャッシュは空のまま、全巻の全ページが 200 で返ることを確かめた
