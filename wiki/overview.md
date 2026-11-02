---
type: concept
summary: システム全体像と各ページへの入口
updated: 2026-11-03
related: [recurring-review-findings, local-ci, frontend, backend, archive-reading, extraction-cache, server-setup, volume-split, organize, duplicate-detection, work-grouping, metadata-linking, metadata-clients, metadata-sources, novel-reader, work-search, inbox, cover-thumbnails, bookmarks, follow, data-jobs]
---

# SpineBox 全体像

> このページは Wiki の入口。**新しくこのプロジェクトに関わる人間 / エージェントが
> 最初に読む 1 枚**として維持する。詳細は各ページへリンクで逃がし、ここは俯瞰に徹する。

## これは何か

ローカルの自炊本(漫画中心・小説も)を管理し、ブラウザで読むための Web アプリ。日本語のみを想定する。
目的・作らないもの・段階計画と完了条件はリポジトリ直下の `GOAL.md` にある(これから作るものの基準。現状はこの Wiki)。

## 構成

開発基盤(エージェント規約・CI・agy レビュー・この Wiki)に加えて、アプリは次の構成要素から成る。
これから作るものの仕様は Issue に置く。実装したら、その PR でここに構成要素を足す。

- [[backend]] — `spinebox/`。FastAPI の単一プロセス。設定・SQLite・スキャン・閲覧 API。フロントと共有する API 契約もここ
- [[archive-reading]] — `spinebox/archive/`。アーカイブの目次読みと 1 エントリの読み出し(zip / cbz は標準 zipfile、7z / rar は 7-Zip CLI)
- [[extraction-cache]] — 内側アーカイブを開いたときだけ使う、ハッシュ名・容量上限つきの展開キャッシュと、7z / rar のページと目次を置く RAM 層(`memory_cache_limit_bytes`)
- [[organize]] — `spinebox/organize/`。整理処理(1 ソース → 巻ごとの CBZ と、命名テンプレートどおりの改名・移動)。蔵書に書き込む唯一の経路で、プレビュー → 実行 → 元に戻す
- [[inbox]] — 受け入れ。受け入れフォルダ → 同期で検出 → 作品・番号・重複・対象外の理由つきでプレビュー → 利用者が選んだものだけ承認で書庫へ(元は受け入れフォルダの退避へ移すだけ・履歴から元に戻せる)。確信度が低い名寄せ・読めない番号・重複は自動で決めない
- [[volume-split]] — 内側アーカイブを開いたときに中の巻へ分解し、名前の巻範囲と中身の食い違いを出す。名前から読む巻番号は候補
- [[bookmarks]] — 作品・巻に付ける印と一覧。整理・作品の統合で引き継ぎ、オフラインでも消さない
- [[cover-thumbnails]] — 作品カードの表紙。最小の巻番号の巻の 1 ページ目を縮小してハッシュ名で置く。一覧からは取り出しの要らない巻だけ作り、入れ子の巻は開いた後か明示操作で作る
- [[duplicate-detection]] — 別スキャンの重複候補。全ページの見た目の指紋(dHash)と巻番号の候補で判定し、利用者が却下できる。計算は明示操作だけ、蔵書は変えない
- [[metadata-clients]] — `spinebox/metadata/`。公開 DB(国立国会図書館サーチ・AniList・MangaUpdates)のクライアントと生応答の保存。結果は候補 + 確からしさ。取得元の癖・制限は [[metadata-sources]]。作品・巻への結線は [[metadata-linking]]
- [[work-grouping]] — 作品の名寄せ(`spinebox/works.py`)。巻の名前から作品を派生させ、別名・著者・統合の根拠を項目ごとの出所・手動フラグ・確信度つきで持つ。確信度の高い統合だけ自動、残りは確認待ち、欠け巻と重複を作品ページで見せる
- [[metadata-linking]] — `spinebox/linking.py`。公開 DB の情報を作品・巻へ結線する規則。連携の自動/確認待ちの基準、空欄だけを埋める・手動は不変、取得失敗の扱い、全文検索・絞り込みの保存場所
- [[follow]] — フォロー。フォローした作品の続刊・欠け(蔵書に無い巻番号)を国立国会図書館サーチの連携した組の書誌で調べて知らせる。入手はしない。確認済みは再確認でも消さない
- [[work-search]] — 作品の全文検索(FTS5 trigram、`spinebox/search.py`)と、著者・種別・ジャンル・タグ・読書状態の絞り込み。索引はトリガーの印で次の一覧の前に追いつく
- [[data-jobs]] — データ管理。スキャン・補完・表紙・解析・重複・名寄せ・受け入れの同期をジョブとして 1 本ずつ動かし(依頼は待ち行列)、中止と履歴(`jobs` 表)を持つ。整理処理はジョブに入れない
- [[frontend]] — `frontend/`。サイドバーと管理のタブ(Peach と同じ画面構成)、作品・巻の一覧と右綴じ・見開きリーダー(React + Vite)。ビルド結果をバックエンドが `/` で配信する
- [[novel-reader]] — 小説(EPUB・テキスト)の巻と縦書きリーダー。EPUB の HTML は信用せず、サーバーの許可リスト・ブラウザでの作り直し・CSP の 3 段で止める。読書位置は区切り+割合

## 主要な設計判断

- 蔵書に書き込むのは整理処理([[organize]])だけ。同じドライブの作業場で全巻を作って検証してから改名で確定し、元は退避フォルダへ移すだけで消さない
- それ以外は蔵書を読み取り専用で扱う。スキャンは目次だけ読み、内側アーカイブは巻を開いたときだけ [[extraction-cache]] に取り出す
- 見えなくなった蔵書のレコード・読書位置は消さず、offline / missing の印を付けるだけ([[backend]])
- 外部メタデータの取得はベストエフォート。失敗・タイムアウト・429 は例外にせず結果で返し、取得元ごとのレート制限と生応答の使い回しで取りに行く回数を抑える([[metadata-clients]])
- エントリ名はディスク上のパスに使わない。名前は生バイト列と使った文字コードごと残す([[archive-reading]])
- 作品は名前から派生させた候補で、手で直したもの・分けたもの・却下したものは再計算で戻さない。自動統合は確信度が高く手動が絡まないものだけ([[work-grouping]])

## 触る前に知っておくこと

- 蔵書を壊さないための不変条件は `CLAUDE.md`(および `.agent/rules/spinebox-invariants.md`)の「蔵書を扱う不変条件」にある。差分がこれに触れていないかを PR ごとに確かめる
- GOAL.md のフェーズ 1〜4 を一括で自律開発するときの進行規約(フェーズごとの通し検証と進捗の記録先、Opus・Sonnet 5.5 のエフォート指定、クラウドクレジット活用の `[Cloud]` / `[Local]` 振り分けと引き取り手順を含む)は `KICKOFF.md` にある
- agy レビューは分野 `spinebox`(`review/domain_invariants.json`)の観点で見る
- GitHub Actions は課金・上限でジョブが起動しないことがある。そのときの CI の代替は [[local-ci]]
- 動作確認用の蔵書サンプルは `_参考/` にある(git 管理外)
- レビューで繰り返し差し戻された誤りの種類は [[recurring-review-findings]]。着手時と push 前に読む

## 運用

サーバー機 `DESKTOP-80AKMUB`(Windows)で常駐させ、ほかの端末から LAN 越しにブラウザで使う単一ユーザーのアプリ。
開発機(`DESKTOP-R9O3S3K`)とは別マシン。CD はまだ無い。常駐(タスクスケジューラ)・LAN 公開(ファイアウォール)・
更新・ログの手順は [[server-setup]](`deploy/` のスクリプト。サーバー機での実機確認は #20 で待ち)。
