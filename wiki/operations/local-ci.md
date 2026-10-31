---
type: operation
summary: Actions が課金・上限でジョブを起動しないとき、CI を手元で回して結果を commit status で PR に載せる手順と、代替を認める条件
updated: 2026-11-01
related: [overview, frontend]
---

# ローカル CI

GitHub Actions がジョブを起動しない(課金・使用上限)ときに、同じ検査を手元で回して PR の CI を代替する。

## なぜこの形か

- **2026-11-01 の最初の push で、`test`・`wiki-lint` が起動せずに failure になった。** 注記は
  「The job was not started because recent account payments have failed or your spending limit needs to be increased.」。
  コードの問題ではない。`review/agy_review.py` は CI が緑でないとレビューを拒否するので、PR → CI → レビュー → マージが止まる。
- **結果は commit status(`local/test`・`local/wiki-lint`)として head に載せる。** commit status の API は Actions を
  使わないので課金と無関係に書け、`gh pr checks` にも出る。PR コメントで「通った」と書くだけだと、どのコミットの
  結果かが機械的に分からない。
- **代替を認めるのは「起動しなかった」チェックだけ**(`review/agy_review.py` `excused_by_local_ci()`)。
  Actions で実際に動いて落ちたチェックをローカルの成功で上書きすると、環境差で落ちている不具合を見逃す。
  push と pull_request で同名の実行が 2 つ付くので、片方でも実際に落ちていれば代替しない。
- **検査の中身は `scripts/ci_local.py` `jobs()` が正。** `.github/workflows/test.yml` もこれを `--no-report` で呼ぶ。
  二箇所に書くと、ローカルでは通るのに Actions では違うものを見ている状態が生まれる。
  ジョブ名は Actions の `jobs:` 直下のキーと一致させる(`local/<名前>` で対応づけるため。`tests/test_ci_local.py` が検査する)。
  `wiki-lint` だけは `.github/workflows/wiki-lint.yml` が直接 `node scripts/wiki-lint.js` を呼ぶ(同じコマンド)。

## 前提

- `python` に pytest、`node`・`npm` が入っていること。`gh` が認証済みであること。
  `frontend/node_modules` が無ければ `test` ジョブが `npm ci` から始める(初回はネットワークが要る。[[frontend]])。
- worktree の変更をすべてコミットし、push 済みであること。未コミット・未追跡のファイルがある、または HEAD が
  リモートに無いときは、結果が head のものだと言えないので何も載せずに exit 2 で終わる。

## 手順

```bash
python scripts/ci_local.py               # 全ジョブを実行して local/* を載せる
gh pr checks <PR番号>                    # local/test・local/wiki-lint が pass
python review/agy_review.py --pr <PR番号>  # 「ローカル CI の成功で代替」と出ればよい
```

手元で確かめるだけなら `python scripts/ci_local.py --no-report`(GitHub に何も載せない)。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| exit 2「未コミットの変更か未追跡のファイル」 | `git status`。生成物なら `.gitignore`、作業中ならコミットする |
| exit 2「HEAD がまだ push されていません」 | `git push` してから実行する |
| `local/*` は pass なのに agy レビューが CI 未達と言う | Actions のチェックが「起動しなかった」以外の理由で落ちていないか(`gh run view <id>`)。実際に落ちているなら直す |
| status を載せられない(gh api の失敗) | `gh auth status`。private リポジトリへの書き込み権限があるか |

## 実施記録

| 日付 | 実施者 | 結果 |
| --- | --- | --- |
| 2026-11-01 | #1 の PR | 導入。Actions は課金で未起動のまま、ローカル CI で代替してレビューした |
