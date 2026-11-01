---
type: component
summary: 蔵書一覧と右綴じ・見開きリーダーのブラウザ側(frontend/)。見開き計算の規則、読書位置の保存の仕方、読み込み待ち・取得失敗の表示、モック API、CI への組み込み
updated: 2026-11-01
related: [overview, local-ci, server-setup, volume-split, organize]
---

# フロントエンド(`frontend/`)

蔵書一覧と、右綴じ・見開きのリーダー。React + Vite + TypeScript。`npm run build` の出力 `frontend/dist` を
バックエンドが `/` で配信する。

## 責務

- する: 一覧(`GET /api/volumes`)・スキャンの起動(`POST /api/scan`)・巻の食い違いのバッジと説明・「中の巻を調べる」(`POST /api/volumes/{id}/analyze`)・リーダー(ページ画像の表示・先読み・
  読書位置の保存と再開)・整理(CBZ 化)のプレビュー → 実行と、履歴からの元に戻す([[organize]])。表示設定(見開き・表紙単独)は端末ごとに `localStorage` へ
- しない: ページの並び順・ページ数・オフライン判定・巻番号と食い違いの判定。どれもバックエンドが決めた `pages[]`・`offline`・`findings` をそのまま使う

## 入口となるファイル

| パス | 役割 |
| --- | --- |
| `frontend/src/reader/layout.ts:15 buildSpreads()` | 見開きの組み方。ページ送り `stepPage()`・タップ/スワイプ/キーの判定もこのファイルの純粋関数 |
| `frontend/src/reader/Reader.tsx:57 Reader()` | リーダー画面。位置の保存・先読み・操作の結線 |
| `frontend/src/reader/loadError.ts:4 volumeErrorMessage()` | 巻情報の取得失敗(404 / 409 / 422 / 接続不可)の文言 |
| `frontend/src/reader/progressSaver.ts:10 createProgressSaver()` | 読書位置の送信のデバウンス |
| `frontend/src/organize/Organize.tsx:59 OrganizePlan()` | 整理のプレビュー → 実行(`#/organize/<巻 id>`)。履歴と元に戻すは同じファイルの `OrganizeHistory()`(`#/organize`) |
| `frontend/src/api.ts:107 api` | API 契約(#14 と共有)の型と呼び出し |
| `frontend/src/library/Library.tsx:48 Library()` | 一覧。食い違いの説明は `FindingDetail()` |
| `frontend/src/mock/mockBackend.ts:88 createMockBackend()` | モック API。`npm run dev:mock` とテストで共用。初回オープンの遅延・巻ごとの失敗を差し込める |

## 決めたこと(コードからは理由が読めないもの)

- **ページ番号は 0 始まり。** `progress.page` も `pages[].index` と同じ番号で送る(画面の表示だけ +1)。
  #14 と同時に作ったため、契約の文面に始まりの番号が無かった。バックエンドと食い違ったら API 契約側を正にして合わせる
- **位置は「画面」ではなく「ページ」で持つ。** 回転や表紙単独の切替で見開きの組み方が変わっても、読んでいたページを
  含む画面から再開できる(`spreadIndexOf()` で範囲外のページも端に寄せるので、ページ数が減った巻も開ける)
- **見開きは表紙単独 ON が既定。** ON なら `[0] [1,2] [3,4] …`、OFF なら `[0,1] [2,3] …`。端数の 1 ページは最後に単独。
  DOM 上は若い番号が先で、CSS の `flex-direction: row-reverse` で右に置く(右綴じ)。見開きにするのは横長かつ幅 600px 以上のときだけ
- **操作の向き(右綴じ):** 左 1/3 タップ・`←` キーが次、右 1/3・`→` が前、中央がメニュー。スワイプは**右へ払う(指を左から右へ)と次**。
  Issue #15 の文面は「左スワイプ = 次」だったが、右綴じでは次のページが左から来るので、それを引き寄せる向きにした。
  逆が良ければ `swipeAction()` の符号だけを変える
- **一度既読になった巻は、前のページへ戻っても `finished=true` のまま送る。** 読み返しで既読が消えないようにするため
- **保存は 800ms のデバウンスで最後の位置だけ。** 一覧へ戻る・タブが裏に回る(`pagehide`/`visibilitychange`)ときは待たずに
  `fetch(..., {keepalive: true})` で送る。スマホはタブを閉じずに切り替えることが多く、デバウンス中の位置が失われるため
- **画面の切り替えはハッシュ(`#/read/<id>`)。** バックエンドは `dist` を `/` で配信するだけでよく、深いパスを
  `index.html` へ振り向ける設定が要らない。ルーターのライブラリも入れていない。
  ただし共有されたリンクやブックマークが `/read/<id>`(パス)の形だと、バックエンドの SPA fallback で画面は開くがハッシュが空で一覧になる。
  そのため起動時に `frontend/src/route.ts:26 canonicalUrl()` が `/#/read/<id>` へ `replaceState` で直す(#19)
- **食い違いの説明はカードのリンクの外の `<details>` に置く。** リンクの中に置くと、説明を開くつもりのタップでリーダーへ飛ぶ。
  バッジ(「巻の食い違い」)はリンクの中。分解で作った子の巻には「「親の名前」の中」と出す。同じ食い違いは親の子すべてに出る([[volume-split]])
- **整理は一覧のカードの「CBZ に整理」から。** 押しただけでは計画(プレビュー)を作るだけで、蔵書は動かない。
  実行ボタンは計画が `planned` のときだけ出す(`blocked` は理由だけ出す)。サーバーの 409 の `detail` をそのまま見せる。
  プレビュー画面を確認の代わりにして、`window.confirm` は使っていない
- **一覧に表紙画像を出していない。** 表紙は 1 ページ目から作る方針(GOAL.md)だが、一覧で全巻の 1 ページ目を取ると
  入れ子のアーカイブを巻の数だけ展開させることになる。サムネイルの仕組み(キャッシュ)ができてから足す
- **読み込み待ちは 2 段で見せる(#26)。** 入れ子のアーカイブ(7z の中の rar)を初めて開くと、`GET /api/volumes/{id}` が内側を
  取り出す間(実測 3〜8 秒)何も出ず、黒い画面のままで読み込み中と分からなかった。巻情報の取得中はリーダーの黒い枠の中央に
  スピナーと「一覧へ戻る」を出し、1.5 秒(`SLOW_HINT_MS`)を超えたら「初めて開く巻は展開に数秒かかる」と補足する。
  巻情報が来た後は、表示中のページ画像が `load` / `error` になるまで画面中央に小さく「読み込み中」を重ねる。
  先読み済みのページで一瞬ちらつかないよう、重ね表示は CSS で 0.3 秒遅れて現れる
- **取得失敗の文言は HTTP ステータスで分ける。** 404 は「蔵書から無くなった」、409 は「オフライン」、422(壊れたアーカイブ・
  展開上限超過)はバックエンドの `detail` をそのまま出す。理由はバックエンドしか知らないため。どの失敗でも一覧へ戻るリンクを出す
- **汎用カルーセルライブラリは使わない**(GOAL.md の初期既定)。ページ送りは状態の差し替えだけで、アニメーションは無い

## 落とし穴

- `.stage` の `touch-action: pinch-zoom` は、1 本指の横の払いをブラウザのスクロールに取られないためのもの。消すとスマホでスワイプが効かない
- モック(`src/mock/`)は `vite.config.ts` からだけ読む。アプリ側から import すると本番のバンドルに入る
- jsdom の画像は読み込まれず `load` も来ないので、ページ画像の読み込み待ちのテストは `fireEvent.load` / `fireEvent.error` で進める
  (`src/reader/Reader.test.tsx`)。キャッシュ済み画像を `ref` で `complete && naturalWidth > 0` と見て済ませる分岐は実ブラウザでしか通らない
- 単体テストの jsdom には `PointerEvent` が無いので、タップ・スワイプは判定関数の単体テストと実ブラウザで確かめる
  (結線のテスト `src/App.test.tsx` はキーボードで操作している)

## CI への組み込み

`scripts/ci_local.py:37 frontend_commands()` が `test` ジョブの最後に `npm --prefix frontend test` を足す([[local-ci]])。

- `frontend/package.json` が無ければ足さない。`node_modules` が無ければ**飛ばさずに** `npm ci` から始める
  (飛ばすと検査していないのに成功が載る)
- npm は Windows では `npm.cmd` なので、`shutil.which()` で解決したパスで呼ぶ(名前のままだと `subprocess` が見つけられない)
- Actions(`.github/workflows/test.yml`)は先に `npm ci --prefix frontend` を実行するので、スクリプト側の `npm ci` は走らない

## 動かし方

```bash
cd frontend && npm ci
npm run dev:mock   # バックエンド無しでモック API と一緒に起動
                   # 「ページ数未確定の巻(入れ子)」は初回だけ 4 秒待つ。#/read/too-big は 422、#/read/999 は 404、#/read/4 は 409
npm run dev        # /api を http://127.0.0.1:8000 へ中継
npm test           # vitest
npm run build      # frontend/dist
```

## 経緯

- #15 で作成。バックエンド(#14)と並行して、API はモックで開発した
- #30 で食い違いのバッジと説明、「中の巻を調べる」ボタンを足した。クラウドで実 API + 合成の蔵書を 375px のヘッドレス Chromium で表示し、
  横スクロールが出ないことを確かめた(実データでの 375px 確認はローカルで行う)
- #19 でビルド済みの `frontend/dist` をバックエンドから配信して実 API で通した(375px・横長 812x375 で、一覧 → 最終ページ → 位置保存 →
  一覧に戻って再開、`/read/<id>` の直開きとリロード)。LAN 越しの実機(スマホ)の確認はサーバー機側で行う(#20)。常駐・公開の手順は [[server-setup]]
