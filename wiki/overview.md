---
type: concept
summary: システム全体像と各ページへの入口
updated: 2026-11-01
related: [recurring-review-findings, local-ci, frontend, backend, archive-reading, extraction-cache, server-setup, volume-split, organize]
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
- [[extraction-cache]] — 内側アーカイブを開いたときだけ使う、ハッシュ名・容量上限つきの展開キャッシュ
- [[organize]] — `spinebox/organize/`。整理処理(1 ソース → 巻ごとの CBZ)。蔵書に書き込む唯一の経路で、プレビュー → 実行 → 元に戻す
- [[volume-split]] — 内側アーカイブを開いたときに中の巻へ分解し、名前の巻範囲と中身の食い違いを出す。名前から読む巻番号は候補
- [[frontend]] — `frontend/`。蔵書一覧と右綴じ・見開きリーダー(React + Vite)。ビルド結果をバックエンドが `/` で配信する

## 主要な設計判断

- 蔵書に書き込むのは整理処理([[organize]])だけ。同じドライブの作業場で全巻を作って検証してから改名で確定し、元は退避フォルダへ移すだけで消さない
- それ以外は蔵書を読み取り専用で扱う。スキャンは目次だけ読み、内側アーカイブは巻を開いたときだけ [[extraction-cache]] に取り出す
- 見えなくなった蔵書のレコード・読書位置は消さず、offline / missing の印を付けるだけ([[backend]])
- エントリ名はディスク上のパスに使わない。名前は生バイト列と使った文字コードごと残す([[archive-reading]])

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
