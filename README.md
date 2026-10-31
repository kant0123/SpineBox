# SpineBox

ローカルの自炊本(漫画中心・小説も)を管理し、ブラウザで読むための Web アプリ。日本語のみを想定する。

**開発中。** まだアプリのコードは無い。開発基盤は
[エージェントルール汎用テンプレート](https://github.com/kant0123/ai-coding-template) から導入した。

## 何を目指しているか

- 入れ子になったアーカイブ(7z の中の RAR の中のフォルダなど)を解凍せずに巻として認識し、そのまま読む
- ファイル名と公開データベースから、作品名・著者・巻数・種別・ジャンル・タグを補完し、シリーズ単位でまとめる
- 重複している巻を見つけ、ユーザーの確認のうえで整理する。蔵書の元ファイルは、ユーザーが明示実行する整理以外では一切変更しない

これから作るものの仕様は [Issues](https://github.com/kant0123/SpineBox/issues) に、実装済みの現状は [wiki/](wiki/overview.md) に置く。

## 開発する人へ

- エージェント向けの規約: [CLAUDE.md](CLAUDE.md)
- 開発の流れ(worktree・PR・CI・agy レビュー): [docs/development_workflow.md](docs/development_workflow.md)
- Wiki の運用: [docs/wiki_workflow.md](docs/wiki_workflow.md)
- agy レビューの仕組み: [review/README.md](review/README.md)

`_参考/` には動作確認用の蔵書サンプルを置く。git の管理外で、コミットしない。
