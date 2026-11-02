# トークン(値の一覧)

出どころは `@digital-go-jp/design-tokens@2.1.0` の `dist/tokens.css` と、HTML 版スニペット
(タグ `v20261021` = v2.18.2)の `src/global.css`。バージョンが上がったら一次情報で引き直す
(`SKILL.md` の取得コマンド)。

## 色

### キーカラー(Blue)

DADS の既定のキーカラー。`--color-key-*` はこの Blue を指す。

| トークン | 値 | | トークン | 値 |
| --- | --- | --- | --- | --- |
| `--color-key-50` | `#e8f1fe` | | `--color-key-700` | `#264af4` |
| `--color-key-100` | `#d9e6ff` | | `--color-key-800` | `#0031d8` |
| `--color-key-200` | `#c5d7fb` | | `--color-key-900` | `#0017c1` |
| `--color-key-300` | `#9db7f9` | | `--color-key-1000` | `#00118f` |
| `--color-key-400` | `#7096f8` | | `--color-key-1100` | `#000071` |
| `--color-key-500` | `#4979f5` | | `--color-key-1200` | `#000060` |
| `--color-key-600` | `#3460fb` | | | |

プリミティブは Blue / Light Blue / Cyan / Green / Lime / Yellow / Orange / Red / Magenta / Purple の
10 色相 × 50〜1200 の 13 段。キーカラーをブランド色に差し替える場合は、同じ色相の段を
`--color-key-*` に割り当てる(ローカルデザインシステムの手順)。

### ニュートラル

| トークン | 値 | 白背景でのコントラスト |
| --- | --- | --- |
| `--color-neutral-white` | `#ffffff` | — |
| `--color-neutral-solid-gray-50` | `#f2f2f2` | 1.11:1 |
| `--color-neutral-solid-gray-100` | `#e6e6e6` | 1.25:1 |
| `--color-neutral-solid-gray-200` | `#cccccc` | 1.61:1 |
| `--color-neutral-solid-gray-300` | `#b3b3b3` | 2.10:1 |
| `--color-neutral-solid-gray-400` | `#999999` | 2.85:1 |
| `--color-neutral-solid-gray-420` | `#949494` | 3.03:1 |
| `--color-neutral-solid-gray-500` | `#7f7f7f` | 4.00:1 |
| `--color-neutral-solid-gray-536` | `#767676` | 4.54:1 |
| `--color-neutral-solid-gray-600` | `#666666` | 5.74:1 |
| `--color-neutral-solid-gray-700` | `#4d4d4d` | 8.45:1 |
| `--color-neutral-solid-gray-800` | `#333333` | 12.63:1 |
| `--color-neutral-solid-gray-900` | `#1a1a1a` | 17.40:1 |
| `--color-neutral-black` | `#000000` | 21:1 |

読み方: **文字は 536 以上**(4.5:1)、**非テキストの境界は 420 以上**(3:1)。500 は文字に使うと
4.0:1 で不足する。`--color-neutral-opacity-gray-*` は同じ段の `rgba(0,0,0,α)`。

### セマンティック

| トークン | 値 | 意味 |
| --- | --- | --- |
| `--color-semantic-success-1` | green-600 `#259d63` | 成功(面) |
| `--color-semantic-success-2` | green-800 `#197a4b` | 成功(文字) |
| `--color-semantic-error-1` | red-800 `#ec0000` | エラー(枠・文字) |
| `--color-semantic-error-2` | red-900 `#ce0000` | エラー(濃) |
| `--color-semantic-warning-yellow-1` | yellow-700 `#b78f00` | 警告(黄) |
| `--color-semantic-warning-yellow-2` | yellow-900 `#927200` | 警告(黄・濃) |
| `--color-semantic-warning-orange-1` | orange-600 `#fb5b01` | 警告(橙) |
| `--color-semantic-warning-orange-2` | orange-800 `#c74700` | 警告(橙・濃) |

### 機能カラー

| 用途 | 値 |
| --- | --- |
| リンク(未訪問) | blue-1000 `#000071` |
| リンク(訪問済み) | magenta-900 `#8b008b` |
| リンク(hover) | blue-900 `#0017c1` |
| リンク(active) | orange-800 `#c74700` |
| フォーカスリング | black `#000000` + yellow-300 `#ffd43d` |

## タイポグラフィ

- ファミリ: `'Noto Sans JP', sans-serif` / 等幅 `'Noto Sans Mono', monospace`
- 太さ: 400(N) / 700(B) の 2 段
- 本文の既定(スニペット): `font-size: 16px; line-height: 1.7; letter-spacing: 0.02em`

| スタイル | サイズ(px) | 行高 | 字間 | 用途 |
| --- | --- | --- | --- | --- |
| Dsp-64B-140 / 57 / 48 | 64・57・48 | 1.4 | 0 | ヘッドコピー |
| Std-45B-140 | 45 | 1.4 | 0 | 大見出し |
| Std-36B-140 | 36 | 1.4 | 0.01em | 見出し |
| Std-32B-150 / 28B-150 | 32・28 | 1.5 | 0.01em | 見出し |
| Std-26B-150 〜 20B-150 | 26・24・22・20 | 1.5 | 0.02em | 見出し |
| Std-18B-160 | 18 | 1.6 | 0.02em | 小見出し |
| Std-17B-170 / 16B-170 | 17・16 | 1.7 | 0.02em | 本文(強調) |
| Std-16N-170 | 16 | 1.7 | 0.02em | **本文の既定** |
| Dns-17N-130 / 16N-130 / 14N-130 | 17・16・14 | 1.3 | 0 | 表・管理画面 |
| Oln-17N-100 / 16N-100 / 14N-100 | 17・16・14 | 1.0 | 0.02em | ボタン等の 1 行 |
| Mono-16N-150 | 16 | 1.5 | 0 | コード |

見出しコンポーネントの既定は太字で、サイズごとの行高は 64〜36 が 1.4、32〜20 が 1.5、
18 が 1.6、16 が 1.7。字間は 45 以上が 0、36〜28 が 0.01em、24〜16 が 0.02em。

## 余白

DADS は余白をトークン化していない(「必要に応じて実施」)。基準単位 **8px** の倍数で規則性を持たせる。

| 値 | 使いどころ |
| --- | --- |
| 8px | ラベルと入力欄、アイコンとラベル |
| 16px | 関連する要素の間、カード内の余白 |
| 24px | 見出しと本文、セクション内の区切り |
| 32px | セクションの間(ガターと同じ) |
| 48px | 大きなセクションの間 |
| 64px | ページの主要な区切り |

## 角丸

| トークン | 値 | 使いどころ |
| --- | --- | --- |
| `--border-radius-4` | 4px | ボタン xs、フォーカスリング |
| `--border-radius-6` | 6px | ボタン sm |
| `--border-radius-8` | 8px | ボタン md/lg、入力欄、モーダル |
| `--border-radius-12` | 12px | カード・パネル |
| `--border-radius-16` | 16px | 大きい面 |
| `--border-radius-24` / `-32` | 24px / 32px | 大きい面 |
| `--border-radius-full` | 624.9375rem | 丸(チップ等) |

## エレベーション

| トークン | 値 |
| --- | --- |
| `--elevation-1` | `0 2px 8px 1px rgba(0,0,0,.1), 0 1px 5px 0 rgba(0,0,0,.3)` |
| `--elevation-2` | `0 2px 12px 2px rgba(0,0,0,.1), 0 1px 6px 0 rgba(0,0,0,.3)` |
| `--elevation-3` | `0 4px 16px 3px rgba(0,0,0,.1), 0 1px 6px 0 rgba(0,0,0,.3)` |
| `--elevation-4` | `0 6px 20px 4px rgba(0,0,0,.1), 0 2px 6px 0 rgba(0,0,0,.3)` |
| `--elevation-5`〜`-8` | さらに大きい 4 段(5: `8px 24px 5px` / 6: `10px 30px 6px` / 7: `12px 36px 7px` / 8: `14px 40px 7px`) |

モーダルは `--elevation-3` を使う。影はコントラストを担保しない。高さを持つ要素には**ボーダーを併用**する
(強制カラーモードで影が消えるため)。ダークテーマのエレベーションは DADS に定義がない。

## レイアウト

- ブレークポイントは 1 つ: **768px**(48rem)。モバイル・タブレット <768 / デスクトップ ≥768。
- 12 カラム。ガターは本文サイズの 2 倍 = 32px。1/2/3/4 カラムとオフセットを使う。
- リキッドレイアウト。横スクロールが出る場合はスクロールバーを隠さない。

## 既存の CSS 変数との対応

`frontend/src/styles.css` の変数はそのまま使い、DADS の値を必要とする箇所だけ個別に指定する。

| 既存 | 値 | コントラスト | DADS での扱い |
| --- | --- | --- | --- |
| `--bg` | `#f6f5f2` | — | ページ背景。白背景前提のコントラスト計算は `--card` で行う |
| `--fg` | `#1d1d1f` | 15.4:1(対 `--bg`) | 本文。gray-900 相当 |
| `--muted` | `#6b6b70` | 4.86:1(対 `--bg`) | 補助文字。4.5:1 を満たす |
| `--card` | `#ffffff` | — | 面 |
| `--border` | `#dddbd5` | **1.27:1**(対 `--bg`、白に対して 1.38:1) | 装飾の区切りだけ。操作要素の境界には gray-600 `#666666` を使う |
| `--accent` | `#2f6fdb` | 4.75:1(対 白) | キーカラー。文字・塗りとも 4.5:1 を満たす |
| `--done` | `#2e8b57` | — | success 系。文字に使うなら 4.5:1 を確認 |
| `--warn` | `#b5541c` | — | warning 系。同上 |

ダークテーマ(`#17171a` 背景)では、`--border` `#34343b` は 1.45:1 で境界に不足する。
境界には `#6b6b75`(3.39:1)以上を使う。
