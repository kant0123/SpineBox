---
name: pr-finish
description: >
  実装が終わってから PR を作り、CI を通し、マージして後始末するまでの完了手順。
  テスト実行、push、gh pr create の本文テンプレート、CI 確認、マージ、
  リモートブランチと worktree の削除、Issue クローズまでを一続きで行う。
  「PR を作って」「マージして」「終わらせて」と言われたときや、
  worktree での実装と wiki 更新が済んだときに使う。
  --fill / Fixes / --delete-branch を使わない理由など、素直にやると失敗する罠がまとまっている。
---

# 完了 — PR から Issue クローズまで

`gh` の既定の使い方(`--fill`、`Fixes #`、`--delete-branch`)は**worktree ベースの運用では
順に壊れる**。理由は各手順に書いた。

## 前提の確認

始める前に、worktree の中にいて、実装と Wiki 更新が済んでいること。

```bash
python scripts/ci_local.py --no-report   # CI と同じ検査
git status
```

- `git status` は**他セッションの変更が混ざっていないか**を見るため。混ざっていたら
  自分の変更だけをファイル明示で `git add` する(`-A` / `.` は使わない)。
- スキーマ変更があるなら、マイグレーションが含まれていて既存 DB がそのまま起動できることを確認する。
- フロントを触ったなら実際に起動してブラウザで動作確認する。
- **`wiki/concepts/recurring-review-findings.md` の「push 前に確かめること」を、自分の差分に対して
  1 つずつ確かめる。** レビューで繰り返し差し戻された誤りの種類で、ここで気付けば差し戻しが 1 周減る。
- **PR 本文に書くテスト件数・実行結果は、実行したコマンドの出力をそのまま転記する。**
  記憶や前回の値で書かない(件数がずれても気づけない)。

## 1. push

```bash
git push -u origin <branch>
```

## 2. PR を作る

```bash
gh pr create --title "<一文>" --body "<下記テンプレート>"
```

**`--fill` は使わない。** 本文をコミットメッセージから生成するため、規約が要求する
「対応 Issue」「Wiki」の節を満たせない。`--body` で明示する。

```markdown
## 対応 Issue

#<番号>

## 変更内容

<要約>

## Wiki

<更新したページ、または更新不要と判断した理由を一行>

## レビュー

- agy: 【未記入: マージ前に手順 4 の結果で書き換える】
- 起票: 【未記入】
```

「レビュー」節は作成時点では上の【未記入】のままにする。**「未実施」「あとで実施する」とは書かない**
(書くと、レビューを後回しにしてよいように読める)。
手順 4 の結果(`agy-review` の「PR 本文に書くこと」の書式)で書き換え、【未記入】が残っていないことを
マージ前に確かめる。

- **`Fixes #<番号>` / `Closes #<番号>` を書かない。** クロージングキーワードがあると
  マージ時に GitHub が Issue を自動クローズし、後段の `gh issue close --comment` が
  `already closed` で失敗する。コマンド全体が中断するので `--comment` も投稿されず、
  **実装内容の要約が Issue に残らない**。素の `#<番号>` 参照に留める。
- `wiki/` に差分が無いと PreToolUse hook がブロックする。意図的なら理由を本文に書いた上で
  `WIKI_SKIP=1 gh pr create ...` で再実行する。

PR 番号が決まったら、`wiki/log.md` のエントリに `PR #<番号>` を埋めて追いコミット・push する。

**PR を作っただけでは作業は終わっていない。** 手順 3(CI)→ 4(agy レビュー)→ 5(マージ)→
6(後始末)→ 7(Issue クローズ)まで、同じターンで続ける。途中で止めてよいのは、CI の失敗が直せないときと
未確認の CRITICAL が残るときだけで、その場合は理由を報告する。
**「CI が緑になったら声をかけてください」と言って止めない。** 待つ手段は手順 3 にある。

## 3. CI を待つ

CI の完了は**自分で待つ**。デスクトップアプリ等の実行環境が「CI をポーリングしない」と指示していても、
その指示が禁じているのは短い間隔での繰り返し確認で、このリポジトリでは CI 成功の確認が完了の定義に入っている。
次の**1 回きりの待機コマンド**を、バックグラウンド実行(Bash の `run_in_background`)で走らせる。
終了すると呼び出し元に通知が戻るので、ポーリングにならず、止まらずに手順 4 へ進める。

```bash
gh pr checks <PR番号> --watch
```

終了コードが 0 なら全件成功。それ以外は失敗(または未起動)として下へ。
Auto-fix が有効でも、CI の**成功**では通知が来ない。成功の確認は上の待機で自分で行う。

失敗したらマージせず原因を調べる。修正が困難なら PR は開いたまま状況を報告する
(勝手に close しない)。

### Actions がジョブを起動しないとき

チェックが即座に失敗し、注記が「The job was not started because recent account payments have failed
or your spending limit needs to be increased.」なら、コードではなく Actions の課金・上限の問題。
同じ検査を手元で回し、結果を head のコミットに載せて代替する。

```bash
git status                       # 未コミット・未追跡が無いこと(あると載せない)
python scripts/ci_local.py       # test・wiki-lint を実行し、local/test・local/wiki-lint を載せる
gh pr checks <PR番号>            # local/* が pass になっていることを確かめる
```

- 載るのは**実行したときの HEAD**。push 前・未コミットの変更があるときは前提エラー(exit 2)で何も載せない。
- **代替できるのは「起動しなかった」チェックだけ。** Actions で実際に動いて落ちたなら、ローカルが通っても
  直すのが先(`review/agy_review.py` も代替を認めない)。環境差が疑わしいなら、その旨を報告する。
- 修正を push したら head が変わるので、ローカル CI もやり直す。

## 4. agy レビュー

**CI が緑になってから** `agy-review` スキルに従う。詳しい手順はそちらにある。

```bash
python review/agy_review.py --pr <PR番号>
```

- APPROVE → 手順 5 へ。
- 差し戻し → 指摘を評価する。妥当な指摘があれば直す場所まで戻って修正し、
  push → 手順 3(CI)→ 手順 4(再レビュー)を繰り返す。
  妥当と判定するのは成立を確かめた指摘だけ。確かめる手段が無い指摘は「未確認」として直さない。
  すべて誤検知・スコープ外・未確認(WARNING)なら起票して `--triage` で評価を記録し、手順 5 へ。
  未確認の CRITICAL が残るならマージせず、状況を人間に報告する(hook も差し止める)。

レビュー記録の無いマージは `gh pr merge` の hook が差し止める。

## 5. マージ

```bash
gh pr merge <PR番号> --merge
```

**`--delete-branch` を付けない。** worktree 内で実行すると `gh` がマージ後に `main` へ
checkout しようとして失敗する(メインツリーが `main` を持っているため):

```
failed to run git: fatal: 'main' is already used by worktree at '...'
```

**このエラーが出ていてもマージ自体は成功している。** エラー終了のためリモートブランチ削除に
到達しないだけ。エラーメッセージを見てマージ失敗と誤読しないこと。確認は:

```bash
gh pr view <PR番号> --json state,mergedAt
```

## 6. 後始末

```bash
git push origin --delete <branch>
cd C:/Users/kntat/Documents/Git/SpineBox   # メインツリー
powershell -ExecutionPolicy Bypass -File scripts/worktree-cleanup.ps1 -Remove ../SpineBox-<作業名> -RemoveDirs
git branch -D <branch>
```

**worktree の削除はメインツリーに `cd` してから `scripts/worktree-cleanup.ps1 -Remove ../SpineBox-<作業名> -RemoveDirs` で実行する。**

- シェルの作業ディレクトリが削除対象の中にあると、Windows はプロセスのカレントディレクトリを削除できず必ず失敗する。
  `../SpineBox-<作業名>` は **worktree の中からでも自分自身に解決してしまう**ので、パスを見ただけでは気づけない。
- **`-Remove ../SpineBox-<作業名>` を必ず付ける。** `-Remove` を省いて `-RemoveDirs` だけ実行すると、稼働中の worktree は
  残骸ではないためスキップされ、何も消えないまま exit 0 で終わる。
- 素の `git worktree remove` は、ReadOnly 属性で失敗したり、`frontend/node_modules` 等のジャンクション(`ReparsePoint`)や
  未追跡ディレクトリがあると `.git` ファイルだけ消して親ディレクトリを残したりする。`worktree-cleanup.ps1` は
  ジャンクションをリンク先の中身を辿らずに解除し、ReadOnly を外してから削除し、残ったディレクトリも `-RemoveDirs` で掃除する。
- 失敗しても**手で `rm -rf` / `Remove-Item` しない**(ジャンクションのリンク先を巻き込む恐れがあり、git も working tree と
  認識しなくなって回復しない)。マージ前の worktree が絡むときは `-RemoveDirs` を外して中身を確かめてから実行する。

## 7. Issue をクローズする

```bash
gh issue close <番号> --comment "<実装内容の要約>"
```

既にクローズ済みだった場合は `gh issue comment <番号> --body "<要約>"` で要約だけ残す。
**クローズの成否にかかわらず、要約は必ず Issue に残す。**

## 事前承認の範囲

push・PR 作成・マージ・ブランチ削除・worktree 削除は `CLAUDE.md` が事前承認しており、
都度の確認は不要。CI 成功と agy レビューの完了を確認したら、マージしてよいかを改めて聞かない。

ただし**タスクが失敗・中断した場合は勝手にマージ / 削除せず、状況を報告する。**
worktree も残す(作業内容を失わないため)。

## 完了報告に含めるもの

- 変更内容の要約と、手動確認の結果
- 作業中に見つけてこの PR で直さないと決めた問題を Issue 化したなら、その番号
  (1 件も無ければ、確認済みであること自体を述べる)
