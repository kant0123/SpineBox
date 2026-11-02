---
type: concept
summary: 作品カードの表紙(#75)。最小の巻番号の巻の 1 ページ目を縮小してハッシュ名で置く。一覧からは取り出し・7-Zip が要らない巻だけその場で作り、入れ子の巻は開いた後か明示操作(管理のデータ管理タブの「表紙のみ補完」)で作る理由と、却下した代替案
updated: 2026-11-03
related: [overview, frontend, backend, extraction-cache, work-grouping, volume-split, duplicate-detection, data-jobs]
---

# 表紙のサムネイル

作品の一覧(`#/works`)のカードに出す表紙。ネットからは取らず(GOAL.md「作らないもの」)、巻の 1 ページ目から作る。
入口は `spinebox/thumbs.py:63 Thumbnails`、API は `spinebox/app.py:862 get_thumbnail()`(`GET /api/volumes/{id}/thumbnail`)と
`spinebox/app.py:883 start_thumbnail_build()`(`POST /api/thumbnails/build`)、画面は `frontend/src/works/WorkCover.tsx:9 WorkCover()`。

## 内容

- **表紙にする巻は `spinebox/works.py:1081 cover_volume_id()`。** 作品の見えている巻のうち、画像の巻(小説・オフライン・0 ページを除く)で
  巻番号が最小のもの。番号の無い巻は後ろ、同じ番号(重複)は巻の名前の自然順。`GET /api/works` の各作品に `cover_volume_id` で載る
- **表紙は巻の 1 ページ目。** ページの並びの規則(`cover.*` を先頭。`spinebox/pages.py`)をそのまま使う。Pillow で 240x360 に収めた JPEG
  (`spinebox/thumbs.py:38 make_thumbnail()`。読めない・巨大な画像は作らない。画素数の上限は指紋と同じ `MAX_PIXELS`)
- **置き場は `data_dir/thumbs/`。** 展開キャッシュと同じ `ExtractionCache`(キーの sha256 の名前・LRU)を別のディレクトリと上限
  (`thumb_cache_limit_bytes`、既定 256 MiB)で使う。キーは作り方の版・大きさ・元ファイルのパス・サイズ・mtime・巻の位置
  (`container_path` / `inner_path` / `entry_prefix`)。元が変われば別のキーになり、古いものは LRU で消える。消えてもよいもの
- **一覧から呼ばれる `GET /api/volumes/{id}/thumbnail` は、取り出し・7-Zip の起動が要らない巻だけその場で作る。**
  `Library.open(extract=False)`(`spinebox/library.py:142 open()`)で開ける巻 = フォルダの巻、外側が zip の中の画像を直接読む巻、
  展開キャッシュに取り出し済みの内側 zip の巻。それ以外(外側が 7z / rar、未取り出しの内側アーカイブ、取り出し済みでも 7z / rar の内側)は
  `NeedsExtraction` で、何も展開せずに 404 `{"pending": true}`(`Cache-Control: no-store`)を返す。小説・1 ページ目が画像でない巻は 404 `{"pending": false}`
- **入れ子の巻の表紙ができる経路は 2 つ。** 巻を読むために開いた後(展開キャッシュに残っていれば次の一覧で作れる)と、明示操作の
  「表紙のみ補完」(管理のデータ管理タブのジョブ `thumbnails`。表紙の巻のうちまだ無いものを 1 巻ずつ、取り出してでも作る。進み具合はタスクセンター。ほかのジョブの実行中は待ち行列に入る。旧 `POST /api/thumbnails/build` も同じジョブ)。#102 で作品一覧のヘッダーの「表紙を作る」は外した
- **画面は「無ければ文字だけのカード」。** `<img loading="lazy">` で、読み込めたら見せ、404 などのエラーなら要素ごと消す。
  サーバーは `ETag` + `Cache-Control: private, no-cache` なので、表紙を作った後に画面を開き直すと取り直す
  (以前は「表紙を作る」の後に `?rev=` を付けて同じ画面で取り直していたが、作成を画面から起動しなくなったので廃止した)

## なぜこうなっているか

- 一覧は作品の数だけ表紙を要求する。全巻の 1 ページ目をその場で取ると、入れ子のアーカイブ(7z の中の rar の中の zip)を巻の数だけ
  展開キャッシュへ取り出すことになり(1 つ数秒、2 GiB の上限を一覧の表示だけで回しうる)、スキャンで内側を取り出さない方針([[extraction-cache]])とも食い違う
- 外側が 7z / rar の巻も一覧では作らない。目次を読むだけで 7-Zip が起動し、ソリッド 7z では `cover.jpg` が塊の後ろにあると 1 枚に巻全体の展開がかかる
  (`cover.jpg` は自然順で末尾に並ぶことがある。GOAL.md「試作で確かめた前提」)
- 7z / rar の内側を `extract=False` で弾くのも同じ理由(取り出し済みでも、目次を読むのに 7-Zip が起動する)
- 表紙の巻を最小の巻番号にしたのは、要望が「1 巻の表紙」だったため。名前の巻番号は候補([[volume-split]])なので、分解前の
  「v01-03」の親は 1〜3 巻の範囲の巻として扱い、その 1 ページ目(=1 巻の表紙)を使う。分解すると子の v01 に移る(元の zip が同じなら作り直しは取り出し済みのキャッシュから)
- 置き場を展開キャッシュと分けたのは、上限と寿命が違うから。展開キャッシュは巻を読むたびに数百 MB 単位で回り、同じ LRU に入れると表紙が押し出される

## 検討して採らなかった選択肢

| 案 | 却下した理由 |
| --- | --- |
| スキャンのときに全巻の表紙を作る | スキャンは目次だけ読む(フェーズ 1 の完了条件)。内側アーカイブを取り出すことになる |
| 一覧の表示で入れ子の巻も取り出して作る | 上の「なぜ」。巻の数だけ展開が走る |
| 1 ページ目をそのまま `<img>` に出す(`/pages/0`) | 表紙 1 枚が数 MB の原寸で、一覧が重い。入れ子の巻は同じく取り出しが走る |
| 表紙の画像をネットから取る(公開 DB の書影) | GOAL.md「作らないもの」 |
| 裏で少しずつ作る常駐処理 | 明示操作と「開いた後に作れる」で足りると判断した。必要になれば `background_analysis` と同じ形で `Thumbnails.build()` を 1 巻ずつ呼べばよい |
| 表紙の有無を `GET /api/works` に載せる | 一覧のたびに全作品の元ファイルを `stat()` することになる。`<img>` の 404 で文字だけのカードに戻す方が安い |

## 落とし穴

- オフラインの巻は表紙も出ない(キーに元ファイルの `stat()` を使うため 409)。作った表紙は消さずに残り、再接続すると同じキーで使われる

## 関連

- [[frontend]] — 作品カードの表紙(作成は管理のデータ管理タブの「表紙のみ補完」)
- [[backend]] — API 契約(`/api/volumes/{id}/thumbnail`・`/api/thumbnails`)
- [[extraction-cache]] — 置き場の仕組みと、取り出し済みかを作らずに確かめる `ExtractionCache.use_existing()`
- [[work-grouping]] — 表紙の巻を選ぶ作品の巻
- [[volume-split]] — 開いて分解すると表紙の巻が子の巻に移る
- [[duplicate-detection]] — 同じく Pillow で 1 ページずつ読む処理(画素数の上限を共用)
