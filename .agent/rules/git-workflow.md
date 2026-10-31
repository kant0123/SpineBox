# Git ワークフロー & 完了の定義

> `CLAUDE.md` の「Git ワークフロー」「完了の定義」「[オプション] agy レビュー」節の写し。
> 規約を変えるときは `CLAUDE.md` と同じ PR でここも直す(片方だけ直すと食い違う)。
> 手順は `.agent/skills/` の各スキル(`worktree-start` / `wiki-ingest` / `pr-finish` / `agy-review` / `file-issue`)。

## 常に守る基本規約

複数のエージェントセッションが同じリポジトリで同時に作業する前提。作業ツリーを共有すると
HEAD の奪い合いで自分のコミットが他人のブランチに乗るため、実装は必ず専用 worktree で行う。

1. **main へ直接 push・直接マージしない**:
   - 必ず PR を経由し、CI の成功(`gh pr checks`)を確認してから `--merge` でマージする。
2. **実装作業はメインツリーで行わない**:
   - `git worktree add ../SpineBox-<作業名> -b <branch> origin/main` で作った**リポジトリ外の**兄弟ディレクトリで作業する。
   - ブランチ名は `fix/` / `feature/` / `chore/` / `docs/` を前置し、**ローカル `main` ではなく `origin/main` から切る**。
3. **`git add` はファイルを明示指定する**:
   - `-A` / `.` は使わない。他セッションの未コミット変更が混ざりうるため、`git diff --cached` で確認してからコミットする。
4. **worktree の安全な後始末**:
   - 使い終わったら、メインツリーに `cd` してから `git worktree remove` で消す。
   - 失敗しても手で `rm -rf` / `Remove-Item` しない(git が working tree と認識しなくなり回復しない)。復旧は `scripts/worktree-cleanup.ps1`。
5. **事前承認済みの操作**:
   - push・PR 作成・マージ・ブランチ削除・worktree 削除は事前承認済み。`git status` で意図しない変更が混ざっていないか確認してから実行する。
   - CI 成功(agy レビューを採用しているならその完了)まで確認したら、マージしてよいかを改めて聞かない。
6. **失敗時はマージしない**:
   - CI が失敗したらマージせず原因を調べる。修正が困難なら PR は開いたまま報告する(勝手に close しない)。
   - Actions がジョブを起動しない(課金・上限。注記「The job was not started」)ときだけ、コミット・push 済みの状態で `python scripts/ci_local.py` を実行し、head に `local/*` を載せて代替する。Actions で実際に動いて落ちたチェックは代替しない。
   - タスクが失敗・中断した場合も勝手にマージ / 削除せず、worktree は残して報告する。
7. **PR / Issue での罠回避**:
   - `gh pr create --fill` は使わない(必須節が満たせないため `--body` で明示指定)。
   - `Fixes #` / `Closes #` を使わない(マージ時に自動クローズされると、後続の `gh issue close --comment` が失敗して要約が残らない)。素の `#<番号>` 参照に留める。
   - `gh pr merge --delete-branch` を使わない(worktree 内ではローカル main への checkout 失敗がマージ失敗と誤読される)。ブランチ削除は `git push origin --delete` で行う。

コミットメッセージは `feat:` / `fix:` / `docs:` / `chore:` プレフィックス + 概要。

## [オプション] agy レビュー

PR の CI が緑になった後、マージの前に、差分を agy (Antigravity CLI) 経由で Gemini に 1 回レビューさせる。
手順は `agy-review` スキル、仕組みは `review/README.md`。

- **レビューは必須。順序は push → CI → `python review/agy_review.py` → (差し戻しなら評価)→ マージ。**
  レビュー記録の無いマージは `gh pr merge` の hook が差し止める。
- **`gh pr merge` には必ず PR 番号を付ける**(hook は番号で PR を特定する)。
- **レビューは head SHA ごとに有効。** 修正を push したら CI から回し直す。
- **差し戻しは 1 件ずつ評価し、妥当な指摘が 1 件でもあれば修正する。** 誤検知と判定するなら成立しない理由を
  コードの場所つきで書き、上流の `kant0123/gemini-review` に `false-positive` で起票してから `--triage` で記録する。
  見逃し(通過した差分が原因の不具合)も `false-negative` で起票する。
- **妥当と判定するのは、成立を確かめた(再現・失敗するテスト・起きる経路のコード)指摘だけ。**
  確かめる手段が無い指摘は「未確認」として修正せず、このリポジトリに起票して `--triage` に書く。
  CRITICAL が未確認ならマージしない(merge hook も差し止める)。
- **妥当だった指摘は、誤りの種類として `wiki/concepts/recurring-review-findings.md` に修正と同じ PR で残す。**
  着手時と push 前にこのページを読む。
- **レビューが失敗(agy のエラー・空出力)したままマージしない。**
- **レポート(`review/work/`)をコミットしない。** 残す価値のある知見は `wiki/` に還元する。

## 完了の定義 (Definition of Done)

- 変更に関係するテストをローカルで実行し、パスを確認している。
- CI(`gh pr checks`)が全件パスしている(Actions が起動しなかったチェックは、同じ head の `local/<同名>` の成功で代替できる)。
- スキーマ変更がある場合、マイグレーションが含まれている。
- フロント変更は実装後にブラウザで動作検証している。
- `wiki/` の該当ページと `wiki/log.md` を同じ PR で更新している(更新不要と判断した場合は PR 本文にその理由を一行書いている)。
- Wiki を更新した場合、**コミット後に** `node scripts/wiki-lint.js` が通っている。
- 作業中に見つけてこの PR で直さないと決めた問題を、すべて Issue 化している(1 件も無ければ、それ自体を確認済みとする)。
- 変更内容の要約と手動確認結果を報告している。起票した Issue があれば番号を挙げている。
- agy レビューを採用している場合、PR の最新 head が agy レビューを通っている(APPROVE、または差し戻しを評価して誤検知を起票済み)。結果を PR 本文の「レビュー」節に書いている。
