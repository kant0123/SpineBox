---
type: operation
summary: Actions が課金・上限でジョブを起動しないとき、CI を手元で回して結果を commit status で PR に載せる手順と、代替を認める条件。Actions の起動条件(main への push と PR だけ)と、CI が遅いときの見つけ方
updated: 2026-11-25
related: [overview, frontend]
---

# ローカル CI

GitHub Actions がジョブを起動しない(課金・使用上限)ときに、同じ検査を手元で回して PR の CI を代替する。

## なぜこの形か

- **2026-11-01 の最初の push で、`test`・`wiki-lint` が起動せずに failure になった。** 注記は
  「The job was not started because recent account payments have failed or your spending limit needs to be increased.」。
  コードの問題ではない。`review/gemini_review.py` は CI が緑でないとレビューを拒否するので、PR → CI → レビュー → マージが止まる。
- **結果は commit status(`local/test`・`local/wiki-lint`)として head に載せる。** commit status の API は Actions を
  使わないので課金と無関係に書け、`gh pr checks` にも出る。PR コメントで「通った」と書くだけだと、どのコミットの
  結果かが機械的に分からない。
- **代替を認めるのは「起動しなかった」チェックだけ**(`review/gemini_review.py` `excused_by_local_ci()`)。
  Actions で実際に動いて落ちたチェックをローカルの成功で上書きすると、環境差で落ちている不具合を見逃す。
  同名の実行が複数付きうる(main への push と PR、取り消し前後など)ので、1 つでも実際に落ちていれば代替しない。
- **検査の中身は `scripts/ci_local.py` `jobs()` が正。** `.github/workflows/test.yml` もこれを `--no-report` で呼ぶ。
  二箇所に書くと、ローカルでは通るのに Actions では違うものを見ている状態が生まれる。
  ジョブ名は Actions の `jobs:` 直下のキーと一致させる(`local/<名前>` で対応づけるため。`tests/test_ci_local.py` が検査する)。
  `wiki-lint` だけは `.github/workflows/wiki-lint.yml` が直接 `node scripts/wiki-lint.js` を呼ぶ(同じコマンド)。

- **Actions の `tests` は main への push と pull_request だけで起動する**(`.github/workflows/test.yml`)。
  以前は全ブランチの push でも起動し、PR ブランチでは同じ検査が 2 回走って使用量が倍になっていた(#369)。
  同じ ref で新しく push されると古い head の実行は `concurrency` で取り消す。レビューが見るのは最新 head の
  チェックだけなので、取り消された実行は判定に残らない。PR を作る前のブランチは CI が走らないので、
  確かめたいときは `--no-report` で手元で回す。

## CI の時間

`tests` ジョブはほぼ全部が pytest。1 テストだけ 30 秒・60 秒ちょうどかかっていたら、SQLite のロック待ちで
`spinebox/db.py` `connect()` の `timeout=30` に達している。#369 ではテスト補助関数が `BEGIN IMMEDIATE` の中で
API を呼び、API 側の検索索引の追いつき(書き込み)がそのロックを待っていた(4 テストで約 210 秒、1 回 6 分の CI の 6 割)。
**テストで DB に直接書くときは、API 呼び出しをトランザクションの外へ出す。**
遅いテストは Actions のログの各行の時刻の差で見つかる(`gh run view <id> --log`)。

## 前提

- `python` に pytest、`node`・`npm` が入っていること。`gh` が認証済みであること。
  `frontend/node_modules` が無ければ `test` ジョブが `npm ci` から始める(初回はネットワークが要る。[[frontend]])。
- hook のテスト(`.claude/hooks/test/command_match.test.mjs`)は `check_*.sh` を bash で起動する。
  Windows の PowerShell は PATH に Git の `cmd` しか無く bash.exe が見えないことがあり、素の `spawnSync('bash')` は
  ENOENT(status null)で 8 件落ちていた(#6)。`.claude/hooks/test/find_bash.mjs:33 findBash()` が `where git` の
  場所から Git 同梱の bash.exe を探すので、PATH を直さなくてよい。Windows では PATH 上の bash より Git の bash.exe を
  優先する(`System32` の bash.exe は WSL のもので、Windows のパスを渡す hook は動かない)。Linux・macOS は `bash` のまま。
- worktree の変更をすべてコミットし、push 済みであること。未コミット・未追跡のファイルがある、または HEAD が
  リモートに無いときは、結果が head のものだと言えないので何も載せずに exit 2 で終わる。

## 手順

```bash
python scripts/ci_local.py               # 全ジョブを実行して local/* を載せる
gh pr checks <PR番号>                    # local/test・local/wiki-lint が pass
python review/gemini_review.py --pr <PR番号>  # 「ローカル CI の成功で代替」と出ればよい
```

手元で確かめるだけなら `python scripts/ci_local.py --no-report`(GitHub に何も載せない)。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| exit 2「未コミットの変更か未追跡のファイル」 | `git status`。生成物なら `.gitignore`、作業中ならコミットする |
| exit 2「HEAD がまだ push されていません」 | `git push` してから実行する |
| `local/*` は pass なのに Gemini レビューが CI 未達と言う | Actions のチェックが「起動しなかった」以外の理由で落ちていないか(`gh run view <id>`)。実際に落ちているなら直す |
| node が `Cannot read package config \\?\UNC\<host>\package.json` で落ちる | cwd が UNC になっている。ネットワークドライブ上では `Path.resolve()` が割り当てドライブを UNC に展開するので、`scripts/ci_local.py` の `ROOT` は `os.path.abspath` で求めている(テンプレート #63)。同じ形のコードを足さない |
| status を載せられない(gh api の失敗) | `gh auth status`。private リポジトリへの書き込み権限があるか |
| Actions が動くようになったら、ローカルでは通っていたテストが落ちた | 環境差。開発機(Windows)と ubuntu ランナーでは PATH 上のツール(7-Zip など)が違う。ローカル代替の期間に入ったテストは、外部ツールの有無を暗黙に前提にしていないか見直す(#34: 7z が PATH にある ubuntu で「7z 未対応」を期待するテストが落ちた) |

## 実施記録

| 日付 | 実施者 | 結果 |
| --- | --- | --- |
| 2026-11-01 | #1 の PR | 導入。Actions は課金で未起動のまま、ローカル CI で代替してレビューした |
| 2026-11-01 | PR #32 | Actions が再び起動し、ローカル代替の期間に入ったテストの環境差(#34)が表に出た |
