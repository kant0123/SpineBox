# AGENTS.md

このリポジトリの規約は **`CLAUDE.md` が正**。ツールが自動で読まない場合も、作業前に必ず読むこと
(蔵書を扱う不変条件・Git ワークフロー・完了の定義)。Claude Code は `CLAUDE.md` を自動で読む。

## 画面(UI)の作成・変更は modern-web-guidance に従う

**`frontend/` の画面を作る・直すときは、着手時に modern-web-guidance で該当するガイドを検索・取得し、それに沿って実装する。**
スキル機構の有無にかかわらず、着手前に次を読む。

- `.agent/skills/modern-web-guidance/SKILL.md` — 検索・取得の手順(`npx modern-web-guidance`)と、SpineBox のローカル追記
  (利用状況の送信を止める `DISABLE_TELEMETRY=1`・既存画面を塗り替えない・このリポジトリの罠)

Claude Code は `.claude/skills/modern-web-guidance/` の同名スキルを自動で読み込む(内容は同じ)。
DADS(デジタル庁デザインシステム)準拠の規約と `dads-ui` スキルは廃止した(#318)。

## ほかの規約

Git ワークフロー・Issue・Wiki・完了の定義は `CLAUDE.md` にある。要約だけここに置くと食い違うので
重複して書かない。
