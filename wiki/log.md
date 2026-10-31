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

## [2026-11-01] ingest | PR #4 ゴール定義ファイル GOAL.md を追加
- 更新: [[overview]]
- 備考: #3。目的・作らないもの・段階計画(フェーズ 0〜4)と完了条件・試作で確かめた前提・未決事項を GOAL.md に置いた。Wiki は現状、GOAL.md はこれから作るものの基準という分担。

## [2026-11-01] ingest | PR #7 GOAL.md・CLAUDE.md・KICKOFF.md をブラッシュアップし .agent/rules と同期
- 更新: [[overview]]
- 備考: #5。複数フォルダ指定とオフライン時の DB 保護、話数・横断検索、1 巻 = 1 CBZ 化の判断理由、公開 DB 併用時の優先順位、フェーズ 1 の初期既定値を GOAL.md に明記。CLAUDE.md・review/domain_invariants.json・.agent/rules/ を同期し、自律オーケストレーション用の KICKOFF.md を追加。

## [2026-11-01] ingest | PR #9 KICKOFF.md にクラウドセッションクレジットの活用箇所と受け渡し手順を明記
- 更新: [[overview]]
- 備考: #8。ローカルの実サンプル(_参考/)や 7z.exe・agy CLI が不要な純粋ロジック・DB・API・UI 実装を [Cloud] として優先切り出しし、ブランチ push までをクラウドセッションで行い、実機確認・PR 作成・ci_local.py・agy_review.py・マージをローカルで引き取るハイブリッド手順を KICKOFF.md に定義。

## [2026-11-01] ingest | PR #11 KICKOFF.md に Opus(中) と Sonnet(高) のモデル・エフォート指定を明記
- 更新: [[overview]]
- 備考: #10。オーケストレーター Opus（中）と実装サブエージェント Sonnet 5.5（高）の役割分担表を追加し、コーナーケース抜け防止とレビュー手戻り削減のため付録プロンプトにも Sonnet 5.5 / エフォート高の指定を明記。

## [2026-11-01] ingest | PR #13 KICKOFF.md を全フェーズ一括の自律開発向けにし、GOAL.md の未決事項を確定
- 更新: [[overview]]
- 備考: #12。フェーズ 1〜4 を順に進め、進捗は GOAL.md のチェックボックスと Issue を正とする。実機でしか確かめられない完了条件は question ラベルの Issue にして先へ進む。整理処理の実データ確認は _参考/ のコピーで行う。楽天ブックス API・Web 小説 API は使わない、命名テンプレートの既定は「<作品名>/<作品名> 第NN巻.cbz」と確定し、GOAL.md の初期既定に移した。

## [2026-11-01] ingest | PR #17 フロントエンド骨格(蔵書一覧と右綴じ・見開きリーダー)を追加
- 更新: [[overview]], [[local-ci]]
- 新規: [[frontend]]
- 備考: #15。API はモックで開発(#14 と並行)。ページ番号は 0 始まり、スワイプは右へ払うと次(Issue の「左スワイプ = 次」から変えた理由は [[frontend]])。ci_local.py の test ジョブにフロントのテストを足し、node_modules が無ければ npm ci から始める。

## [2026-11-01] ingest | バックエンド骨格(設定・DB・zip 目次読み・展開キャッシュ・スキャン・閲覧 API)
- 更新: [[overview]]
- 新規: [[backend]], [[archive-reading]], [[extraction-cache]]
- 備考: #14、PR #18。spinebox/ パッケージと tests/ を追加。スキャンは目次だけ読み内側アーカイブを取り出さない、蔵書のバイト列・mtime がスキャンと全ページ閲覧で変わらない、キャッシュの LRU をテストで確認。7z / rar は後続の [Local] Issue。

## [2026-11-01] ingest | PR #21 Windows 常駐起動・LAN 公開設定とビルド済みフロントの結合確認
- 更新: [[overview]], [[backend]], [[frontend]]
- 新規: [[server-setup]]
- 備考: #19。`/healthz`(稼働中コミット付き)、`deploy/` の常駐スクリプト 3 本、`/read/<id>` 直リンクの正準化。開発機ではタスク登録・ファイアウォール規則を実行しておらず(システム設定の変更)、サーバー機での確認は #20。再起動ループは通常プロセスで確かめた。

## [2026-11-01] ingest | PR #24 hook テストが Git の bin を PATH に持たない Windows で bash 未検出になる問題を修正
- 更新: [[local-ci]]
- 備考: #6。`command_match.test.mjs` の `spawnSync('bash')` が PowerShell(PATH に Git の cmd のみ)で ENOENT になり 8 件落ちていた。`find_bash.mjs` `findBash()` で git の場所から bash.exe を解決。PowerShell で修正前 53 件中 8 失敗 → 修正後 60 件(+find_bash 7 件)全成功を確認。

## [2026-11-01] ingest | PR #25 常駐ラッパーを止めても python の子プロセスが孤児として残らないようにする
- 更新: [[server-setup]], [[backend]]
- 備考: #23。ラッパー自身を kill-on-close の Job Object に入れて強制終了でも python が道連れになるようにし、起動前にポートを握る前回の孤児(`-m spinebox` で親が居ない)を検出して止める。親が生きているプロセスは止めない。`install_task.ps1 -Action Stop` を追加。`Stop-ScheduledTask` 単体の挙動はサーバー機で未確認(#20)。
