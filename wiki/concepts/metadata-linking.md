---
type: concept
summary: 公開 DB の情報を作品・巻へ結線する規則(#50)。連携(work_links)の自動/確認待ちの基準、空欄だけを埋める・手動は不変・名前由来の値を置き換える条件、失敗の扱い、保存場所(全文検索・絞り込みが乗る列)、却下した代替案
updated: 2026-11-02
related: [metadata-clients, metadata-sources, work-grouping, backend, frontend, overview]
---

# 公開 DB の結線(`spinebox/linking.py`)

[[metadata-clients]] が返す候補を、作品(`works`)と巻(`volumes`)の項目へ結ぶ(GOAL.md フェーズ 3「公開データベースから埋まる」、#50)。
クライアントは候補と確からしさを返すだけで、**どれを採るか・どう埋めるかはここ**。取得元の癖は [[metadata-sources]]、作品・項目の持ち方は [[work-grouping]]。

## 全体像

```
fetch_work(作品, 取得元)  ← 唯一ネットワークに触る。ボタン/「情報を補完」/裏の定期取得からだけ
   │  候補を作って work_links に保存(自動で連携 / 確認待ち / 該当なし / 失敗の記録)
   ▼
apply_work(作品)          ← DB だけで完結・冪等。fetch・choose・reset・スキャン後に走る
   │  連携から work_fields / work_aliases / volume_fields を導出
   ▼
```

**スキャン・閲覧・編集はネットワークに触らない**(CLAUDE.md「取得失敗でスキャンと閲覧を止めない」)。`apply_work` は連携の中身(保存済み)の純関数なので、
スキャンで巻が増えれば `apply_all` が新しい巻へ書誌を入れ、連携を外せば公開 DB 由来の値だけが消える。

## 連携の決め方(`spinebox/linking.py:150 decide()`)

- 取得元ごとに 1 作品 1 行(`work_links`: `status` = `linked` / `pending` / `none`)。`manual` は利用者が選んだ連携
- **自動で連携するのは、最良候補の確からしさ ≥ 0.75 かつ 2 位との差 ≥ 0.15**(`AUTO_LINK_MIN` / `LINK_MARGIN`)。足りなければ `pending`(確認待ち)で、0.5 以上の候補を最大 5 件見せる。
  数値の根拠は実データ(`_参考/サンプル本` の 2 作品)で、かくかくしかじかは 1.0 対 0.71、お気楽領主は漫画 1.0 対小説 1.0 の同点。閾値の調整は `tests/test_linking.py` の同点・僅差のケースを見てから
- 作品の種別(`kind`)が分かっていれば、種別の違う候補は除く
- **種別が決まっていない作品で国立国会図書館サーチが漫画と小説で割れているときは、AniList / MangaUpdates も確認待ちにする(`hold = "kind_unresolved"`、`kind_unresolved()`)。**
  お気楽領主は MangaUpdates の漫画が名前完全一致で差が付き、先に自動連携して種別と著者を書いてしまった(NDL の割れを無視した誤り)。NDL の候補を選ぶと、次の取得(保存済みの応答を使うので通信は出ない)で残りが自動で決まる
- 国立国会図書館サーチは 1 冊ずつの書誌なので、**(種別・作品名・シリーズ名・著者)が同じものを「組」にして連携の単位にする**(`_ndl_groups()`)。組の確からしさ = 最良の巻 × 0.85 + 手元の巻番号を組が覆う割合 × 0.15。
  同じ名前で巻次の無い映画ノベライズと、1〜4 巻を持つ漫画を見分けるため。`source_id` は組のハッシュ(`ndl:` + 10 桁)
- 自動連携は粘る(再取得で候補が残っていれば維持)。手動の連携は再取得で変えない(同じ候補が出たときに中身だけ更新)。手動の「どれでもない」は、その取得元へ問い合わせない

## 項目の埋め方(`spinebox/linking.py:464 _apply_field()`)

| 規則 | 理由 |
| --- | --- |
| 手動の項目は何があっても変えない(空にした項目も空のまま) | CLAUDE.md の不変条件。`manual` 列で見る。`tests/test_linking.py` が全項目で再取得・スキャン・再計算の前後を確かめる |
| 空欄は埋める(出所 `public_db`、確信度 = 連携の確信度、`evidence.link.sources` に取得元) | 外部は空欄だけを埋める |
| 名前(ファイル名・外側のアーカイブ名)由来の値は、**連携の確信度 ≥ 0.9 か、利用者が連携を選んだときだけ**置き換える。置き換えた元の値は `evidence.replaced` に残し、連携を外すと戻す | 実データで作品名「Kakukaku Shikajika」→「かくかくしかじか」になる。確信度が低い連携で作品名を変えると、取り返しがつかなくなる。置き換えた作品名は別名に残す(その名前の巻がまた新しい作品にならないように) |
| 取得元の優先: 種別・出版社は NDL、あらすじ・巻数は AniList → MangaUpdates、ジャンル・タグは MangaUpdates と AniList の和(重複除去・上限つき) | 取得元の強みで分けた(NDL は書誌、他は作品単位の分類) |
| 別名は公開 DB の名前から(ローマ字と日本語を先に、最大 6、3 文字未満・非対応文字種は除く) | 別名の一致が名寄せの証拠([[work-grouping]])になるので、タイ語など使えない綴りは入れない。公開 DB の別名で作品が自動統合されることがある(確信度は連携の確信度) |
| 巻の書誌(出版社・レーベル・発売日・ISBN・巻次)は、NDL の連携がある作品の**巻番号が単独で分かる巻**だけに入れる(`_apply_volumes()`) | 範囲(`v01-03`)の巻や、名前と中身の食い違い([[work-grouping]] の findings)がある巻には入れない。巻番号は名前から読んだ候補にすぎない。同じ番号の重複スキャンには同じ書誌が入る |

## 手動の編集と「自動に戻す」

`spinebox/works.py:660 edit_fields()` は手動で保存し、直す前の値を `evidence.previous` に退避する。`spinebox/works.py:699 reset_field()` が復元し、無ければ空に戻す(その後 `apply_work` が公開 DB の値を入れ直す)。
作品名は直す前の名前が残っていないと戻せない(409)。巻の書誌は `edit_volume_fields()` / `reset_volume_field()` で同じ。
統合(`merge_works`)は手動の項目を優先して引き継ぎ、連携も `_merge_links()` で移す。整理(CBZ 化)で巻が作り直されても、`transfer_volume_fields()` が書誌を引き継ぐ。

## 取得元が落ちているとき

失敗は**例外にせず**、作品 × 取得元の `work_links.failure`(種類・メッセージ・HTTP 状態・再試行秒・時刻)に記録し、成功すれば消す。すでに入った値と連携は失敗で変えない。
有効期限切れでも取得元が落ちていれば古い保存済みの応答を使い(`stale`)、画面に出す。想定外の例外も `internal` として記録して続ける(`fetch_work` は raise しない)。
全作品の取得(`POST /api/works/metadata/fetch`)は 1 本ずつ直列で、既定の最小間隔(NDL 1.0 秒・AniList 2.0 秒・MangaUpdates 1.0 秒)を守る。
対象は未取得・失敗から `retry_hours` 経過・成功から `recheck_days` 経過の作品(`works_needing_fetch()`)。裏の定期取得は既定で無効(`[metadata] background`)。

## 保存場所(全文検索・絞り込みの入口)

後続の全文検索・絞り込みの Issue はここに乗せる。

| 何 | どこ |
| --- | --- |
| 作品の項目(`title` `author` `kind` `publisher` `genres` `tags` `synopsis` `total_volumes`)と出所・手動・確信度 | `work_fields`(マイグレーション 6 の既存表。`genres` / `tags` は JSON 配列なので `json_each(value)` で展開。定数は `spinebox/works.py:32 FIELD_KIND` ほか) |
| 作品の別名(公開 DB 由来を含む) | `work_aliases`(`source = 'public_db'`) |
| 巻の書誌(`publisher` `label` `released` `isbn` `volume_label` `subtitle`) | `volume_fields`(マイグレーション 7) |
| 連携・候補・失敗 | `work_links`(マイグレーション 7。`candidates` `item` は JSON) |
| 生の応答 | `metadata_responses`([[metadata-clients]]) |

## 却下した案

- **連携を 1 作品 1 行(取得元をまとめる)にする**: 取得元ごとに候補も失敗も違い、NDL の割れを AniList が引きずる構造になる。取得元ごとの行にして、保留の連鎖(`kind_unresolved`)だけを明示した
- **確信度が高ければ作品名を常に置き換える/一切置き換えない**: 前者は低い確信度で名前が変わる事故、後者はローマ字のファイル名の作品が日本語名にならず名寄せも進まない。「0.9 以上か利用者が選んだとき」にした
- **スキャンのたびに公開 DB を引く**: スキャン・閲覧を外部の障害に巻き込み、レート制限も食う。取得は明示操作(か設定した定期取得)に限った
- **NDL を 1 冊ずつ連携の単位にする**: 作品に 1〜10 件の書誌が並び、どれを選ぶかを巻ごとに利用者に聞くことになる。組にして 1 回の確認で済ませた
