# AGENTS.md

このリポジトリの規約は **`CLAUDE.md` が正**。ツールが自動で読まない場合も、作業前に必ず読むこと
(蔵書を扱う不変条件・Git ワークフロー・完了の定義)。Claude Code は `CLAUDE.md` を自動で読む。

## 画面(UI)の作成・変更は DADS 準拠

**`frontend/` の画面を作る・直すときは、DADS(デジタル庁デザインシステムβ版 v2.18.2)準拠とする。**
スキル機構の有無にかかわらず、着手前に次を読む。

- `.agent/skills/dads-ui/SKILL.md` — 手順と、必ず守るルール
- `.agent/skills/dads-ui/references/tokens.md` — 色・タイポ・余白・角丸・影・768px と既存変数との対応
- `.agent/skills/dads-ui/references/components.md` — 画面要素 → DADS コンポーネントの対応
- `.agent/skills/dads-ui/references/checklist.md` — 検証(375px のブラウザ確認を含む)

Claude Code は `.claude/skills/dads-ui/` の同名スキルを自動で読み込む(内容は同じ)。
既存画面は触る範囲だけ準拠に寄せる。全画面の一括塗り替えはしない。

## ほかの規約

Git ワークフロー・Issue・Wiki・完了の定義は `CLAUDE.md` にある。要約だけここに置くと食い違うので
重複して書かない。
