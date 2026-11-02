---
name: dads-ui
description: >
  デジタル庁デザインシステム(DADS)β版 v2.18.2 に準拠した画面の作成・修正を行うときに使う。
  「デジタル庁デザインシステムに合わせて」「DADS 準拠で」「デザインシステムに沿って直して」
  「この画面の UI を整えて」「ボタン/フォーム/テーブル/モーダルを DADS の仕様に」
  「アクセシビリティを満たして」「画面を作って」「UI を修正して」などのフレーズで発動。
  対象は frontend/ の React + CSS の画面。バックエンドだけの変更では使わない。
---

# Skill: DADS 準拠の画面作成・修正

## Objective

`frontend/` の画面を、デジタル庁デザインシステムβ版 v2.18.2 の仕様(基本デザイン・
コンポーネント・アクセシビリティ)に沿って作り、直す。ゴールは見た目を似せることではなく、
**値・構造・振る舞いを仕様に一致させる**こと。

## 前提: このリポジトリのローカルデザインシステム

DADS はプラットフォーム型のデザインシステムで、そのまま使わず導入先のローカルデザインシステムを
作る前提になっている(ガイダンス「ローカルデザインシステム」)。SpineBox のローカル DS は
`frontend/src/styles.css` の CSS 変数と、ライト/ダークの 2 テーマ。

| 対象 | 決め方 |
| --- | --- |
| 構造・寸法・字・角丸・影・振る舞い | **DADS の値をそのまま使う** |
| 色(キーカラー) | 既存の `--accent` 等を維持してよい。ただしコントラスト要件は必ず満たす |
| ダークテーマ | DADS に定義がない SpineBox の拡張。ライトと同じ要件を課す |
| 新規画面 | DADS のトークン値を直接使う |
| 既存画面の修正 | 触る範囲だけ DADS に寄せる。画面全体の一括塗り替えはしない |

## 一次情報(推測で書かない)

| 何 | 場所 | 使い方 |
| --- | --- | --- |
| 仕様本文 | 公式 Markdown 一式(v2.18.2 / 2026-10-21) | `components/<slug>/index.md` の「使い方」「アクセシビリティ」を読む |
| 実装の正 | [HTML 版スニペット](https://github.com/digital-go-jp/design-system-example-components-html/tree/v20261021)(v2.18.2 はタグ `v20261021`) | `src/components/<name>/<name>.css` と `*.html`。状態・寸法・ARIA はここが一次 |
| トークン | `@digital-go-jp/design-tokens@2.1.0` | `dist/tokens.css`(Figma 2.0.4〜2.18.1 対応。v2.18.2 の更新はコンポーネント側で、トークン値の変更はない) |
| バージョン | [サイトのトップ](https://design.digital.go.jp/dads/) | 「v2.18.2」の表示と zip の日付を確認 |

```powershell
$d = "$env:TEMP\dads"; New-Item -ItemType Directory -Force $d | Out-Null
Invoke-WebRequest -UseBasicParsing https://design.digital.go.jp/dads/dads-markdown-20261021.zip -OutFile "$d\docs.zip"
Expand-Archive "$d\docs.zip" "$d\docs" -Force
Select-String -Path "$d\docs\components\button\index.md" -Pattern '^#{1,3} '

# 実装 CSS(例: ボタン)
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/digital-go-jp/design-system-example-components-html/v20261021/src/components/button/button.css
```

- `main` は最新版。v2.18.2 に対応するタグは `v20261021`(日付タグ)。取得はタグに固定する。
- サイトのトップが v2.18.2 でなくなっていたら、このスキルの数値は古い。取得した Markdown と CSS を優先する。
- 「使い方」ページが未整備のコンポーネント(タブ・モーダル等)は、HTML 版スニペットが実質の仕様。

## 手順

1. **DADS のコンポーネントに割り当てる** — `references/components.md` の表で slug を決める。
   該当が無ければ近いものの構造とアクセシビリティを借り、独自 UI を新設しない。
2. **「使い方」と「アクセシビリティ」を読む** — 概要だけで実装しない。判断に迷ったら一次情報に戻る。
3. **値を決める** — `references/tokens.md`。色は既存変数、寸法・字・角丸・影は DADS の値。
4. **セマンティック HTML → CSS の順で実装する** — 状態(`:hover` `:focus-visible` `:active`
   `:disabled`)を DADS の CSS と同じ順・同じ値で揃える。`dads-*` クラスは持ち込まず、
   既存のクラス名に値を写す(スタイルの出どころを 2 つにしない)。
5. **検証する** — `references/checklist.md`。`npm test` とブラウザ(375px 必須)。
6. **報告する** — DADS から外した値・独自に足した UI は理由つきで書く。

## 必ず守る

### タイポグラフィ

- 本文・UI は 16px 以上。14px は付随情報のみ、**14px 未満は禁止**。
- 読み物の本文は `line-height` 1.5 以上(DADS の既定は 1.7、`letter-spacing: 0.02em`)。
- 見出しレベルを飛ばさない。`h1` は 1 ページ 1 つ。
- 日本語にイタリックを使わない。

### 色

- 文字 4.5:1、非テキスト(枠線・アイコン・区切り)3:1。
- **既存の `--border`(#dddbd5)は背景 `--bg` に対して 1.27:1**。入力欄・チェックボックス・選択枠など
  「境界が操作の理解に要る」要素には使わない → gray-600 `#666666`(5.74:1)。ダークは
  `#6b6b75` 目安(3.39:1)。
- リンクは下線。色だけでリンク・状態・重要度を区別しない。
- フォーカスリングは**黒 4px + offset 2px + 黄 `#ffd43d` 2px** の二重。変更禁止
  (`outline` と `box-shadow` の組み合わせ)。
- セマンティックカラーの色相と意味の対応を変えない(エラー=赤、成功=緑、警告=黄/橙)。

### 操作

- ターゲット 44×44px 以上。小さいボタンは `::after` で領域を広げる。
- `disabled` を原則使わない。使うなら理由と解除条件を近くに書く。
- ボタン: 塗り(プライマリー)は 1 画面 1 つ、アウトライン(セカンダリー)は文脈ごと 3 つまで、
  テキスト(ターシャリー)は中止・キャンセル。デスクトップは右端がプライマリー・左端がターシャリー。
- `tabindex` でフォーカス順を変えない。`order` / `grid-placement` で視覚順と DOM 順をずらさない。
- アイコンはラベルと併用する。単体なら `aria-label`。

### フォーム

- ラベル必須。placeholder をラベル代わりにしない。
- 必須/任意のラベル、サポートテキスト、エラーテキストをそろえる。エラーは
  `aria-invalid="true"` + `aria-describedby`(エラー文とサポート文の id)。
- エラーテキストに `aria-live` を使わない。
- `maxlength` で制限しない。`disabled` / `readonly` を使わない。コピペを塞がない。

### レイアウト

- ブレークポイントは 768px の 1 つ(モバイル <768 / デスクトップ ≥768)。既存の
  `max-width: 767px` と一致させる。
- リキッド。375px で横スクロールを出さない。
- 12 カラム、ガターは本文サイズの 2 倍(32px)。

### 動き・状態

- `prefers-reduced-motion: reduce` を尊重する。
- `forced-colors: active` でも境界・状態が分かるようにする。
- オーバーレイ上の要素は、影があってもボーダーを付ける(強制カラーモードで影が消えるため)。

## このリポジトリの罠

- **`.page` を新しい画面のコンテナに使わない** — リーダーの紙面用(#99)。
  `pointer-events: none` は `.stage .page` に限定してある。
- **ライト/ダークの両方**を確認する。`:root[data-theme="dark"]` と `prefers-color-scheme` の
  2 経路がある。
- 768px 未満のサイドバーは引き出し。画面の要素を引き出しの上に重ねない。
- `styles.css` は 1 ファイル。節コメント(`/* 一覧 */` など)の下に足す。
- テストは role / aria / テキストで書く(`getByRole` が使える形にする)。見た目のクラス名に依存させない。

## 出力

- 変更したファイルと、DADS のどのコンポーネント・どの値に合わせたか
- 検証結果(375px / 768px / 1280px、キーボード、コントラスト、ライト/ダーク)
- DADS から外した箇所とその理由

## References

- `references/tokens.md` — 色・タイポ・余白・角丸・影・ブレークポイントの値と既存変数との対応
- `references/components.md` — 画面要素 → DADS コンポーネントの対応と必須事項
- `references/checklist.md` — 検証手順(自動 + ブラウザ + キーボード)
