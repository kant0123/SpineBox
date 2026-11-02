---
type: component
summary: 小説(EPUB・テキスト)の巻と縦書きリーダー。EPUB の HTML を信用しない 3 段の安全方針(サーバーの許可リスト・ブラウザの作り直し・CSP)、文字コード判定と分割、行の途中で切らないページ割り、区切り+割合の読書位置、却下した代替案
updated: 2026-11-02
related: [overview, backend, frontend, archive-reading]
---

# 小説リーダー(EPUB・テキスト)

`.epub` と `.txt` を 1 巻として登録し、縦書き(`vertical-rl`)・右から左へのページ送りで読む。
巻の `format`(`epub` / `text`。画像の巻は NULL、API では `images`)で漫画のリーダーと切り替える。

## 責務

- する: スキャンでの登録(EPUB は目次だけ、テキストは中身を読まない)、開いたときの EPUB の解析(container.xml → OPF → spine)と
  テキストの文字コード判定・分割、本文の無害化、縦書きのページ割り、区切り+割合の読書位置
- しない: アーカイブの中の `.epub` / `.txt`(下の「決めたこと」)、PDF、横書き表示、EPUB の CSS・目次(nav)・リンク、
  CBZ への整理([[organize]] は小説を `blocked` にする)、重複候補の指紋([[duplicate-detection]] は小説を対象にしない)

## 入口となるファイル

| パス | 役割 |
| --- | --- |
| `spinebox/scanner.py:406 _scan_novel()` | 小説の登録。危険な名前・上限超え・container.xml の無い EPUB は理由を `scan_issues` に残して巻にしない |
| `spinebox/novel/books.py:59 Books` | 巻を開いて区切りの HTML と画像を返す。テキストの復号・区切りと EPUB の構造は元ファイルの版ごとに数冊だけメモリに覚える |
| `spinebox/novel/epub.py:100 open_book()` | EPUB の解析。参照の解決は `resolve_href()`、XML は `parse_xml()`(実体宣言を拒む) |
| `spinebox/novel/sanitize.py:185 sanitize_html()` | サーバー側の無害化(1 段目) |
| `spinebox/novel/text.py:24 decode_text()` | 文字コード判定。分割は `split_sections()`、青空文庫形式のルビは `line_to_html()` |
| `frontend/src/novel/NovelReader.tsx:59 NovelReader()` | リーダー。行の位置の測り方は `measureColumns()` |
| `frontend/src/novel/pagination.ts:40 pageStarts()` | ページの切れ目・位置の換算・ページ送りの純粋関数 |
| `frontend/src/novel/sanitize.ts:58 sanitizeFragment()` | ブラウザ側の無害化(2 段目) |

API(`GET /api/volumes/{id}` の `format`・`sections`・`encoding`、`/sections/{i}`、`/resources/{i}`、進捗の `position`)は [[backend]] の API 契約の表。

## 安全方針: EPUB の中の HTML / CSS / SVG は信用しない

3 段にしている。どれか 1 段がすり抜けても、残りで止まるようにするため。

1. **サーバーの許可リスト(`sanitize_html`)。** 元の文字列を手直しするのではなく、許した要素・属性だけを出力し直す。
   残すのは文章の構造(段落・見出し・ルビ・強調・リスト・表・画像)と、縦中横などの決まった class だけ。
   `script` / `style` / `iframe` / `object` / フォーム部品などは中身ごと捨て、`on*`・`style`・`href` は出さない。
   画像の参照は、本文の場所からの相対パスを EPUB の中の manifest の画像(JPEG / PNG / GIF / WebP)と照らし、
   `/api/volumes/{id}/resources/{n}` に書き換えたものだけを残す。外部 URL・`data:`・`javascript:`・見つからない参照の画像は消す。
   SVG は中の `image` だけを `<img>` にし、ほかは捨てる(表紙を SVG で包む EPUB が多い)。SVG 画像そのものは資源として返さない
2. **ブラウザでの作り直し(`sanitizeFragment`)。** サーバーが返した HTML も信用せず、`DOMParser`(スクリプトを実行せず画像も読み込まない文書)で
   解析し、同じ許可リストで新しい要素を作って入れる。`innerHTML` / `dangerouslySetInnerHTML` は使わない
3. **CSP。** すべての応答に `Content-Security-Policy`(`script-src 'self'` でインラインのスクリプト・イベントハンドラを止め、
   `connect-src` / `img-src` も自分のオリジンだけ)と `X-Content-Type-Options: nosniff` を付ける(`spinebox/app.py:41 APP_CSP`)。
   画像の資源は直接開かれても何も動かないよう `default-src 'none'; sandbox` を付ける

**EPUB の CSS は使わない。** `url()` での外部読み込み・`@import`・画面を覆う配置(偽の UI)を止めるため。見た目はリーダーの CSS が決める。
そのため書体・字下げ・中央揃えなど本の指定は落ちる。縦中横(`tcy`)・正立・横倒し・傍点だけは class 名の当たりで拾っている。

ZIP としての読み取りは [[archive-reading]] の `list_entries()`(エントリ名の検証・展開上限)を通す。危険な名前を 1 つでも含む EPUB は
まともな作成ツールの出力ではないので丸ごと扱わない(漫画のアーカイブは危険なエントリだけ飛ばして残りを読むのと違う)。
container.xml・OPF は 4 MiB、本文 1 つは 16 MiB を上限にして読む。XML は `<!ENTITY` を含めば拒む(展開爆弾・外部実体)。

## 文字コードと分割(テキスト)

- 判定順は UTF-8 BOM → UTF-16 BOM → UTF-8 → cp932。どれも**厳密に**復号できたものを使う。BOM の無い UTF-16 は判定しない
  (NUL を含むものは理由つきで拒む。ASCII だけの UTF-16 は UTF-8 としても「復号できて」しまい、黙って化けるため)。
  判定できないときは 422 にして、理由を `scan_issues` に残す。スキャンは中身を読まないので、一覧には出たまま開けない形になる
- 1 ファイル 64 MiB まで(全体を復号して区切るため)。区切りは行の切れ目でおよそ 2 万文字ごと(`SECTION_CHARS`)。
  1 行が長すぎるときだけ行の途中で切り、ルビ(`《…》`)の途中では切らない。区切りは中身だけから決まるので、読書位置の基準になる
- 青空文庫形式のルビ(`｜漢字《かんじ》`、`|` も可、区切り記号なしは直前の漢字の並び)に対応する。`［＃…］` の注記は表示しない。
  HTML はサーバーが本文をエスケープしてから組み立てるので、本文中の `<` は文字のまま出る

## ページ割りと読書位置

- **区切り(section)。** EPUB は spine の本文 1 つ(`linear="no"` は除く)、テキストは上の 2 万文字。`page_count` は区切りの数で、開いたときに入る
- **ページの切れ目は、描画した後の実際の行の位置から決める(`pageStarts`)。** 文字(ルビを含む)と挿絵の矩形を `Range.getClientRects()` で集め、
  重なる(2px 未満の隙間の)ものを 1 行にまとめ、画面に収まらなくなった行の手前で切る。次のページの最初の行が左端に覗かないよう、
  ページの残りは `clip-path` で隠す。画面より広い挿絵だけは画面の幅で切る
- **位置は「区切りの番号(`reading_progress.page`)+区切りの中の割合(`reading_progress.position`)」。** 割合は帯の右端からの距離 / 帯の幅。
  文字サイズ・画面幅が変わったら、同じ割合を含むページを開き直す。ページ番号は保存しない(端末・文字サイズで変わるため)
- 全体の進み具合(メニューの %)は区切りの長さ(テキストは文字数、EPUB は本文の展開後のバイト数)で重み付けする
- 行の高さは文字サイズの 2 倍(`LINE_HEIGHT`)。ルビ(0.5em)が行間に収まる大きさ

## 決めたこと(却下した代替案)

- **`sandbox` 付き iframe で本文を出す案は採らなかった。** スクリプトは止まるが、`allow-same-origin` を付けない限り親から中を測れず、
  縦書きのページ割り(行の位置)・タップの判定・文字サイズの変更が iframe の中と外に割れる。付けると sandbox の意味が薄れる。
  代わりに 2 段の許可リスト+CSP にした
- **EPUB の CSS をサニタイズして通す案も採らなかった。** CSS の構文は広く(`url()`・`image-set()`・`@font-face`・`@import`・エスケープ)、
  許可リストを保守し続けるより、見た目をリーダーが決める方が安全で単純
- **行の高さの整数倍でページを切る案は実ブラウザで破綻した。** 当初は 1 行の幅 = 文字サイズ × 1.8 を整数 px に丸め、ページ幅をその倍数にしていた。
  ヘッドレス Chromium で、ルビのある行が 1px ずつ広がり(親文字の字形がフォントの寸法で 1em より広い)、30 行ほどで最初の行が
  左端で半分切れた。行の幅はフォント(端末)次第でぶれるので、実際の行の位置から切る形にした
- **アーカイブの中の `.epub` / `.txt` は巻にしない。** 漫画のアーカイブには説明書きの `.txt` がよく入っていて、小説と取り違えるため。
  同じ理由で、画像のあるフォルダの `.txt` も添え物として扱う。中の EPUB が要る実データが出たら、内側アーカイブと同じく開いたときに
  [[extraction-cache]] へ取り出す形で足す
- **縦書き指定の無い EPUB も縦書きで出す。** 日本語の小説を読む用途(GOAL.md)なので。横書き表示は作っていない
- **`sources.kind` は小説も `archive`。** CHECK 制約の作り直し(テーブルの再作成)を避けるため。読み分けは `volumes.format` で行う

## 落とし穴

- **小説の巻を `Library.open` に渡さない。** ページ画像を持たないので 422 にしている(`GET /pages/{i}` も 422)。重複候補の指紋は `format IS NULL` で除いている
- **本文の HTML をサーバーで作る経路を足すときは、必ず `sanitize_html` かエスケープを通す。** フロントの 2 段目に頼らない
- jsdom には描画が無く `Range.getClientRects` も無いので、ページ割りは行の位置が測れず「画面の幅で等分」に倒れる。
  vitest(`frontend/src/novel/NovelReader.test.tsx`)は `scrollWidth` を文字数・文字サイズから計算する差し替えで試し、
  行が切れないことは実ブラウザで確かめる
- 挿絵は読み込み後に大きさが決まるので、`load`(捕捉)でページ割りをやり直す

## 経緯

- #57 で作成(フェーズ 4「EPUB・テキストの縦書き表示」)。マイグレーション 7(`volumes.format`・`reading_progress.position`)。
  クラウドのヘッドレス Chromium で、合成の EPUB(縦書き指定・ルビ・挿絵・危険な HTML)と cp932 のテキストを実バックエンドで配信し、
  375x812 と 812x375 で最初から最後まで送って、どのページでも行が境目で切れないこと・外部への通信が 0 件・コンソールのエラーが 0 件・
  文字サイズを変えても近い位置に留まることを確かめた。実機(スマホ)での確認は未実施
  ローカルの組み込みブラウザ(Chromium)でも、実バックエンド(`_参考/` の漫画と合成の小説を混ぜた蔵書)に対して確かめた。
  375x812 で縦書きの EPUB・青空文庫形式のテキスト(UTF-8 / BOM 付き / cp932 / UTF-16)を最初から最後まで送り、812x375 で縦書き指定なしの EPUB も読めた。
  文字サイズを 18→24px に上げても同じ割合を含むページに留まり、再読み込みで同じページから再開した。漫画のリーダーは 40 ページ送って壊れていない。
  悪意のある EPUB(`<script>`・`on*`・`javascript:`・外部の画像 / CSS / iframe / object / SVG / フォーム / 音声映像)は、表示された DOM に `h1`・`p`・内部の `img` だけが残り、
  別ポートの記録用サーバーへの要求は 0 件・スクリプトの実行なし。CSP は、手で差し込んだインラインのスクリプト・イベントハンドラ・外部の画像をコンソールのエラーつきで止めた
