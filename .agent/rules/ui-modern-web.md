# 画面(UI)の作成・変更

> `CLAUDE.md` の「画面(UI)の作成・変更」節の写し。規約を変えるときは同じ PR でここも直す
> (片方だけ直すと食い違う)。手順は `.agent/skills/modern-web-guidance/SKILL.md`。

- **`frontend/` の画面を作る・直すときは、着手時に modern-web-guidance(GoogleChrome/modern-web-guidance)で
  該当するガイドを検索・取得し、それに沿って実装する。** 手順は `.agent/skills/modern-web-guidance/SKILL.md`
  (Claude Code は `.claude/skills/modern-web-guidance/`)。スキル機構のないツールは `AGENTS.md` から入り、`SKILL.md` を直接読む。
- **CLI は `DISABLE_TELEMETRY=1` を付けて実行する**(既定では検索語などが Google へ送られる)。検索語にプロジェクト固有の語を入れない。
- 既存画面は**触る範囲だけ**ガイドに寄せる。全画面の一括塗り替えはしない(今の値は `frontend/src/styles.css` が正)。
- DADS(デジタル庁デザインシステム)準拠の規約と `dads-ui` スキルは、modern-web-guidance と競合したため廃止した(#318)。
