`GOAL.md`、`CLAUDE.md`、`wiki/index.md`（および関連ページ）、現在のリポジトリ構造と `_参考/` のサンプル構成を確認し、「フェーズ 1 — スキャンとリーダー」の自律オーケストレーションを開始してください。

## 初期開発オーケストレーション規約（本セッションの進行ルール）

`CLAUDE.md` の単発作業向けサブエージェント制限を上書きし、本セッションでは**余っているクラウドセッションクレジット（Claude Code on the web / `claude --remote`）を優先活用するハイブリッド体制**で進行します。

### モデルおよびエフォート（Thinking Effort）の役割分担

| 役割 | 担当モデル | 推奨エフォート | 主な狙い・選定理由 |
| --- | --- | :---: | --- |
| **全体統括・オーケストレーター**（親） | **Opus** | **中 (Medium)** | 思考の空回りを防ぎ、状況把握・Issue 起票・進行判断・git 同期をテンポよく回すため |
| **実装サブエージェント**（子 / クラウド・ローカル共通） | **Sonnet 5.5** | **高 (High)** | 安全検証（Zip Slip/Bomb）・文字コード（UTF-8/cp932）・キャッシュ等のコーナーケース抜けを防ぎ、`agy-review` の 1 発 APPROVE と差し戻し時の確実な原因特定・再現テスト作成を行うため |

### 1. 段階的 Issue 化と実行環境（`[Cloud]` / `[Local]`）の振り分け

- 全体計画は `GOAL.md` を正とします。最初から全機能を細分化せず、現在のコードベースと `wiki/` を踏まえて**直近の 1〜3 タスクだけ**を `gh issue create` で起票してください（重複を防ぐため先に `gh issue list --state open` を確認）。
- Issue 本文には「`GOAL.md` のどの段階のどの完了条件に向かう作業か」、対象ファイル（新規作成含む）、受け入れ基準に加え、**実行環境区分（`[Cloud]` または `[Local]`）**を明記してください。
- **タスクは可能な限り `[Cloud]` として切り出し、クラウドセッションクレジットを優先消費します。**

| 区分 | 対象タスク（振り分け基準） |
| --- | --- |
| **`[Cloud]`**<br>（クラウドセッション優先） | ローカル専用の `_参考/サンプル本`（git 管理外）や Windows の `7z.exe` 実機が無くても、Python 標準ライブラリ（`sqlite3`・`zipfile` 等）・動的生成したテスト用 ZIP・モックだけで実装と単体テストが完結するタスク。<br>・SQLite スキーマ・マイグレーション・リポジトリ層・FTS5 trigram 検索・読書位置/履歴管理<br>・Zip Slip（`..`・絶対パス・ドライブ文字）/ Zip Bomb（サイズ・エントリ数・入れ子深さ上限）検証と、展開キャッシュのハッシュ名・LRU 容量上限管理<br>・`cover.jpg` 等を含むページ並び順（自然順＋表紙規則）や、ファイル名・フォルダ名からの作品名・著者・巻数・話数・ローマ字/日本語正規化パーサ<br>・FastAPI エンドポイント骨格・設定管理・オフラインフォルダ保持ロジック（テスト用生成 ZIP による API テストを含む）<br>・React + Vite フロントエンド実装（蔵書一覧 UI、右綴じ・見開き計算・表紙単独切替・先読みプリロード等のリーダーコンポーネント）<br>・公開 DB（国立国会図書館サーチ API / AniList / MangaUpdates 等）のクライアント・優先順位マージ・手動編集保護ロジック（モックテストつき）<br>・スコープ外で起票された独立 Issue（例: `#6` など） |
| **`[Local]`**<br>（ローカル Sonnet 委譲） | ローカル開発機にしか無い環境・データが実装中に必須となるタスク。<br>・Windows の `7z.exe` CLI 呼び出しラッパーと、`_参考/サンプル本` の実アーカイブ（7z / RAR 入れ子・Shift_JIS 混在実データ）を直接読ませて調整する結合処理<br>・実ブラウザ（モバイル幅 375px 相当を含む）での表示・操作確認と E2E 通し検証 |

### 2. `[Cloud]` タスクの実行とローカル引き取り手順

クラウド VM には `_参考/`・Windows `7z.exe`・ローカル認証済みの `agy` CLI（`review/agy_review.py`）が存在しないため、**「実装〜ブランチ push」をクラウドセッションで行い、「実データ/ブラウザ確認〜PR 作成・ローカル CI・`agy-review`・マージ」をローカルで引き取ります。**

1. **【クラウド側】実装・単体テスト・Wiki 更新・ブランチ push**
   - `claude --remote` が利用可能なら CLI から起動し、Web UI から投入する場合は対象 Issue 番号と末尾の「クラウドセッション投入用プロンプト」を提示して実行します（変更ファイルが重ならない独立した `[Cloud]` Issue であれば、複数クラウドセッションへ並列投入してクレジットを消化して構いません）。
   - クラウドセッションは `origin/main` からブランチ（`feature/issue-<番号>-<短い名前>` 等）を切り、実装・テスト追加・`pytest` / `node --test` パス・`wiki/` 更新（`wiki-ingest`）・`node scripts/wiki-lint.js` パスまで行い、`git push -u origin <branch>` で完了します（PR 作成・`agy_review.py`・マージはクラウド上では行いません）。
2. **【ローカル側】引き取り・実機検証・PR・`agy-review`・マージ**
   - クラウドセッションの push 完了後、ローカル（Sonnet サブエージェントまたは親）が `git fetch origin` → `git worktree add ../SpineBox-<作業名> <branch>` でブランチを取り込みます。
   - ローカルで必要な検証（`_参考/サンプル本` との結合確認や、フロント変更時のブラウザ 375px 動作確認）を実施します。
   - `pr-finish` スキルに従い、`gh pr create` → `wiki/log.md` に PR 番号を記入して push → `python scripts/ci_local.py`（Actions 未起動時の `local/*` 付与）→ `python review/agy_review.py --pr <PR番号>` → `gh pr merge <PR番号> --merge` → リモートブランチ・worktree 削除 → **メインツリーで `git pull --ff-only`** → `gh issue close <番号> --comment` を実行します。
   - `agy-review` で妥当な差し戻しが出た場合、軽微な修正はローカルの worktree でそのまま直し、大規模な修正は該当クラウドセッションで修正・再 push させます。

### 3. `[Local]` タスクの実行手順（ローカル Sonnet サブエージェント直列委譲）

- `[Local]` 区分の Issue は、ローカルの **Sonnet 5.5 サブエージェント（エフォート: 高 / High）** を起動し、`worktree-start` → 実装・テスト → `wiki-ingest` → CI・`agy-review` → `pr-finish`（PR マージ・リモートブランチ削除・worktree 削除・`gh issue close <番号> --comment`）までを 1 Issue 単位で完遂させます。
- **複数 Issue のまとめ委譲は禁止**です。ローカルの Sonnet サブエージェントへの委譲プロンプトには以下の必須指示を含めてください：
  - **サブエージェント自身による再委譲の禁止**
  - 着手時に `CLAUDE.md`・`GOAL.md`・`wiki/index.md`・`wiki/concepts/recurring-review-findings.md` および各スキル（`.claude/skills/` 配下）を読むこと
  - **メインツリー（`C:/Users/kntat/Documents/Git/SpineBox`）では一切ファイルを編集・コミットせず**、`git worktree add ../SpineBox-<作業名> -b <branch> origin/main` で切った専用 worktree 内で作業すること
  - 動作確認用の蔵書サンプルはメインツリーの `C:/Users/kntat/Documents/Git/SpineBox/_参考/サンプル本` にあること（git 管理外のため worktree には複製されない。テストや検証ではこのパスを参照し、元ファイルには一切書き込まないこと）
  - GitHub Actions が課金・上限でジョブを起動しない（「The job was not started」）場合は、push 後に `python scripts/ci_local.py` でローカル CI ステータス（`local/*`）を載せて代替すること（実際に動いて落ちたチェックは代替不可）

### 4. 役割分担と次サイクル判断（メインツリーの同期）

- コードレビューと品質検証は `agy-review` と CI に委ね、あなた（Opus）自身による二重のコードレビューは行わないでください。
- 1 Issue の PR マージ・Issue クローズ・worktree 削除を確認したら、**必ずメインツリーで `git pull --ff-only` を実行して最新のコードと `wiki/` を手元に反映したうえで**、次の Issue の起票・振り分けへ進みます。

### 5. エスカレーション・一時停止基準

- 以下のいずれかに該当した場合は、推測や強行突破で次へ進めず、一時停止して状況・選択肢・推奨案を私に報告してください：
  - `GOAL.md` の前提が崩れる問題、解釈で成果物が大きく変わる仕様判断、または未決事項の決定が必要になった場合（設定値として外出しにして `GOAL.md` の初期既定値で進められる部分はそのまま進めて構いません）
  - CI 失敗が解消できない場合、`agy-review` がエラー（exit 1）で失敗した場合、または `CRITICAL` の「未確認」指摘が残ってマージが差し止められた場合
  - サブエージェントやクラウドセッションがエラーで中断した場合

### 6. フェーズ完了時の通し検証

- フェーズ 1 の全 Issue が完了したら、最後にローカル環境で `GOAL.md` の「フェーズ 1 — スキャンとリーダー」の全完了条件（`_参考/` のサンプル全 10 巻の一覧表示、375px スマホ幅での 1 ページ目〜最終ページ閲覧、内側アーカイブ無展開テスト、元ファイル 1 バイト不変テスト、キャッシュ上限テスト、`python scripts/ci_local.py --no-report` パス）を通しで確認し、結果を報告してください。

---

## 付録: クラウドセッション投入用プロンプトテンプレート

`[Cloud]` 区分の Issue を Claude Code クラウドセッション（Web / `claude --remote`）に投げるときは、**モデルに Sonnet 5.5（または 3.7 Sonnet）、エフォート: 高（High）** を指定し、以下のテンプレートを使用してください。

```markdown
Issue #<番号> を実装してください。
※ モデル: Sonnet 5.5 (または 3.7 Sonnet) / エフォート: 高 (High) で実行してください。

1. 着手前に `CLAUDE.md`、`GOAL.md`、`wiki/index.md`、`wiki/concepts/recurring-review-findings.md`、`.claude/skills/wiki-ingest/SKILL.md` を読み、不変条件（特に「蔵書を扱う不変条件」）を確認してください。
2. `gh issue view <番号>`（gh が使えない環境ではプロンプト記載の受け入れ基準）を確認し、`origin/main` から `<feature または fix>/issue-<番号>-<短い名前>` ブランチを作成して作業してください。
3. 実装と単体テスト（クラウド環境には `_参考/` や `7z.exe` 実機が無いため、Python 標準の `zipfile` や一時ディレクトリ・インメモリ SQLite・モックで自己完結するテスト）を追加し、`pytest` がすべてパスすることを確認してください。
4. `.claude/skills/wiki-ingest/SKILL.md` に従って `wiki/` の該当ページ（および `wiki/index.md`・`wiki/log.md`）を更新し、コミット後に `node scripts/wiki-lint.js` が通ることを確認してください（`wiki/log.md` の PR 番号はローカル側で PR 作成後に記入するため省略して構いません）。
5. `git add` はファイルを明示指定し（`-A` / `.` 禁止）、`git push -u origin <ブランチ名>` まで実行して完了報告してください。
   ※ PR 作成・`ci_local.py`・`agy_review.py`・マージはローカル環境側で引き取って実行するため、クラウドセッション内では実行しないでください。
```
