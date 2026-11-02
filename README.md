# SpineBox

ローカルの自炊本(漫画中心・小説も)を管理し、家の中のどの端末からでもブラウザで読むための Web アプリ。
単一ユーザー・日本語のみ・LAN 内での利用を想定している(インターネットへは公開しない)。

目的・作らないもの・段階計画と完了条件は [GOAL.md](GOAL.md)。フェーズ 1〜4 の完了条件はすべて満たしている。
実装済みの現状は [wiki/](wiki/overview.md)、これから作るものは [Issues](https://github.com/kant0123/SpineBox/issues) にある。

## できること

- **入れ子のまま取り込む** — zip / cbz / rar / 7z と画像フォルダを、解凍せずに目次だけ読んで巻として一覧に出す。
  アーカイブの中のアーカイブ(7z の中の rar の中の zip など)は、開いたときや明示操作のときだけ取り出し、巻ごとに分解する。
  Shift_JIS 混在のファイル名や、拡張子と中身が違うアーカイブも読む
- **そのまま読む** — 右綴じ・見開き(表紙だけ単独)・先読み・スマホのタップ / スワイプ・続きから読む。
  EPUB とテキスト(UTF-8 / Shift_JIS / UTF-16、青空文庫のルビ)は縦書きで読む
- **作品単位で探す** — ローマ字名と日本語名の別スキャンも 1 つの作品にまとめ、欠けている巻を表示する。
  著者・タグ・ジャンル・種別・出版社・読書状態での絞り込みと全文検索。ブックマークとフォロー(持っていない続刊のお知らせ)
- **情報を補完する** — ファイル名と公開データベース(国立国会図書館サーチ・AniList・MangaUpdates)から作品名・著者・
  出版社・種別・ジャンル・タグを埋める。手で直した項目は上書きしない。確信度の低い候補は確認を求める
- **整理する** — 別スキャンの重複候補の表示、1 巻 = 1 CBZ への変換、テンプレートに沿った名前・フォルダへの整理、
  ファイル名の特殊記号・文字化けの整理。どれもプレビュー → 実行 → まとめて元に戻すができ、元のファイルは退避フォルダへ
  移すだけで消さない
- **受け入れる** — 受け入れフォルダに置いた本を同期で検出し、整理案をプレビューして、承認したものだけ書庫へ入れる
- **データ管理** — スキャン・情報の補完・表紙の作成・重複の調査などをジョブとして実行し、タスクセンターで進み具合と履歴を見る

画面は常に表示するサイドバー(ホーム・フォロー・漫画・小説・著者・タグ・出版社・ブックマーク・管理)と、
管理のタブ(統計・好み・データ管理・フォロー管理・タスクセンター)でできている。

## 蔵書を壊さないための約束

利用者が長年ためた蔵書を預かるので、次を守る([CLAUDE.md](CLAUDE.md) の「蔵書を扱う不変条件」)。

- スキャン・閲覧・情報の取得は読み取りだけ。蔵書に書き込むのは、利用者が明示的に実行した整理処理だけ
- 整理は同じドライブの作業場で作って検証してから改名で確定し、元は退避フォルダへ移すだけで削除しない
- アーカイブ内の名前は検証してから使い、展開には上限を設ける。展開キャッシュは消えてもよいもの
- 蔵書フォルダが一時的に見えなくても(外付けドライブ未接続など)、記録・手動で直した情報・読書位置は消さない

## サーバー機で動かす

Windows のサーバー機に常駐させ、LAN 内の別の端末からブラウザで開く。詳しい手順と仕組みは
[wiki/operations/server-setup.md](wiki/operations/server-setup.md)。

### 初回

1. Python 3.12・Node.js・Git・7-Zip を入れ、このリポジトリを置く
2. `config.example.toml` を `config.local.toml` にコピーして、蔵書フォルダ(`library_roots`)・待ち受け(`host` に
   サーバー機の LAN アドレス)・7-Zip のパス(`sevenzip_path`)などを書く
3. 依存を入れてフロントをビルドする

   ```powershell
   py -3.12 -m pip install -r requirements.txt
   npm --prefix frontend ci ; npm --prefix frontend run build
   ```

4. 管理者として開いた PowerShell で、リポジトリのフォルダから LAN の接続を許可し、常駐タスクを登録する

   ```powershell
   .\deploy\firewall_rule.ps1 -Action Add
   .\deploy\install_task.ps1 -Action Register -StartNow `
     -PythonPath "<py -3.12 -c 'import sys; print(sys.executable)' で表示されたパス>" `
     -ConfigPath "$PWD\config.local.toml" `
     -HealthUrl "http://<サーバー機の LAN アドレス>:8000/healthz" `
     -UserName "$env:USERDOMAIN\$env:USERNAME"
   ```

5. 別の端末から `http://<サーバー機の LAN アドレス>:8000/` を開く

### 再起動・更新

`deploy` フォルダのバッチをダブルクリックする(管理者権限を求められる)。

| バッチ | すること |
| --- | --- |
| `deploy\restart_spinebox.bat` | 常駐タスクを止めて起動し直し、応答を確かめる |
| `deploy\update_spinebox.bat` | 止める → `git pull` → Python の依存 → フロントのビルド → 起動し、新しいコミットで応答することを確かめる |

DB の変更は起動時に自動で入る。途中で失敗しても、最後に起動し直して止めたままにはしない。うまくいかないときは
`logs\supervisor.log` と `logs\spinebox-*.err.log` を見る。

## 開発する人へ

- 技術: Python 3.12 / FastAPI / SQLite(FTS5 trigram)/ React + Vite + TypeScript / 7-Zip CLI
- 開発用の起動: `py -3.12 -m spinebox`(設定は `config.local.toml` か環境変数 `SPINEBOX_CONFIG`)。
  フロントだけなら `npm --prefix frontend run dev:mock`(モック API)
- テスト: `py -3.12 scripts/ci_local.py --no-report`(CI と同じ検査)
- エージェント向けの規約: [CLAUDE.md](CLAUDE.md)、開発の流れ(worktree・PR・CI・agy レビュー):
  [docs/development_workflow.md](docs/development_workflow.md)、Wiki の運用: [docs/wiki_workflow.md](docs/wiki_workflow.md)、
  agy レビュー: [review/README.md](review/README.md)
- `_参考/` には動作確認用の蔵書サンプルを置く。git の管理外で、コミットしない

開発基盤は [エージェントルール汎用テンプレート](https://github.com/kant0123/ai-coding-template) から導入した。
