---
type: component
summary: 公開 DB クライアント(spinebox/metadata/)の入口。検索の入出力の型、失敗の表現、生応答の保存(metadata_responses)、取得元ごとのレート制限、確からしさの出し方、結線が使う入口。結線そのものは [[metadata-linking]]
updated: 2026-11-09
related: [metadata-sources, metadata-linking, overview, backend, work-search, follow]
---

# 公開 DB クライアント(`spinebox/metadata/`)

国立国会図書館サーチ・AniList・MangaUpdates を「作品名(+著者・巻番号)で検索 → 候補の列」で引き、**正規化した項目と確からしさ(数値と根拠)**を返す。#44(GOAL.md フェーズ 3 の取得元クライアント)。
取得元ごとの癖・制限・採否の理由は [[metadata-sources]]。

## 責務

- する: 検索、応答の正規化、候補ごとの確からしさ、生応答の保存と使い回し、レート制限、失敗を例外にせず結果で返す
- **しない: DB の作品・巻への結線、取得元どうしの優先順位マージ、手動編集の保護、自動採用の判断、API のエンドポイント。** この結果を見る側が行う(#50 で実装: [[metadata-linking]])。
  結線するときの不変条件は CLAUDE.md のとおり(手で編集した項目を外部ソースで上書きしない・外部は空欄だけを埋める・取得失敗でスキャンと閲覧を止めない)
- 取得はベストエフォート。このパッケージはスキャン・閲覧の経路から呼ばない(呼ぶ側が、失敗を `SearchOutcome` で受けて続行する)

## 結線が使う入口

```python
service = MetadataService(settings.metadata, conn)       # conn はリクエストごとの接続でよい。fetcher はプロセス共有
outcomes = service.search(SearchQuery("作品名", author=None, volume=None, kind=None, isbn=None, limit=None))
# → 有効な取得元ごとの SearchOutcome(例外は投げない)
```

| パス | 役割 |
| --- | --- |
| `spinebox/metadata/service.py:57 MetadataService` | 入口。`search(query, sources=None)`。有効な取得元は設定 `[metadata] sources` の順に直列で引く。取得元の追加は `SOURCE_CLASSES` への登録 |
| `spinebox/metadata/model.py:29 SearchQuery` | 入力。`title` だけ必須。`author` `volume` `kind` は確からしさの手がかり、`isbn` は NDL で巻の書誌を直接引く |
| `spinebox/metadata/model.py:44 MetadataItem` | 正規化した項目(作品名・副題・読み・ローマ字名・別名・著者・巻・巻の表記・全巻数・出版社・レーベル・発売日・ISBN・種別・ジャンル・タグ・出典 URL。あらすじは読み取らない([[metadata-linking]] の「あらすじの廃止」))。取れなかった項目は None / 空。`scope` が `volume`(NDL: 巻ごとの書誌)か `work`(AniList / MangaUpdates: 作品全体) |
| `spinebox/metadata/model.py:82 Match` | 確からしさ。`score`(0〜1)、観点ごとの `components`、根拠 `reasons`(`title:exact` `author:match` `volume:mismatch` など) |
| `spinebox/metadata/model.py:96 Candidate` | 項目 + `Match` + `response_key`(`metadata_responses.request_key`。生応答を引き直す鍵) |
| `spinebox/metadata/model.py:123 SearchOutcome` | 取得元ごとの結果。`ok`・`candidates`(確からしさの高い順)・`failure`・`responses`(使った生応答の出所と `from_cache` / `stale`)・`notes` |
| `spinebox/metadata/base.py:30 MetadataSource` | 取得元の共通基底。`search()` が例外を畳んで `SearchOutcome` にする。取得元は `_search()` だけ書く |
| `spinebox/metadata/base.py:79 fetch` | 保存済みの応答つきの取得(下記) |
| `spinebox/metadata/ndl.py:58 parse_opensearch` / `spinebox/metadata/anilist.py:58 parse_search` / `spinebox/metadata/mangaupdates.py:75 parse_search`・`spinebox/metadata/mangaupdates.py:88 parse_detail` | 応答 → 項目。不正な応答は `ParseError` |
| `spinebox/metadata/match.py:118 score_item` | 確からしさの計算 |
| `spinebox/metadata/http.py:152 HttpFetcher` | タイムアウト・再試行・User-Agent・取得元ごとの関門(`spinebox/metadata/http.py:86 Gate`) |
| `spinebox/metadata/store.py:32 ResponseStore` | 生応答の保存。`get_by_key(source, request_key)` で候補の `response_key` から引ける |
| `spinebox/db.py:40 MIGRATIONS` の 5 番 | `metadata_responses` |
| `scripts/metadata_probe.py` | 実通信の確認と、記録(`tests/fixtures/metadata/`)の採取。手動実行のみ、CI では動かさない |

## 失敗の表現

`SearchOutcome.failure` は `Failure(kind, message, status, retry_after)`。`kind`: `timeout` `connect` `http_status` `rate_limited` `invalid_response` `too_large` `disabled` `internal`。

- **失敗は成功した 0 件と別物。** 見つからなかったときは `ok` で `candidates` が空。通信・応答の不備は `failure`。結線側は「失敗 = 何も更新しない」にできる
- `message` には URL・クエリ・応答本文を入れない(API キーがクエリに入る取得元を足しても漏れない)
- 例外を投げない境界は 3 重: HTTP 層(`FetchError` に畳む)、`MetadataSource.search`(`ParseError`・想定外の例外を `invalid_response` / `internal` に)、`MetadataService.search`(有効でない取得元は `disabled`)
- MangaUpdates の詳細だけ失敗したときは `ok` のまま、その候補は検索結果の項目で返し、理由を `notes` に残す

## 生応答の保存(`metadata_responses`)

- 鍵は `(source, request_key)`。`request_key` は要求(メソッド・URL・クエリ・JSON 本文)の SHA-256 で、**秘匿情報は含めない**。行には秘匿を除いた要求の記述(JSON)・本文のバイト列そのまま・取得日時を持つ。同じ鍵は上書き
- 同じ要求は有効期限内(設定 `cache_ttl_days`、既定 30 日)なら保存済みを使い、ネットワークに出ない(`SearchOutcome.from_cache`)
- **有効期限が切れた行も消さない。取得に失敗したとき、その古い応答を代わりに使う**(`ResponseRef.stale`、`notes` に理由)。取得元が落ちていても検索が返るようにするため
- 保存するのは**解釈できて 200 だった応答だけ**。失敗・解釈できない応答は保存しない。保存済みが解釈できなくなっていたら捨てて取り直す。保存に失敗(DB ロックなど)しても検索結果は返す
- 検索 1 回が複数の要求(MangaUpdates は検索 + 詳細)に分かれるので、`ResponseRef` は要求ごとに出る。`Candidate.response_key` はその候補の項目を取り出した要求
- **DB テーブルにしてファイルにしなかった理由**: GOAL.md が「生レスポンスと、どの項目をどのソースから埋めたかを DB に残す」としており、後続の結線(出所の記録)と同じ DB で引ける。
  ファイル案は、ハッシュ名の管理と掃除を別に作る必要があり、蔵書の DB と別にバックアップ・移行する手間も増える。作品・巻には結び付けない(外部キー無し)ので、結線の仕組みが決まる前でも入れられた
- 接続はリクエストごとに開く前提(アプリの `get_conn`)。`ResponseStore` の内部ロックは同じインスタンスの中だけ
- **`HttpFetcher`(関門・接続)はプロセスで 1 つを共有する**(`spinebox/metadata/service.py:37 shared_fetcher`)。`MetadataService` をリクエストごとに作っても最小間隔と 429 の停止が持ち越される。
  `fetcher` を渡さなければ共有のものを使い、`close()` は閉じない。`transport` を渡した(テストの)ときだけ専用に作って `close()` で閉じる。渡された `fetcher` も閉じない(#44 の agy レビューで指摘された誤り)

## レート制限(`HttpFetcher` / `Gate`)

- 取得元ごとに 1 つの関門。**要求を直列にし、直前の要求が終わってから最小間隔を空ける**(設定 `[metadata.min_interval_seconds]`。既定は NDL 1.0・AniList 2.0・MangaUpdates 1.0 秒、根拠は [[metadata-sources]])。
  時計と sleep は差し替えられる(テストは `tests/metadata_fakes.py FakeClock`)
- **429 は再試行しない。** `Retry-After`(秒・HTTP 日付。なければ 60 秒、上限 15 分)の間は、その取得元に要求を出さない。残りが 10 秒を超えるなら待たずに `rate_limited` で失敗させ(呼び出しを塞がない)、
  10 秒以内なら待って送る。ほかの取得元には影響しない
- 5xx・タイムアウト・接続不可は `max_retries`(既定 2)まで、1 秒・2 秒のバックオフで再試行。4xx・リダイレクトは再試行しない。応答は 8 MiB まで
- User-Agent は `SpineBox/0.1 (personal library manager; non-commercial)`。設定 `contact` を付けられる

## 確からしさ(`score_item`)

題名 0.55・著者 0.20・巻 0.15・種別 0.10 の重みで、**比べられた観点だけ**の重み付き平均(0〜1)。

- 題名: 作品名・ローマ字名・別名・(NDL の読みをローマ字にしたもの)のうち最良。完全一致 1.0、部分一致(副題つきなど)0.6〜0.9、それ以外は類似度 × 0.8。ローマ字は長音・マクロンの揺れを畳む(`ou`/`o`、`ii`/`i`)
- 著者: 日本語の著者名と MangaUpdates のローマ字名のように**表記体系が違うと比べない**(`author:not_comparable`。点数に入れず根拠に残す)
- 巻: NDL(巻ごとの書誌)だけ。一致 1.0、食い違い 0.0、**巻が不明(映画ノベライズなど)は 0.5**。作品単位の項目は比べない
- 種別: 項目が不明なら比べない
- 自動採用の閾値はこのパッケージでは決めない(結線側が決める: [[metadata-linking]])。NDL は同じ作品名でも漫画と小説が別の巻として並ぶので、種別と巻の根拠を必ず見る

## 秘匿情報

今の 3 取得元はキー不要。将来の取得元のために、設定 `[metadata.keys]`(設定ファイルはリポジトリ外。`MetadataSettings.keys` は repr に出ない)と、
`SourceRequest.secret_params` / `secret_headers`(送信にだけ使い、鍵・保存する記述・repr に含まない)を用意した。
**httpx / httpcore は URL をクエリごと INFO・DEBUG で出す**ので、`spinebox/metadata/http.py` が両ロガーを WARNING に固定している(外すとキーがログに出る。テストで確認)。

## 踏むと壊れること

- 取得元の追加は `SOURCE_CLASSES` と `[metadata] sources` の両方。設定に未知の名前があれば警告して飛ばす(`MetadataService.unknown`)
- マイグレーションの版数は #43(作品の名寄せ)と並行で足される。マージ時に衝突したら自分の要素を main の末尾の後ろへ付け替える(未リリースなので可)
- `tests/fixtures/metadata/` は公開 API の応答そのまま(手で直さない)。取り直すときは `scripts/metadata_probe.py --record`。AniList は利用条件で大量の写しを禁じているので、検索数件分に留める

## 関連

- [[metadata-sources]] — 取得元ごとの癖・制限・利用条件・採否
- [[backend]] — 設定とマイグレーション
- [[overview]] — 全体像
- [[work-search]] — 結線が種別・ジャンル・タグを `work_fields` の `kind` / `genres` / `tags`(配列は JSON)に足すと、そのまま絞り込みと全文検索に効く
