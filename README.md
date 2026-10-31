# SpineBox

ローカルの自炊本(漫画中心・小説も)を管理し、ブラウザで読むための Web アプリ。日本語のみを想定する。

**開発中。** まだアプリのコードは無い。開発基盤は
[エージェントルール汎用テンプレート](https://github.com/kant0123/ai-coding-template) から導入した。

## 何を目指しているか

入れ子のアーカイブを解凍せずに巻として認識してそのまま読み、ファイル名と公開データベースから情報を補完して
作品単位で探せるようにする。目的・作らないもの・段階計画と完了条件は [GOAL.md](GOAL.md)。

これから作るものの仕様は [Issues](https://github.com/kant0123/SpineBox/issues) に、実装済みの現状は [wiki/](wiki/overview.md) に置く。

## 開発する人へ

- エージェント向けの規約: [CLAUDE.md](CLAUDE.md)
- 開発の流れ(worktree・PR・CI・agy レビュー): [docs/development_workflow.md](docs/development_workflow.md)
- Wiki の運用: [docs/wiki_workflow.md](docs/wiki_workflow.md)
- agy レビューの仕組み: [review/README.md](review/README.md)

`_参考/` には動作確認用の蔵書サンプルを置く。git の管理外で、コミットしない。
