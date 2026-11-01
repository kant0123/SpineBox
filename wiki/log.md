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

## [2026-11-01] ingest | PR #22 フェーズ 1 の 7-Zip CLI 連携(7z / rar・内側アーカイブの遅延取り出し)と _参考 サンプル結合
- 更新: [[archive-reading]], [[extraction-cache]], [[backend]], [[overview]], [[server-setup]]
- 備考: #16。実データは 7z → rar → rar → zip と深さ 3 に入れ子になっていたため、`max_depth` の既定を 1 から 3 にして、内側アーカイブを再帰で開くようにした(Issue の想定は「rar の中に巻フォルダ」だった)。`_参考/サンプル本` でスキャンが 10 巻・キャッシュ空・全巻の全ページが 200・原本のサイズ/mtime/sha256 不変を確認。

## [2026-11-01] verify | フェーズ 1 通し検証
- 更新: なし(GOAL.md のチェックボックスのみ)
- 備考: #27、PR #28。main 696fdec で `py -3.12 scripts/ci_local.py --no-report` 成功(pytest 250 passed)、`SPINEBOX_SAMPLE_DIR` 指定の `tests/test_reference_samples.py` 成功。`_参考/サンプル本` を蔵書ルートにした実起動でスキャン 10 巻・直後のキャッシュ 0 件、375px の組み込みブラウザで入れ子 3 段の rar 巻を 1→472 ページまで読み既読保存、zip 巻の 4 ページ目から再開を確認。原本 2 ファイルのサイズ・mtime・sha256 は検証前後で一致。サーバー機での常駐・LAN 閲覧は人間確認待ち #20。初回オープン時に読み込み表示が無い点は #26。

## [2026-11-01] ingest | PR #35 7z の入った環境で落ちるテストを直す
- 更新: [[local-ci]]
- 備考: #34。`tests/test_zipreader.py` `test_unsupported_formats` が既定の `sevenzip_path` で「7z 未対応」を期待しており、7z が PATH にある Actions の ubuntu で落ちていた。存在しない 7-Zip のパスを明示して未対応を確かめる形にした。7z を PATH に入れた開発機で修正前の失敗を再現・修正後の成功を確認。

## [2026-11-01] ingest | PR #32 リーダーの読み込み待ち・取得失敗の表示
- 更新: [[frontend]]
- 備考: #26。巻情報の取得中にスピナーと一覧へ戻るリンク(1.5 秒超で補足)、ページ画像の読み込み中に重ね表示、取得失敗は 404 / 409 / 422 / 接続不可で文言を分けた。モック API に初回オープンの遅延と巻ごとの失敗を足した。ローカル引き取り時に `_参考/サンプル本`(キャッシュ空)で 375px の実ブラウザ確認: 初めて開く 7z 内 zip の巻で読み込み中→補足→表示、存在しない巻 ID でエラー文言と一覧へ戻るリンク。

## [2026-11-01] ingest | 巻への分解と名前の巻範囲の食い違い検出
- 更新: [[backend]], [[archive-reading]], [[extraction-cache]], [[frontend]], [[overview]], [[server-setup]](コード参照の行番号のみ)
- 新規: [[volume-split]]
- 備考: #30、PR #37。内側アーカイブの巻を開いたとき・`POST /api/volumes/{id}/analyze`・設定で有効にした裏の解析で中の巻の単位を子の巻として登録し、親は一覧から隠す。食い違いは欠け・余りだけ(重複は後続の重複検出に任せて出さない)。分解前の親の読書位置は親に残し、子へ写す。マイグレーション 2 と `SCAN_VERSION` 2。クラウドでは合成の入れ子 zip でテストし、ローカル引き取りで `_参考/サンプル本` を実起動して確認した(スキャン直後のキャッシュ 0 件、`Kakukaku Shikajika v01-03` を開くと v01 157・v02 156・v03 159 ページ(計 472)の 3 巻に分解して食い違い無し、`v04` は分解されず問題なし、お気楽領主の zip(v01-06、中は 1,1,2,2,3,4,5,6 巻)は食い違い無し)。375px の組み込みブラウザで子の巻を最終ページまで読んで既読保存、途中ページからの再開、合成データの欠け 2 巻のバッジと説明を確認。`tests/test_reference_samples.py` に分解の確認を足した。原本 2 ファイルのサイズ・mtime は確認の前後で一致。作業中に 7-Zip を PATH に入れた環境で落ちる既存テストを見つけて #33 に起票した。

## [2026-11-01] ingest | #31 整理処理(CBZ 化 → 検証 → 確定 → 退避 → 元に戻す)
- 新規: [[organize]]
- 更新: [[backend]], [[frontend]], [[overview]], [[volume-split]], [[server-setup]](コード参照の行番号), [[extraction-cache]](コード参照の行番号)
- 備考: #31、PR #39。マイグレーション 3(organize_ops / organize_steps / organize_volumes、volumes.organized_op_id。クラウドでは 2 番だったが #30 が先に入ったので付け替え)。設定 `organize_dir`(相対は各蔵書ルートが起点)。
  クラウドで合成の zip と一時ディレクトリのテストで、往復後に蔵書フォルダがパス・バイト列・mtime まで一致することを確認。
  ローカル引き取りで #30(巻への分解)に合わせた: 整理の単位は見えている子の巻を持たない巻(分解済みなら子ごとに 1 CBZ)、中の巻をまだ調べていない内側アーカイブは計画で拒否して `needs_analysis` を返し、画面に「中の巻を調べて計画を作り直す」を出す。
  `_参考/サンプル本` を同じドライブの一時ディレクトリへコピーして往復を確認(原本は処理対象にしていない): お気楽領主の zip は 8 CBZ、かくかくしかじかの 7z は v01・v02・v03(分解した子)+ v04 の 4 CBZ。全ページが元とバイト列で一致、再スキャン後に全 CBZ の巻が読め、読書位置が引き継がれ、元に戻すと蔵書がパス・sha256・サイズ・mtime まで一致。
  375px と横長の組み込みブラウザで、一覧 →「CBZ に整理」→ プレビュー(未解析なら調べてやり直し)→ 実行 → 履歴 → 元に戻す を確認。

## [2026-11-01] ingest | 別スキャンの重複候補の検出
- 更新: [[backend]], [[frontend]], [[volume-split]], [[extraction-cache]], [[archive-reading]], [[organize]], [[overview]], [[recurring-review-findings]]
- 新規: [[duplicate-detection]]
- 備考: #38、PR #40。全ページの dHash(Pillow)の集合と巻番号の候補で判定し、`POST /api/duplicates/scan`(明示操作、既定は裏で動かして進み具合を返す)でだけ指紋を作る。マイグレーション 4(#31 の 3 の後ろ。整理済みの巻は一覧の条件に揃えて対象外)。却下は `dismissed_at` を立てるだけで再計算しても復活せず、戻せる。Issue の「表紙〜先頭数ページと等間隔に数枚」は別スキャンでページ位置が合わないので全ページに変えた(根拠は [[duplicate-detection]])。実データ(`_参考/サンプル本`)でお気楽領主の 1・2 巻だけが候補(一致ページ比 0.989 / 0.973、それ以外の 64 組は最大 0.048)。375px と 812x375 の組み込みブラウザで画面・却下・戻す・一覧の印を確認、原本 2 ファイルのサイズ・mtime・sha256 は前後で一致。

## [2026-11-01] verify | PR #42 フェーズ 2 通し検証
- 更新: なし(GOAL.md のチェックボックスのみ)
- 備考: #41。main 80e077d で `py -3.12 scripts/ci_local.py --no-report` 成功(pytest 400 passed / vitest 61 passed / wiki-lint OK)。`_参考/サンプル本` を同じドライブの一時ディレクトリへコピーし、コピーを蔵書ルートにして実起動: スキャン 10 巻、`v01-03` を解析すると 1・2・3 巻(157/156/159 ページ)に分解され食い違い無し、`v04` も無し、お気楽領主の zip も無し(サンプルに食い違いが無い件は人間確認待ち #29)。重複検出は 12 巻を 17 秒で指紋化し、候補はお気楽領主 1 巻(186/188 一致)・2 巻(142/146)の 2 組だけ。かくかくしかじかの 7z を CBZ 4 つに整理 → 全 CBZ の最終ページ取得 → v02 の読書位置(40 ページ)が CBZ の巻へ引き継ぎ → 元に戻すとコピーの sha256 が実行前と一致・読書位置も元の巻へ、作成した CBZ は退避フォルダに残る。#31 引き取り時に報告された「スキャンボタンで一覧が更新されない」はブラウザで再現しなかった。原本 2 ファイルのサイズ・mtime・sha256 は検証前後で一致。

## [2026-11-02] ingest | 公開 DB クライアントと生応答の保存
- 更新: [[backend]], [[organize]](コード参照の行番号のみ), [[overview]], [[recurring-review-findings]]
- 新規: [[metadata-clients]], [[metadata-sources]]
- 備考: #44、PR #46。国立国会図書館サーチ(OpenSearch)・AniList・MangaUpdates のクライアント、候補ごとの確からしさ、生応答の保存(マイグレーション 5 `metadata_responses`)、取得元ごとのレート制限、`scripts/metadata_probe.py`。DB の作品・巻への結線はしていない(後続)。実通信(2026-11-02)で「お気楽領主の楽しい領地防衛」「かくかくしかじか」「Okiraku ryoshu no tanoshi ryochi」を 3 取得元で引き、応答を `tests/fixtures/metadata/` に記録した。実通信で分かった癖(MangaUpdates の perpage は 10 未満が無視される、AniList はローマ字の綴りが厳密、NDL は著者で絞ると巻が揃う)は [[metadata-sources]]。
