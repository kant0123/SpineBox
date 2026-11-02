# コンポーネントの対応表

slug は公式サイト `https://design.digital.go.jp/dads/components/<slug>/` と、
公式 Markdown の `components/<slug>/index.md`。実装は
`https://github.com/digital-go-jp/design-system-example-components-html/tree/v20261021/src/components/<name>` で、
`<name>` は概ね slug と同じ。`form-control-label` はサイトのコンポーネント一覧に無いが、
フォームの実装で使う。タグ `v20261021` が v2.18.2 に対応する(`main` は最新)。

## テキスト

| 要素 | slug | 必須事項 |
| --- | --- | --- |
| 見出し | `heading` | レベルを飛ばさない。サイズは 64〜16 の段から選ぶ。`h1` は 1 ページ 1 つ |
| 本文 | (`typography`) | 16px / 1.7 / `letter-spacing: 0.02em` |
| リンク | (`link-text`) | 下線。訪問済みはマゼンタ。ターゲットは 24×24px 以上。外部・新規タブはアイコン + 「新しいタブで開きます」等の代替テキスト |
| 箇条書き | `list` | `<ul>` を使う(項番が要るときだけ `<ol>`)。入れ子は `<ul>` の中に `<ul>` |
| 説明リスト | `description-list` | `<dl>` / `<dt>` / `<dd>`。ラベルと値の対 |
| 引用 | `blockquote` | 引用元を `<cite>` かリンクで示す |
| 区切り | `divider` | `<hr>`。色だけで区切らない |

## 操作

| 要素 | slug | 必須事項 |
| --- | --- | --- |
| ボタン | `button` | 塗り/アウトライン/テキストの 3 種。最小サイズは lg 56px・md 48px・sm 36px・xs 28px。sm/xs は `::after` で 44px のヒット領域。塗りは原則 1 画面 1 つ |
| タブ | `tab` | `role="tablist"` / `tab` / `tabpanel`、`aria-selected`、矢印キーで移動、パネルは `tabindex="0"`。**絞り込みには使わない** |
| アコーディオン | `accordion` | ネイティブの `<details>` / `<summary>`。見出しは `<summary>` の中 |
| ディスクロージャー | `disclosure` | 追加情報の折りたたみ。主要情報を隠さない |
| ページネーション | `page-navigation` | `<nav aria-label="ページ">`。アイコンだけのボタンは視覚的に隠したラベル |
| ステップ | `step-navigation` | `<nav aria-label="ステップ">` + `<ol>`。現在地は `aria-current`、完了は視覚的に隠した「完了」 |
| パンくず | `breadcrumb` | `<nav aria-labelledby>` + 視覚的に隠した「現在位置」。最後の項目は `aria-current="page"` |
| メニュー | `menu-list` / `horizontal-menu` / `mobile-menu` / `drawer` | モバイルの引き出しは `drawer`。ハンバーガーは `hamburger-menu-button` |

## フォーム

| 要素 | slug | 必須事項 |
| --- | --- | --- |
| ラベル一式 | `form-control-label` | ラベル(太字)+ 必須/任意 + サポートテキスト + エラー。必須は「※必須」を赤 |
| 1 行入力 | `input-text` | 高さ sm 40 / md 48 / lg 56。枠 1px gray-600、角丸 8。`aria-describedby` にエラーとサポート。placeholder は原則使わない |
| 複数行 | `textarea` | ラベル + サポート + エラー。高さは `rows` で確保 |
| セレクト | `select` | ラベル。未選択の選択肢は「選択してください」等 |
| チェックボックス | `checkbox` | 複数選択。`<fieldset>` + `<legend>` でグループ化 |
| ラジオ | `radio` | 単一選択。`<fieldset>` + `<legend>` |
| スイッチ | `switch` | 切り替えが即時反映される設定だけ。送信ボタンが要るものには使わない |
| 検索 | `search-box` | `<label>` で入力を包み、視覚的に隠した「検索」。送信ボタンは塗り |
| ファイル | `file-upload` | `<input type="file">` + ドロップ領域。選択済みファイルの削除手段を用意 |
| 日付 | `date-picker` | 入力 + カレンダー。キーボードで日付を選べる |

## データ表示

| 要素 | slug | 必須事項 |
| --- | --- | --- |
| テーブル | `table` | `<figure>` + `<figcaption>`、`<th scope="col">`。シンプルな構造を保つ。狭い画面は横スクロールの入れ物で包む |
| カード | `card` | 1 つの主題。カード全体をリンクにしない(リンクは見出しに) |
| チップ | `chip-label` / `chip-tag` | 状態は `chip-label`、削除できる属性は `chip-tag` |
| 進捗 | `progress-indicator` | `role="progressbar"` + `aria-valuenow` / `aria-valuemin` / `aria-valuemax`。不定なら `aria-valuetext` |
| 画像 | `image` | `alt`。キャプションは `<figcaption>` |
| 目次 | `toc` | 見出しへのリンク集。長い読み物だけ |

## 通知・オーバーレイ

| 要素 | slug | 必須事項 |
| --- | --- | --- |
| モーダル | `modal-dialog` | ネイティブ `<dialog>` + `showModal()`。`aria-labelledby` で見出しを参照。開いたら見出しへフォーカス、Esc で閉じる、閉じたら元の要素へ戻す。ボーダー 1px + `elevation-3` |
| 通知バナー | `notification-banner` | 見出し + 本文 + アクション。閉じるボタンにはラベル。`info` / `success` / `warning` / `error` の 4 種 |
| 注釈 | `notice-block` | 補足情報。エラー表示の代わりに使わない |
| ドロワー | `drawer` | 画面端から出すコンテナ。モバイルメニューの入れ物 |

## このアプリの画面 → 何を使うか

| 画面 | 使うもの |
| --- | --- |
| 作品・巻の一覧 | `card`(表紙つき)/ `list`、`search-box`、`chip-label`(状態)、`page-navigation` |
| 作品の詳細 | `heading`、`description-list`、`table`(巻)、`button`、`breadcrumb` |
| 管理(タブ) | `tab`、`table`、`notification-banner`、`progress-indicator`、`button` |
| フォーム(整理・設定) | `form-control-label` + `input-text` / `select` / `checkbox` / `radio` / `textarea` + `button` |
| 確認・警告 | `modal-dialog`(破壊的操作の確認)、`notification-banner`(結果) |
| リーダー | 紙面・見開きは DADS に対応なし。**操作部だけ** `button` / `drawer` / `progress-indicator` を当てる |

## DADS にないもの・使わないもの

- **リーダーの紙面表示・見開き・縦書き**は DADS のコンポーネントにない。独自のままにする。
- **カルーセル**は DADS でも「使用に注意が必要」とされている。原則使わない。一覧の代替にならない。
- **タブをフィルタに使わない**(DADS の「注意が必要なケース」)。絞り込みはフォームコントロール。
- 複数セクションを同時に見せたいなら `accordion`、1 つだけ表示するなら `tab`。
