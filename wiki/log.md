# Log

Wiki に対して何を・いつ行ったかの追記専用ログ。**新しいエントリは末尾に足す**(既存行は書き換えない)。

各エントリは `## [YYYY-MM-DD] <種別> | <一行要約>` で始める。種別は以下の 3 つ。

| 種別 | 意味 |
| --- | --- |
| `ingest` | 実装・変更の内容を Wiki に反映した |
| `query` | 調査・分析の結果を Wiki のページとして還元した |
| `lint` | 健康診断を行い、矛盾・陳腐化・孤立ページを修正した |

直近の動きは次のコマンドで追える。

```bash
grep "^## \[" wiki/log.md | tail -5
```

---

<!-- 記入例(実際のエントリを追記したらこのコメントは削除してよい)

## [2026-08-11] ingest | PR #123 エピソード進捗の一括更新に対応
- 更新: [[episode-progress]], [[db-naming]]
- 新規: [[bulk-update-transaction]]
- 備考: 一括更新はトランザクション境界が従来と異なる。[[batch-updater]] からの相互リンクを追加。

## [2026-08-12] lint | 孤立ページと陳腐化した記述の整理
- 更新: [[index]], [[deploy]]
- 削除: なし
- 発見: [[legacy-import]] は被リンク 0。実装も削除済みのためページを削除した。

-->

## [2026-11-01] ingest | 開発基盤をテンプレートから導入
- 更新: [[overview]], [[recurring-review-findings]]
- 備考: kant0123/ai-coding-template (4666bb3) から導入。CI・wiki-lint・agy レビュー・Issue 連携を採用。Projects ボードは不採用、CD は未導入(本番はサーバー機 DESKTOP-80AKMUB)。
  テンプレート側の履歴エントリはこのリポジトリの出来事ではないので持ち込んでいない。台帳の実例はテンプレート側の PR 番号である旨を注記した。

## [2026-11-01] ingest | PR #2 Actions が起動しないときのローカル CI を追加
- 更新: [[overview]]
- 新規: [[local-ci]]
- 備考: #1。最初の push で Actions が課金・上限によりジョブを起動しなかった。scripts/ci_local.py が test・wiki-lint を手元で実行して commit status (local/*) を載せ、review/agy_review.py は「起動しなかった」チェックに限りそれで代替する。test.yml もこのスクリプトを呼ぶ形にし、これまで CI で実行されていなかった review/tests・hook のテストも走るようになった。

## [2026-11-01] ingest | PR #? ゴール定義ファイル GOAL.md を追加
- 更新: [[overview]]
- 備考: #3。目的・作らないもの・段階計画(フェーズ 0〜4)と完了条件・試作で確かめた前提・未決事項を GOAL.md に置いた。Wiki は現状、GOAL.md はこれから作るものの基準という分担。
