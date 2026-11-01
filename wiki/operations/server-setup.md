---
type: operation
summary: サーバー機(Windows)での初回セットアップ、タスクスケジューラへの常駐登録、LAN 公開(ファイアウォール)、更新手順、python を孤児にしない仕組み、ログの場所。開発機で確かめた範囲とサーバー機での確認待ちの範囲
updated: 2026-11-02
related: [overview, backend, frontend, archive-reading]
---

# サーバー機のセットアップと常駐運用

サーバー機 `DESKTOP-80AKMUB`(Windows)で SpineBox を常駐させ、LAN 内の別端末のブラウザから読めるようにする手順。
スクリプトは `deploy/`(既存の CD テンプレート `*.example` とは別物。CD は未導入)。
**開発機で確かめたのはスクリプトの構文・引数検証・`-WhatIf`・再起動ループ・子プロセスの後始末(下の「python を孤児にしない」)まで。実際の登録と LAN からの閲覧、`Stop-ScheduledTask` での停止はサーバー機での確認待ち(#20)。**

## 前提

- Python 3.12、Node.js 22(フロントのビルド用)、7-Zip(7z / rar を読むのに必須。無くても zip / cbz は読める)、git
- 管理者権限の PowerShell(登録・ファイアウォールだけ。起動・状態確認は不要)
- インターネットへ公開しない。認証が無いので、ルーターのポート開放はしない

## 初回セットアップ

```powershell
git clone https://github.com/kant0123/SpineBox.git ; cd SpineBox
py -3.12 -m pip install -r requirements.txt
npm --prefix frontend ci ; npm --prefix frontend run build   # frontend/dist ができる。バックエンドが / で配信する
copy config.example.toml config.local.toml                    # 編集: library_roots / host / port / sevenzip_path
py -3.12 -m spinebox                                          # 手動で起動して確かめる
```

- `sevenzip_path` は 7z.exe のパス(例 `C:/Program Files/7-Zip/7z.exe`)を書く。常駐タスクの実行ユーザー(SYSTEM 等)の PATH に
  7z が無いことが多いので、名前だけにせずパスで指定する。見つからないと 7z / rar は「未対応」として一覧から外れる([[archive-reading]])
- `host` の既定は `127.0.0.1`(自機だけ)。LAN で使うときはサーバー機の LAN アドレスか `0.0.0.0` を**明示して**設定する。
  127.0.0.1 以外のときは起動時に注意を stderr へ出す(`spinebox/__main__.py:15 main`)
- `GET /healthz` が `{"ok": true, "commit": "<短い SHA>"}`(`spinebox/app.py:118 create_app` の `healthz`)。DB を開けなければ 503。
  `commit` は**プロセスが起動時に読んだ**コミット(`spinebox/buildinfo.py:14 current_commit`)で、git が使えず `.git` も読めなければ null。
  古いプロセスが生き残っていると 200 のまま古い `commit` を返すので、更新後は `commit` で反映を見分ける
- `/read/<id>` を直接開いてもよい。バックエンドは未知のパスに `index.html` を返し(SPA fallback)、
  フロントが `/#/read/<id>` に直す(`frontend/src/route.ts:48 canonicalUrl`)。ルート自体はハッシュで決まるため、
  この直しが無いと一覧が開く

## ファイアウォール(LAN 公開)

```powershell
.\deploy\firewall_rule.ps1 -Action Add -Port 8000    # 管理者。-WhatIf で内容だけ確認できる
.\deploy\firewall_rule.ps1 -Action Status
```

- 規則は受信・TCP・指定ポート・**プライベートプロファイルのみ**・接続元は同じサブネット(`LocalSubnet`)だけ
- **ネットワークの種類が Public だと規則が効かず、別端末から開けない。** `Status` が種類を表示する。
  Wi-Fi が Public になっていたらネットワークのプロパティで Private にする(スクリプトは種類を変えない)
- ポートは設定の `port` と揃える

## 常駐(タスクスケジューラ)

```powershell
.\deploy\install_task.ps1 -Action Register -PythonPath C:\Python312\python.exe -ConfigPath C:\SpineBox\config.local.toml -StartNow
.\deploy\install_task.ps1 -Action Status       # タスクの状態・ラッパーと python のプロセス・/healthz の応答
.\deploy\install_task.ps1 -Action Stop         # タスクを止め、残ったプロセスがあれば PID を指定して止める(管理者)
.\deploy\install_task.ps1 -Action Unregister
```

登録するのは「OS の起動 30 秒後に `deploy/start_spinebox.ps1` を実行する」タスク。ログオン不要。

- **実行ユーザーは既定が SYSTEM。** ログオン不要で動くが、ユーザーのフォルダ(`C:\Users\...` の Python や蔵書)は読めず、
  ネットワーク共有にも資格情報が無い。そのときは `-UserName <ユーザー>` を付ける(パスワードを尋ねる。
  「ログオンしているかどうかにかかわらず実行する」で登録される)
- `start_spinebox.ps1` が `python -m spinebox` を起動し、終了したら再起動する(2 秒から倍々で最大 60 秒。
  60 秒以上動いたあとなら 2 秒に戻る)。タスク側にも 1 分間隔の再試行を設定してあり、スクリプト自身が落ちても戻る。
  引数が不正(Python・設定ファイル・リポジトリが無い)なときは exit 2 で止まり、再起動ループに入らない
  (再起動しても直らないので)
- Python は登録時に絶対パスへ解決して渡す(SYSTEM は利用者の PATH を持たない)。`py` ランチャーなら
  `-PythonPath py -PythonArguments "-3.12"`
- `-WhatIf` は登録内容を表示するだけ。システム設定の変更なので、**開発機では登録していない**

## 更新手順

```powershell
.\deploy\install_task.ps1 -Action Stop
git pull --ff-only ; py -3.12 -m pip install -r requirements.txt
npm --prefix frontend ci ; npm --prefix frontend run build
Start-ScheduledTask SpineBox
.\deploy\install_task.ps1 -Action Status    # commit が変わったか、/healthz が 200 か
```

- DB のマイグレーションは起動時に自動で当たる(`spinebox/db.py:16 MIGRATIONS`)
- 止めるには `Stop-ScheduledTask` ではなく `-Action Stop` を使う。タスクを止めたあと、ラッパーや `-m spinebox` の
  プロセスが残っていないかを確かめ、残っていれば PID を指定して止める(普通は何も残らない。下の「python を孤児にしない」)
- `Stop-ScheduledTask` 単体で子まで止まるかは、サーバー機で未確認(#20)。止まらなくても次の起動が孤児を片付ける

## python を孤児にしない

ラッパーの PowerShell だけが死ぬと、子の python(uvicorn)は親を失って生き残り、ポートを握り続ける。
次の起動は bind に失敗して終わり、再起動ループが空回りする(#23。開発機で再現した)。2 段で防ぐ。

1. **ラッパーが自分自身を Job Object に入れる**(`deploy/start_spinebox.ps1:75 Enable-KillChildrenOnExit`)。
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` の Job に入れておくと、python は子として同じ Job に入り、ラッパーが
   `Stop-Process -Force` などで強制終了されても(ハンドルが閉じる = Job 内の全プロセスが終了する)道連れで終わる。
   `py -3.12` の `py.exe → python.exe` も両方入る。Windows 8 / Server 2012 以降はネスト Job が使えるので、
   タスクスケジューラが既に Job に入れていても割り当てられる。割り当てに失敗したら警告を `supervisor.log` に出して続ける
   (その場合は 2 段目だけが頼り)
2. **起動のたびに、設定のポートの持ち主を調べる**(`deploy/start_spinebox.ps1:203 Resolve-PortConflict`)。
   `-m spinebox` のプロセスで、親をたどっても生きている親が居ない(= 親が先に死んだ孤児)ものだけを、子孫ごと止めて
   `supervisor.log` に PID 付きで残す。**親が生きているもの(手で起動した SpineBox・別のプログラム)は止めず、
   警告だけ出して起動を試みる**(bind に失敗して通常の再起動の間隔で待つ)。`-NoReapOrphan` で、孤児も止めず記録だけにできる。
   ポートは `python -m spinebox --print-bind` で設定ファイルから取る(TOML を PowerShell で読み直さない。`spinebox/__main__.py:15 main`)

親の生死は `ParentProcessId` の先に実在するプロセスがあり、かつ子より古いこと(PID は再利用される)で判定する。
Ctrl+C など「正常な中断」では `finally` が python の木を止めるので、1 段目は強制終了のための備え。

## ログ

`<リポジトリ>\logs\`(`-LogDir` で変更)。UTF-8。

| ファイル | 内容 |
| --- | --- |
| `supervisor.log` | 起動・終了コード・稼働秒数・再起動までの待ち。5 MB で `supervisor.old.log` へ回す |
| `spinebox-<日時>.out.log` / `.err.log` | その起動回のアプリの標準出力 / 標準エラー(uvicorn の起動・アクセスログはエラー側)。新しい 30 回分だけ残す |

起動のたびに別のファイルにするのは、再起動ループが前回のログを上書きして、落ちた原因を消さないため。

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| 別端末から開けない | `firewall_rule.ps1 -Action Status`(規則が Private・ネットワークが Private か)、設定の `host` が `127.0.0.1` のままでないか、`/healthz` を自機で叩けるか |
| タスクは登録したが動かない | `Status` の最終結果。`supervisor.log` に何も無ければスクリプトが起動していない(SYSTEM でスクリプトのあるフォルダが読めない、等)。`-UserName` で登録し直す |
| 画面のスキャンが「スキャンに失敗しました: ...」になる / 「読めなかったもの」に想定外のエラーが出る | 画面の型とメッセージが原因の手掛かり。同じ起動回の `.err.log` にトレースバックがある(ソース単位の想定外はスキャンを止めずに記録だけする。`sqlite3.OperationalError` のように DB が使えないときだけ 500 で止まる。[[backend]]) |
| `supervisor.log` に終了が連続する | 同じ起動回の `.err.log` を読む。設定エラー(`設定エラー: ...`)・ポート使用中・依存の不足が多い |
| `Status` の `commit` が更新前のまま | 古いプロセスが残っている。`-Action Stop` で止めて開始し直す(`Status` の PID を指定して止めてもよい) |
| `supervisor.log` に「警告: ポート N は別のプロセスが使用中です」 | 孤児ではない(親が生きている)プロセスがポートを握っている。手で起動した SpineBox や別のプログラムなので、持ち主を止めるかポートを変える。ラッパーは止めない |
| 日本語が化けてスクリプトが動かない | `.ps1` は UTF-8 (BOM 付き)で保存する(BOM が無いと Windows PowerShell 5.1 が CP932 で読む)。`tests/test_deploy_scripts.py` が BOM を検査する |

## 実施記録

| 日付 | 実施者 | 結果 |
| --- | --- | --- |
| 2026-11-01 | #23(開発機) | ラッパーを `Stop-Process -Force` しても python(`py.exe → python.exe` を含む)が残らないこと、孤児がポートを握った状態でラッパーを起動すると検出・停止して起動すること、親が生きているプロセスは止めないことを確認(自動テストと、実アプリ・`py -3.12` での手動確認)。`Stop-ScheduledTask` と `-Action Stop` の実機確認は未実施(#20) |
| 2026-11-01 | #19(開発機) | スクリプトの構文・`-WhatIf`・引数検証・再起動ループ(python を強制終了 → 約 4 秒で再起動、`/healthz` 復帰)・`frontend/dist` の配信を確認。登録・ファイアウォール・LAN 閲覧は未実施(#20) |
