---
type: component
summary: FastAPI のバックエンド(設定・DB・スキャン・閲覧 API)。フロントと共有する API 契約と、蔵書レコードを消さない offline / missing の扱い
updated: 2026-11-01
related: [overview, archive-reading, extraction-cache, server-setup]
---

# バックエンド

`python -m spinebox` で起動する単一プロセスの FastAPI アプリ。蔵書フォルダを走査して巻の候補を DB に登録し、
巻のページ一覧・ページ画像・読書位置を API で返す。`frontend/dist` があれば同じプロセスで静的配信する。

## 責務

- する: 設定の読み込み、スキャン(目次読みだけ)、巻を開いてページを返す、読書位置の保存
- しない: 蔵書への書き込み(読み取り専用。整理処理はフェーズ 2 以降で別に作る)、メタデータ取得、認証
- アーカイブの中身の扱いは [[archive-reading]]、内側アーカイブの取り出し先は [[extraction-cache]]

## 入口となるファイル

| パス | 役割 |
| --- | --- |
| `spinebox/app.py:41 create_app` | API の全体。例外 → HTTP ステータスの対応もここ |
| `spinebox/buildinfo.py:14 current_commit` | `/healthz` が出す稼働中コミット。git → `.git` 直読みの順で、解決できなければ None |
| `spinebox/scanner.py:198 _scan_archive` | アーカイブを巻の候補に切り出す。開き直さない条件・失敗時の扱い |
| `spinebox/scanner.py:144 _scan_root` | ルートの走査と、見つからなくなったソースの missing 化 |
| `spinebox/library.py:78 open` | 巻を開いてページを並べる(画像フォルダ / アーカイブ内フォルダ / 内側アーカイブ) |
| `spinebox/config.py:107 load_settings` | 設定ファイルの探し方と相対パスの起点 |
| `spinebox/db.py:16 MIGRATIONS` | スキーマ。`PRAGMA user_version` の連番で末尾に足していく |

## API 契約(フロントと共有)

フロント(`frontend/`)はこの形を前提にしている。変えるときはこのページと同じ PR で直す。

| メソッドとパス | 返すもの |
| --- | --- |
| `POST /api/scan` | `{"volumes": 件数, "offline_roots": [パス...]}`。件数は一覧に出る巻の数(オフラインの巻を含む)。スキャン中の再実行は 409 |
| `GET /api/volumes` | `[{"id", "title", "source_name", "page_count", "offline", "progress"}]`。`page_count` は内側アーカイブの巻だと開くまで null。`progress` は `{"page", "finished"}` か null |
| `GET /api/volumes/{id}` | `{"id", "title", "page_count", "pages": [{"index", "name"}], "progress"}`。内側アーカイブの巻はここで初めて取り出す |
| `GET /api/volumes/{id}/pages/{index}` | 画像のバイト列。`ETag` と `Cache-Control: private, max-age=3600`、`If-None-Match` 一致で 304 |
| `PUT /api/volumes/{id}/progress` | body `{"page": int, "finished": bool}`(型は厳密。`"1"` は 422)。保存した値を返す。page はページ数が分かっていれば範囲外を 422 |

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
- 走査中に読めなかったサブフォルダ(`os.walk` の onerror)の配下は missing にしない。一時的な権限エラーで
  大量の巻を一覧から落とさないため

ファイルを移動・改名すると、今は「旧パスが missing・新パスが新しい巻」になり読書位置は引き継がれない。
同一性の判定(中身のハッシュ等)はフェーズ 2 の重複検出と一緒に考える。

## 落とし穴

- **スキャンは目次しか読まない。** `ZipFile.open` も展開キャッシュも呼ばない(テスト `test_scan_never_extracts_inner_archives`)。
  内側アーカイブのページ数を出したくなっても、スキャンで開いてはいけない(GOAL.md フェーズ 1 の完了条件)
- **開き直さない判定はサイズ・mtime・`SCAN_VERSION`。** 巻の切り出し方を変えたら `spinebox/scanner.py:30 SCAN_VERSION` を上げる。
  上げ忘れると既存 DB の巻は古い切り出し方のまま残る
- **読めなかったアーカイブはサイズ・mtime を記録しない。** 次のスキャンで再挑戦させるため(未対応の 7z / rar も毎回試す。
  開く前に拡張子で弾くので軽い)。既存の巻もそのまま残す
- `scan_issues` は再導出できる記録なので、ソースを読み直すたびに置き換える(DELETE してよい唯一の表)
- 相対パスは設定ファイルの場所が起点。サービスとして起動したときのカレントに左右されないため
- SQLite はリクエストごとに接続を開く(FastAPI の同期エンドポイントはスレッドプールで動くため)。WAL を使う

## 経緯

- #19 で `/healthz` と常駐・LAN 公開の運用([[server-setup]])を足した。`host` の既定は 127.0.0.1 のまま、127.0.0.1 以外にしたときは起動時に注意を出す

- #14 で骨格を作った。zip / cbz の内側までを対象にし、7z / rar は後続の [Local] Issue で `register_reader()` から差し込む
