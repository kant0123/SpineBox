"""範囲指定の整理(#84): 整理画面のテンプレート({title} 記法・[...] の省略・<作品名> の互換)、ファイル名の整理、
範囲(ルート全体 / サブフォルダ)の改名・移動の計画 → 実行 → まとめて元に戻す。蔵書は一時ディレクトリに動的生成する。"""
import os
import unicodedata
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip
from spinebox import db, works
from spinebox.app import create_app
from spinebox.cache import ExtractionCache
from spinebox.library import Library
from spinebox.organize.cleanup import clean_name, clean_stem, fix_mojibake
from spinebox.organize.fs import move_no_overwrite
from spinebox.organize.organizer import OrganizeError, Organizer, RangeError, RangeNotFound
from spinebox.organize.template import (DEFAULT_NAME_TEMPLATE, PRESETS, NameTemplate, NameValues, RangeTemplate,
                                        TemplateError, ValueMissing, range_examples)
from spinebox.scanner import Scanner

TITLE = "お気楽領主の楽しい領地防衛"
FULL = {"title": TITLE, "author": "作者 太郎", "publisher": "出版社X", "label": "レーベルY", "kind": "漫画",
        "year": "2020"}


def values(kind="volume", start=3, end=None, **fields):
    return NameValues({**FULL, **fields}, kind, start, start if end is None else end)


def render(file_template="", folder_template="", v=None):
    t = RangeTemplate(file_template, folder_template)
    v = v or values()
    return t.render_folder(v), t.render_file(v)


# --- テンプレート ------------------------------------------------------------------

def test_each_parameter_and_width():
    _, name = render("{title}|{author}|{publisher}|{label}|{kind}|{year}|{number}|{number:3}|{unit}".replace("|", " "))
    assert name == f"{TITLE} 作者 太郎 出版社X レーベルY 漫画 2020 3 003 巻"
    # 日本語の名前・<…> の書き方も同じ変数。NN は 2 桁の略記
    assert render("{作品名} <著者> 第NN巻")[1] == f"{TITLE} 作者 太郎 第03巻"
    # 番号の直後の「巻」は話単位なら「話」に、範囲は 01-03
    assert render("{title} 第{number:2}巻", v=values(kind="episode", start=12))[1] == f"{TITLE} 第12話"
    assert render("{title} 第{number:2}{unit}", v=values(start=1, end=3))[1] == f"{TITLE} 第01-03巻"


def test_brackets_are_omitted_when_a_value_is_empty():
    t = "{title}[ ({author})][ - {label} {year}]"
    assert render(t)[1] == f"{TITLE} (作者 太郎) - レーベルY 2020"
    assert render(t, v=values(author=None))[1] == f"{TITLE} - レーベルY 2020"
    # 組の中の値が 1 つでも空なら組ごと省略
    assert render(t, v=values(author=" ", year=None))[1] == TITLE
    # 組の外の値が空なら対象外(空文字で展開しない)
    with pytest.raises(ValueMissing, match="著者が空欄"):
        render("{author} {title}", v=values(author=None))
    with pytest.raises(ValueMissing, match="番号を読めません"):
        render("{title} {number:2}", v=values(start=None))
    # 二重の角括弧はその文字そのもの
    assert render("[[{author}]] {title}")[1] == f"[作者 太郎] {TITLE}"


def test_folder_template_segments_and_empty_segment_dropped():
    folder, name = render("{title} 第{number:2}{unit}", "{publisher}/[{label}]/{title}")
    assert folder == ["出版社X", "レーベルY", TITLE] and name == f"{TITLE} 第03巻"
    # 省略で空になった区切りは無くす(空のフォルダ名を作らない)
    assert render("", "{publisher}/[{label}]/{title}", values(label=None))[0] == ["出版社X", TITLE]
    # どちらも空なら None(ファイル名は整理だけ・フォルダはそのまま)
    assert render() == (None, None)
    # 末尾の .cbz は書いても拡張子にしない(拡張子は元のものを保つ)
    assert render("{title}.cbz")[1] == TITLE


def test_values_are_sanitized_into_one_segment():
    folder, name = render("{title}", "{title}", values(title="Fate/Zero: 前編?"))
    assert folder == ["Fate_Zero_ 前編_"] and name == "Fate_Zero_ 前編_"
    assert render("{title}", "", values(title=".."))[1] == "_"
    assert render("{title}", "", values(title="CON"))[1] == "_CON"


@pytest.mark.parametrize("file_t, folder_t, message", [
    ("{title}/{author}", "", "/ は使えません"),
    ("{題名}", "", "使えません"),
    ("{number:9}", "", "桁数"),
    ("[{title}", "", "閉じていません"),
    ("{title}]", "", "対応する \\["),
    ("[[x] [{title}]", "", "対応する \\["),
    ("[a[{title}]]", "", "中に \\["),
    ("{title", "", "閉じていません"),
    ("title}", "", "開きの無い"),
    ("a?b", "", "使えない文字"),
    ("", "../x", "「..」"),
    ("", "x//y", "空"),
    ("", "C:/x", "相対パス"),
])
def test_invalid_range_templates(file_t, folder_t, message):
    with pytest.raises(TemplateError, match=message):
        RangeTemplate(file_t, folder_t)


def test_name_template_setting_is_kept_and_translated():
    # 設定の name_template は #58 の書き方のまま(角括弧はその文字)。新しい変数も <…> で使える
    legacy = NameTemplate("[<著者>] <作品名>/<作品名> 第NN巻.cbz")
    assert legacy.render(values()) == f"[作者 太郎] {TITLE}/{TITLE} 第03巻.cbz"
    assert NameTemplate("<レーベル>/<作品名> <発売年>.cbz").render(values()) == f"レーベルY/{TITLE} 2020.cbz"
    # 整理画面の書き方へ読み替えても同じ名前になる
    file_t, folder_t = legacy.range_templates()
    assert (file_t, folder_t) == ("{title} 第{number:2}巻", "[[{author}]] {title}")
    folder, name = render(file_t, folder_t)
    assert "/".join([*folder, name + ".cbz"]) == legacy.render(values())
    assert NameTemplate(DEFAULT_NAME_TEMPLATE).range_templates() == ("{title} 第{number:2}巻", "{title}")


def test_presets_and_examples_are_valid():
    for label, file_t, folder_t in PRESETS:
        RangeTemplate(file_t, folder_t)  # 不正なプリセットを出さない
    examples = range_examples("{title} 第{number:2}{unit}[ ({author})]", "[{label}]/{title}", "漫画")
    assert examples[0]["path"] == f"漫画/レーベルY/{TITLE}/{TITLE} 第03巻 (作者 太郎).cbz"
    assert examples[1]["path"] == "漫画/ある作品/ある作品 第12話.cbz"
    assert range_examples("{author}", "")[1] == {"label": examples[1]["label"], "path": None,
                                                 "reason": "作品の著者が空欄です"}
    assert range_examples("", "")[0]["path"] == "(元のフォルダ)/(元の名前を整理したもの).cbz"
    with pytest.raises(TemplateError):
        range_examples("{x}", "")


# --- ファイル名の整理 --------------------------------------------------------------

def test_cleanup_special_characters_and_width():
    c = clean_name("ＡＢＣ１２３ ｶﾞﾝﾀﾞﾑ\u3000第1巻:前編?.zip")
    assert c.name == "ABC123 ガンダム 第1巻：前編？.zip"
    assert set(c.changes) == {"Windows で使えない文字を全角にしました", "全角の英数字を半角にしました",
                              "半角カナを全角にしました", "空白を整えました(全角の空白・続いた空白)"}
    assert clean_name(" .作品\t 名\x01. .cbz").name == "作品 名.cbz"
    assert "制御文字を取り除きました" in clean_name("作品\x01.cbz").changes
    assert clean_name("CON.cbz").name == "_CON.cbz"
    nfd = unicodedata.normalize("NFD", "がぎぐ")
    assert clean_name(nfd + ".epub") == clean_name("がぎぐ.epub").__class__("がぎぐ.epub", ("濁点・半濁点の分離を結合しました",))
    # 全角の記号・名前の途中のドットはそのまま。拡張子は変えない
    assert clean_name("作品!(上) 第1.5巻.CBZ").name == "作品!(上) 第1.5巻.CBZ"
    with pytest.raises(ValueMissing, match="空"):
        clean_stem(" . ")


@pytest.mark.parametrize("original, wrong, right", [
    ("かくかくしかじか 第2巻", "utf-8", "cp932"),  # UTF-8 を Shift_JIS として読んだ(縺九￥…)
    (f"{TITLE} 第2巻", "cp932", "cp1252"),  # Shift_JIS を Windows-1252 として読んだ(‚¨‹C…)
    (f"{TITLE} 第2巻", "cp932", "latin-1"),
    (f"{TITLE} 第2巻", "utf-8", "latin-1"),  # UTF-8 を Latin-1 として読んだ(ã\x81\x8a…)
])
def test_cleanup_fixes_detectable_mojibake(original, wrong, right):
    broken = original.encode(wrong).decode(right)
    fixed = clean_name(broken + ".cbz")
    assert fixed.name == original + ".cbz"
    assert any("文字化け" in c for c in fixed.changes)


@pytest.mark.parametrize("name", [TITLE, "かくかくしかじか 第1巻", "東京", "漢字", "café au lait", "Ünïcödé",
                                  "ある作品 第05話", "鬼滅の刃 23", "ﾃｽﾄ", "Ｒｅ：ゼロ"])
def test_cleanup_does_not_break_real_names(name):
    fixed, note = fix_mojibake(name)
    assert note is None and fixed == name


# --- 合成蔵書 -----------------------------------------------------------------------

def cbz(path: Path, label: str, n: int = 2) -> Path:
    return write_zip(path, [(f"{i:03d}.jpg", page_bytes(f"{label}/{i}")) for i in range(1, n + 1)])


@pytest.fixture
def lib(tmp_path) -> Path:
    root = tmp_path / "lib"
    cbz(root / "漫画" / "作者A" / f"{TITLE} 1巻.cbz", "v1")
    write_zip(root / "漫画" / "作者A" / f"{TITLE} 2巻.zip", [("001.jpg", page_bytes("v2/1"))])  # 1 巻の zip
    cbz(root / "漫画" / "スキャン" / f"{TITLE} 第2巻.cbz", "v2-other", 3)  # 同じ巻の別スキャン → 衝突
    cbz(root / "漫画" / "ｱﾙ作品　第5話.cbz", "ep5")  # 半角カナ・全角空白
    write_zip(root / "漫画" / "まとめ.zip", [("v1/001.jpg", page_bytes("m1")), ("v2/001.jpg", page_bytes("m2"))])
    (root / "漫画" / "画像の巻").mkdir(parents=True)
    (root / "漫画" / "画像の巻" / "001.jpg").write_bytes(page_bytes("img"))
    (root / "小説").mkdir()
    (root / "小説" / "ある小説 第1巻.txt").write_bytes("本文です。".encode("utf-8"))
    cbz(root / "そのほか" / "番外編.cbz", "extra")
    return root


@pytest.fixture
def env(make_settings, lib):
    settings = make_settings(lib)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    works.sync(conn)
    library = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    root_id = conn.execute("SELECT id FROM library_roots").fetchone()["id"]
    return SimpleNamespace(conn=conn, settings=settings, library=library, root=lib, root_id=root_id)


def organizer(env, **kw) -> Organizer:
    return Organizer(env.conn, env.settings, env.library, **kw)


def full_snapshot(root: Path) -> tuple[dict, set]:
    files = {k: v for k, v in snapshot(root).items() if not k.startswith(".spinebox")}
    dirs = {str(Path(d).relative_to(root)) for d, _, _ in os.walk(root)}
    return files, {d for d in dirs if not d.startswith(".spinebox")}


def vid_of(env, rel: str) -> int:
    return env.conn.execute("""SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
                               WHERE s.rel_path = ? AND v.missing = 0""", (rel,)).fetchone()["id"]


def work_of(env, volume_id: int) -> int:
    return env.conn.execute("SELECT work_id FROM work_volumes WHERE volume_id = ?", (volume_id,)).fetchone()["work_id"]


def by_from(plan) -> dict:
    return {it["from_rel_path"]: it for it in plan["items"]}


def set_progress(env, volume_id, page):
    with db.transaction(env.conn) as c:
        c.execute("INSERT OR REPLACE INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, ?, 0, ?)",
                  (volume_id, page, db.now()))


def progress(env, volume_id):
    r = env.conn.execute("SELECT page FROM reading_progress WHERE volume_id = ?", (volume_id,)).fetchone()
    return None if r is None else r["page"]


def plan_range(env, path="", file_t="", folder_t="", exclude=(), include_cbz=False, **kw):
    return organizer(env, **kw).plan_range(env.root_id, path, file_template=file_t, folder_template=folder_t,
                                           exclude_volume_ids=exclude, include_cbz=include_cbz)


def test_preview_writes_nothing_for_root_and_subfolder(env):
    before = full_snapshot(env.root)
    for path in ("", "漫画", "漫画/作者A"):
        for file_t, folder_t in (("", ""), ("{title} 第{number:2}{unit}", "{title}"), ("{author}", "")):
            plan_range(env, path, file_t, folder_t)
    assert full_snapshot(env.root) == before  # プレビューでは蔵書を 1 バイトも変えない
    assert not (env.root / ".spinebox").exists()


def test_scope_is_root_or_subfolder(env):
    everything = by_from(plan_range(env))
    assert {"小説/ある小説 第1巻.txt", "そのほか/番外編.cbz", f"漫画/作者A/{TITLE} 1巻.cbz"} <= set(everything)
    sub = by_from(plan_range(env, "漫画/作者A"))
    assert set(sub) == {f"漫画/作者A/{TITLE} 1巻.cbz", f"漫画/作者A/{TITLE} 2巻.zip"}
    # ルートの絶対パス・区切りの揺れで書いてもよい
    assert set(by_from(plan_range(env, str(env.root / "漫画" / "作者A") + "\\"))) == set(sub)
    for bad, message in (("../外", "「..」"), ("無いフォルダ", "見つかりません"), ("D:/本", "中を指して"),
                         (".spinebox", "作業場")):
        if bad == ".spinebox":
            (env.root / ".spinebox").mkdir()
        with pytest.raises(RangeError, match=message):
            plan_range(env, bad)
    with pytest.raises(RangeNotFound):
        organizer(env).plan_range(99999)


def test_cleanup_only_plan(env):
    plan = plan_range(env, "漫画")
    assert plan["kind"] == "range" and plan["scope"]["path"] == "漫画" and plan["state"] == "planned"
    items = by_from(plan)
    ep = items["漫画/ｱﾙ作品　第5話.cbz"]
    assert ep["status"] == "move" and ep["output_rel_path"] == "漫画/アル作品 第5話.cbz"
    assert "半角カナを全角にしました" in ep["changes"]
    # 整える所の無い名前はそのまま。複数巻のまとめ・画像フォルダは対象外(CBZ 化へ案内)
    assert items[f"漫画/作者A/{TITLE} 1巻.cbz"]["status"] == "unchanged"
    multi = [it for it in plan["items"] if it["from_rel_path"] == "漫画/まとめ.zip"]
    assert len(multi) == 2 and all(it["status"] == "excluded" and it["cbz_volume_id"] for it in multi)
    assert items["漫画/画像の巻"]["status"] == "excluded" and "画像フォルダ" in items["漫画/画像の巻"]["reason"]
    assert [v["title"] for v in plan["volumes"]] == ["ｱﾙ作品　第5話"]


def test_template_plan_keeps_extension_and_starts_folders_at_range(env):
    plan = plan_range(env, "漫画", "{title} 第{number:2}{unit}", "{title}")
    items = by_from(plan)
    v1 = items[f"漫画/作者A/{TITLE} 1巻.cbz"]
    assert v1["status"] == "move" and v1["output_rel_path"] == f"漫画/{TITLE}/{TITLE} 第01巻.cbz"
    # zip は zip のまま(中身を作り直すのは CBZ 化の役目)
    assert items[f"漫画/作者A/{TITLE} 2巻.zip"]["output_rel_path"] == f"漫画/{TITLE}/{TITLE} 第02巻.zip"
    assert items[f"漫画/スキャン/{TITLE} 第2巻.cbz"]["output_rel_path"] == f"漫画/{TITLE}/{TITLE} 第02巻.cbz"
    # 小説も拡張子を保って動かせる。種別が未決でも小説の巻は「小説」
    novel = by_from(plan_range(env, "小説", "{title}", "{kind}"))["小説/ある小説 第1巻.txt"]
    assert novel["status"] == "move" and novel["output_rel_path"] == "小説/小説/ある小説.txt"
    # 範囲の外は対象にしない(フォルダーテンプレートの起点は範囲のパス)
    assert not any(it["from_rel_path"].startswith("そのほか/") for it in plan["items"])


def test_conflicts_and_targets_that_cannot_be_used(env):
    # 拡張子が違えば別の名前(zip と別スキャンの cbz の 2 巻は両方動かせる)
    items = by_from(plan_range(env, "漫画", "{title} 第{number:2}巻", "{title}"))
    assert items[f"漫画/作者A/{TITLE} 2巻.zip"]["status"] == items[f"漫画/スキャン/{TITLE} 第2巻.cbz"]["status"] == "move"
    # 画像フォルダの巻の中へは入れない(次のスキャンで添え物・別の巻と取り違えうる)
    into = by_from(plan_range(env, "漫画", "{title}", "画像の巻"))[f"漫画/作者A/{TITLE} 1巻.cbz"]
    assert into["status"] == "conflict" and "画像フォルダの巻" in into["reason"]
    # 既にファイルがある出力先・別のドライブは衝突
    cbz(env.root / "漫画" / "アル作品 第5話.cbz", "taken")
    assert by_from(plan_range(env, "漫画"))["漫画/ｱﾙ作品　第5話.cbz"]["status"] == "conflict"
    cross = by_from(plan_range(env, "漫画/作者A", "{title} 第{number:2}巻", "", same_drive=lambda a, b: False))
    assert {it["status"] for it in cross.values()} == {"conflict"}
    assert all("別のドライブ" in it["reason"] for it in cross.values())


def test_duplicate_names_conflict_and_can_be_excluded(env):
    # 1 巻(作者A)と別スキャンの 2 巻を同じ名前にするテンプレート → 両方を衝突に、片方を外すともう片方が動く
    plan = plan_range(env, "漫画", "{title}", "{title}")
    items = by_from(plan)
    c1, c2 = items[f"漫画/作者A/{TITLE} 1巻.cbz"], items[f"漫画/スキャン/{TITLE} 第2巻.cbz"]
    assert c1["status"] == c2["status"] == "conflict" and "同じ名前" in c1["reason"]
    again = by_from(plan_range(env, "漫画", "{title}", "{title}", exclude=[c2["volume_id"]]))
    assert again[f"漫画/スキャン/{TITLE} 第2巻.cbz"]["status"] == "skipped"
    assert again[f"漫画/作者A/{TITLE} 1巻.cbz"]["status"] == "move"


def test_missing_values_and_brackets(env):
    v1 = vid_of(env, f"漫画/作者A/{TITLE} 1巻.cbz")
    with db.transaction(env.conn) as c:
        c.execute("""INSERT INTO volume_fields (volume_id, field, value, source, manual, updated_at)
                     VALUES (?, 'label', 'レーベルY', 'manual', 1, ?)""", (v1, db.now()))
    items = by_from(plan_range(env, "漫画/作者A", "{title} 第{number:2}{unit}", "[{label}]/{title}"))
    assert items[f"漫画/作者A/{TITLE} 1巻.cbz"]["output_rel_path"] == f"漫画/作者A/レーベルY/{TITLE}/{TITLE} 第01巻.cbz"
    assert items[f"漫画/作者A/{TITLE} 2巻.zip"]["output_rel_path"] == f"漫画/作者A/{TITLE}/{TITLE} 第02巻.zip"
    # 組の外で値が無ければ対象外(理由つき)
    blocked = plan_range(env, "そのほか", "{title} {number:2}")
    assert blocked["state"] == "blocked" and blocked["op_id"] is None
    assert "番号を読めません" in blocked["items"][0]["reason"]


def test_offline_root_is_blocked_without_touching_db(env):
    with db.transaction(env.conn) as c:
        c.execute("UPDATE library_roots SET online = 0")
    plan = plan_range(env)
    assert plan["state"] == "blocked" and plan["problems"] == ["蔵書フォルダが今オフラインです"]
    assert env.conn.execute("SELECT COUNT(*) FROM volumes WHERE missing = 0").fetchone()[0] > 0


def test_range_roundtrip_multiple_works_restores_everything(env):
    root = env.root
    before = full_snapshot(root)
    v1 = vid_of(env, f"漫画/作者A/{TITLE} 1巻.cbz")
    vzip = vid_of(env, f"漫画/作者A/{TITLE} 2巻.zip")
    ep = vid_of(env, "漫画/ｱﾙ作品　第5話.cbz")
    other = vid_of(env, f"漫画/スキャン/{TITLE} 第2巻.cbz")
    novel = vid_of(env, "小説/ある小説 第1巻.txt")
    work = work_of(env, v1)
    set_progress(env, v1, 1)
    set_progress(env, ep, 1)
    # 手で直した作品名がテンプレートに使われ、整理しても手動のまま
    works.update_fields(env.conn, work, title="お気楽領主", set_title=True)
    with db.transaction(env.conn) as c:
        c.execute("UPDATE work_volumes SET manual = 1 WHERE volume_id = ?", (ep,))
    o = organizer(env)
    plan = o.plan_range(env.root_id, "", file_template="{title} 第{number:2}{unit}", folder_template="{title}",
                        exclude_volume_ids=[other])
    moving = {it["from_rel_path"]: it["output_rel_path"] for it in plan["items"] if it["status"] == "move"}
    assert moving[f"漫画/作者A/{TITLE} 1巻.cbz"] == "お気楽領主/お気楽領主 第01巻.cbz"
    assert moving[f"漫画/作者A/{TITLE} 2巻.zip"] == "お気楽領主/お気楽領主 第02巻.zip"
    assert moving["小説/ある小説 第1巻.txt"] == "ある小説/ある小説 第01巻.txt"
    assert len({work_of(env, it["volume_id"]) for it in plan["items"] if it["status"] == "move"}) >= 3
    done = o.execute(plan["op_id"])
    assert done["state"] == "done"
    assert (root / "お気楽領主" / "お気楽領主 第02巻.zip").read_bytes() == \
        before[0][str(Path("漫画") / "作者A" / f"{TITLE} 2巻.zip")][0]
    # 巻 id はそのまま。読書位置・作品の割り当て(手動も)・手動の作品名が残る
    assert vid_of(env, "お気楽領主/お気楽領主 第01巻.cbz") == v1
    assert vid_of(env, "ある小説/ある小説 第01巻.txt") == novel
    assert progress(env, v1) == 1 and progress(env, ep) == 1
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    assert work_of(env, v1) == work_of(env, vzip) == work
    assert env.conn.execute("SELECT manual FROM work_volumes WHERE volume_id = ?", (ep,)).fetchone()["manual"] == 1
    assert env.conn.execute("SELECT value, manual FROM work_fields WHERE work_id = ? AND field = 'title'",
                            (work,)).fetchone()["manual"] == 1
    assert [h["kind"] for h in o.history()] == ["range"]

    undone = o.undo(plan["op_id"])  # 1 回分をまとめて元に戻す
    assert undone["state"] == "undone"
    assert full_snapshot(root) == before  # パス・バイト列・mtime・フォルダまで実行前と一致
    assert vid_of(env, f"漫画/作者A/{TITLE} 1巻.cbz") == v1
    row = env.conn.execute("SELECT title, number_start FROM volumes WHERE id = ?", (ep,)).fetchone()
    assert (row["title"], row["number_start"]) == ("ｱﾙ作品　第5話", 5)
    assert progress(env, v1) == 1
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    assert vid_of(env, "小説/ある小説 第1巻.txt") == novel and work_of(env, v1) == work


def test_include_cbz_plan_classifies_and_writes_nothing(env):
    before = full_snapshot(env.root)
    plan = plan_range(env, "漫画", include_cbz=True)
    assert plan["state"] == "planned" and plan["include_cbz"] is True
    assert not plan["problems"]
    by_source: dict[str, list[dict]] = {}
    for it in plan["items"]:
        by_source.setdefault(it["from_rel_path"], []).append(it)
    # 1 巻 = 1 CBZ の巻は中身を作り直さず、改名・移動するだけ
    v1 = by_source[f"漫画/作者A/{TITLE} 1巻.cbz"][0]
    assert v1["status"] == "unchanged" and v1["conversion"] is False
    # CBZ でない 1 巻の zip・複数巻のまとめ・画像フォルダは CBZ 化する
    single = by_source[f"漫画/作者A/{TITLE} 2巻.zip"]
    assert len(single) == 1 and single[0]["status"] == "cbz" and single[0]["conversion"] is True
    assert single[0]["output_rel_path"] == f"漫画/作者A/{TITLE} 2巻.cbz"
    multi = by_source["漫画/まとめ.zip"]
    assert len(multi) == 2 and all(it["status"] == "cbz" and it["conversion"] for it in multi)
    assert sorted(it["output_rel_path"] for it in multi) == ["漫画/v1.cbz", "漫画/v2.cbz"]
    image = by_source["漫画/画像の巻"][0]
    assert image["status"] == "cbz" and image["output_rel_path"] == "漫画/画像の巻.cbz"
    assert {s["rel_path"] for s in plan["cbz_sources"]} == {
        f"漫画/作者A/{TITLE} 2巻.zip", "漫画/まとめ.zip", "漫画/画像の巻"}
    # ファイル名・フォルダーテンプレートは CBZ 化にも同じように効く
    templated = plan_range(env, "漫画/作者A", "{title} 第{number:2}{unit}", "{title}", include_cbz=True)
    converted = [it for it in templated["items"] if it["from_rel_path"] == f"漫画/作者A/{TITLE} 2巻.zip"][0]
    assert converted["status"] == "cbz" and \
        converted["output_rel_path"] == f"漫画/作者A/{TITLE}/{TITLE} 第02巻.cbz"
    assert full_snapshot(env.root) == before

    # include_cbz=False は従来どおり、これらを対象外にする
    plain = plan_range(env, "漫画")
    assert plain["include_cbz"] is False and plain["cbz_sources"] == []
    assert all(it["status"] == "excluded" for it in plain["items"] if it["from_rel_path"] == "漫画/まとめ.zip")


def test_include_cbz_roundtrip_creates_cbz_and_restores_everything(env):
    root = env.root
    before = full_snapshot(root)
    old_zip = vid_of(env, f"漫画/作者A/{TITLE} 2巻.zip")
    old_multi = [r["id"] for r in env.conn.execute(
        """SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
           WHERE s.rel_path = '漫画/まとめ.zip' AND v.missing = 0 ORDER BY v.entry_prefix""")]
    old_image = vid_of(env, "漫画/画像の巻")
    assert len(old_multi) == 2
    work_before = work_of(env, old_multi[0])
    set_progress(env, old_multi[0], 0)
    set_progress(env, old_zip, 0)
    o = organizer(env)
    plan = o.plan_range(env.root_id, "漫画", include_cbz=True)
    done = o.execute(plan["op_id"])
    assert done["state"] == "done"
    with zipfile.ZipFile(root / "漫画" / "v1.cbz") as z:
        assert z.namelist() == ["0001.jpg"] and z.read("0001.jpg") == page_bytes("m1")
    with zipfile.ZipFile(root / "漫画" / "v2.cbz") as z:
        assert z.read("0001.jpg") == page_bytes("m2")
    with zipfile.ZipFile(root / "漫画" / "画像の巻.cbz") as z:
        assert z.read("0001.jpg") == page_bytes("img")
    with zipfile.ZipFile(root / f"漫画/作者A/{TITLE} 2巻.cbz") as z:
        assert z.read("0001.jpg") == page_bytes("v2/1")
    # 元は削除せず、退避フォルダへ移す
    trash = root / ".spinebox" / "trash" / plan["op_id"] / "original" / "漫画"
    assert (trash / "まとめ.zip").is_file() and (trash / "画像の巻").is_dir()
    assert not (root / "漫画" / "まとめ.zip").exists()
    # 新しい巻に読書位置・作品の割り当てが移り、元の巻は「整理済み」になる
    new_multi = vid_of(env, "漫画/v1.cbz")
    assert progress(env, new_multi) == 0
    assert env.conn.execute("SELECT organized_op_id FROM volumes WHERE id = ?",
                            (old_multi[0],)).fetchone()[0] == plan["op_id"]
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    assert work_of(env, new_multi) == work_before
    # 1 回の操作で、改名と CBZ 化をまとめて元に戻せる
    undone = o.undo(plan["op_id"])
    assert undone["state"] == "undone"
    assert full_snapshot(root) == before
    assert progress(env, old_multi[0]) == 0 and progress(env, old_zip) == 0
    assert env.conn.execute("SELECT organized_op_id FROM volumes WHERE id = ?",
                            (old_image,)).fetchone()[0] is None
    # 元に戻した後、同じ範囲をもう一度 CBZ 化できる(出力先に残った missing の sources 行を再利用する)
    again = o.plan_range(env.root_id, "漫画", include_cbz=True)
    assert again["state"] == "planned" and not again["problems"]
    assert any(it["status"] == "cbz" and it["from_rel_path"] == "漫画/まとめ.zip" for it in again["items"])
    o.execute(again["op_id"])
    o.undo(again["op_id"])
    assert full_snapshot(root) == before


def test_include_cbz_conflicting_source_is_excluded_as_a_whole(env):
    # 同じフォルダ名の 2 巻を持つ zip。ファイル名テンプレートが空だと同じ名前になる
    write_zip(env.root / "漫画" / "同名.zip",
              [("a/vol/001.jpg", page_bytes("same/1")), ("b/vol/001.jpg", page_bytes("same/2"))])
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    plan = plan_range(env, "漫画", include_cbz=True)
    same = [it for it in plan["items"] if it["from_rel_path"] == "漫画/同名.zip"]
    assert len(same) == 2 and all(it["status"] == "conflict" and "同じ名前" in it["reason"] for it in same)
    # 片方の巻を外すと、その元全体を外す(元の一部だけ CBZ 化しない)
    again = plan_range(env, "漫画", include_cbz=True, exclude=[same[0]["volume_id"]])
    same_again = [it for it in again["items"] if it["from_rel_path"] == "漫画/同名.zip"]
    assert all(it["status"] == "skipped" and "元全体" in it["reason"] for it in same_again), \
        [(it["status"], it["reason"]) for it in same_again]
    assert not any(s["rel_path"] == "漫画/同名.zip" for s in again["cbz_sources"])
    # 外した状態なら、他の元は CBZ 化できる
    assert any(it["status"] == "cbz" for it in again["items"])


def test_include_cbz_image_folder_with_subfolders_is_a_conflict(env):
    (env.root / "漫画" / "画像の親" / "子").mkdir(parents=True)
    (env.root / "漫画" / "画像の親" / "001.jpg").write_bytes(page_bytes("parent"))
    (env.root / "漫画" / "画像の親" / "子" / "001.jpg").write_bytes(page_bytes("child"))
    Scanner(env.conn, env.settings).scan()
    plan = plan_range(env, "漫画", include_cbz=True)
    parent = [it for it in plan["items"] if it["from_rel_path"] == "漫画/画像の親"]
    assert len(parent) == 1 and parent[0]["status"] == "conflict"
    assert "サブフォルダ" in parent[0]["reason"]
    assert not any(s["rel_path"] == "漫画/画像の親" for s in plan["cbz_sources"])


def test_include_cbz_stale_source_or_target_is_refused_without_writing(env):
    o = organizer(env)
    plan = o.plan_range(env.root_id, "漫画", include_cbz=True)
    # 計画の後に元が変わる
    os.utime(env.root / "漫画" / "まとめ.zip", None)
    changed = full_snapshot(env.root)
    with pytest.raises(OrganizeError, match="元が変わりました"):
        o.execute(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "failed"
    assert full_snapshot(env.root) == changed
    # 作り直して、計画の後に出力先が埋まる
    plan = o.plan_range(env.root_id, "漫画", include_cbz=True)
    (env.root / "漫画" / "v1.cbz").write_bytes(page_bytes("taken"))
    with pytest.raises(OrganizeError, match="出力先に既にファイル"):
        o.execute(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "failed"
    assert not (env.root / ".spinebox" / "work" / plan["op_id"]).exists()
    assert not (env.root / ".spinebox" / "trash" / plan["op_id"]).exists()


def test_include_cbz_undo_refuses_when_created_cbz_is_gone(env):
    o = organizer(env)
    plan = o.plan_range(env.root_id, "漫画", include_cbz=True)
    o.execute(plan["op_id"])
    (env.root / "漫画" / "v1.cbz").unlink()
    with pytest.raises(OrganizeError, match="作った CBZ が見つかりません"):
        o.undo(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "done"


def test_include_cbz_failure_rolls_back_rename_and_cbz(env):
    before = full_snapshot(env.root)
    calls = []

    def flaky(src, dst):
        calls.append(Path(dst))
        if "trash" in Path(dst).parts and "original" in Path(dst).parts and \
                sum("trash" in p.parts and "original" in p.parts for p in calls) == 2:
            raise OSError("ディスクが外れた")
        move_no_overwrite(src, dst)

    o = organizer(env, move=flaky)
    plan = o.plan_range(env.root_id, "漫画", include_cbz=True)
    with pytest.raises(OrganizeError, match="ディスクが外れた"):
        o.execute(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "failed"
    assert full_snapshot(env.root) == before
    assert not (env.root / ".spinebox" / "work" / plan["op_id"]).exists()


def test_cleanup_roundtrip_and_failure_midway(env):
    before = full_snapshot(env.root)
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        raise OSError("ディスクが外れた")

    o = organizer(env, move=flaky)
    plan = o.plan_range(env.root_id, "漫画")
    with pytest.raises(OrganizeError, match="ディスクが外れた"):
        o.execute(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "failed" and full_snapshot(env.root) == before
    ok = organizer(env)
    plan = ok.plan_range(env.root_id, "漫画")
    ok.execute(plan["op_id"])
    assert (env.root / "漫画" / "アル作品 第5話.cbz").is_file()
    ok.undo(plan["op_id"])
    assert full_snapshot(env.root) == before


# --- API ------------------------------------------------------------------------------

def test_api_range_options_example_folders_plan_execute_undo(make_settings, lib):
    c = TestClient(create_app(make_settings(lib), frontend_dist=None))
    c.post("/api/scan")
    before = full_snapshot(lib)
    opts = c.get("/api/organize/range/options").json()
    assert opts["defaults"] == {"file_template": "{title} 第{number:2}巻", "folder_template": "{title}"}
    assert [r["path"] for r in opts["roots"]] == [str(lib)] and opts["presets"] and opts["parameters"]
    root_id = opts["roots"][0]["id"]
    ex = c.post("/api/organize/range/example", json={"file_template": "{title}", "folder_template": "",
                                                       "path": "漫画"}).json()
    assert ex["error"] is None and ex["examples"][0]["path"].endswith(".cbz")
    assert "使えません" in c.post("/api/organize/range/example", json={"file_template": "{x}"}).json()["error"]
    folders = c.get("/api/organize/range/folders", params={"root_id": root_id}).json()
    assert folders["folders"] == ["そのほか", "小説", "漫画"] and folders["parent"] is None
    sub = c.get("/api/organize/range/folders", params={"root_id": root_id, "path": "漫画"}).json()
    assert sub["folders"] == ["スキャン", "作者A", "画像の巻"] and sub["parent"] == ""
    assert c.get("/api/organize/range/folders", params={"root_id": root_id, "path": "../x"}).status_code == 422
    assert c.get("/api/organize/range/folders", params={"root_id": 999}).status_code == 404

    plan = c.post("/api/organize/range-plan", json={"root_id": root_id, "path": "漫画"}).json()
    assert plan["state"] == "planned" and plan["scope"]["path"] == "漫画"
    moved = [it for it in plan["items"] if it["status"] == "move"]
    assert moved and moved[0]["changes"]
    assert full_snapshot(lib) == before
    assert c.post(f"/api/organize/{plan['op_id']}/execute").json()["state"] == "done"
    assert c.get("/api/organize").json()[0]["kind"] == "range"
    assert c.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    assert full_snapshot(lib) == before
    bad = c.post("/api/organize/range-plan", json={"root_id": root_id, "file_template": "[{title}"})
    assert bad.status_code == 422 and "閉じていません" in bad.json()["detail"]
    assert c.post("/api/organize/range-plan", json={"root_id": root_id, "path": "無い"}).status_code == 422
    assert c.post("/api/organize/range-plan", json={"root_id": 999}).status_code == 404


def test_api_range_plan_include_cbz_execute_and_undo(make_settings, lib):
    c = TestClient(create_app(make_settings(lib), frontend_dist=None))
    c.post("/api/scan")
    before = full_snapshot(lib)
    root_id = c.get("/api/organize/range/options").json()["roots"][0]["id"]
    plan = c.post("/api/organize/range-plan", json={"root_id": root_id, "path": "漫画", "include_cbz": True}).json()
    assert plan["state"] == "planned" and plan["include_cbz"] is True
    assert plan["cbz_sources"] and any(it["status"] == "cbz" and it["conversion"] for it in plan["items"])
    assert c.post(f"/api/organize/{plan['op_id']}/execute").json()["state"] == "done"
    assert c.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    assert full_snapshot(lib) == before


def test_range_folders_hide_workspace_and_hidden_and_refuse_offline(env):
    (env.root / ".spinebox").mkdir()
    (env.root / ".hidden").mkdir()
    listing = organizer(env).range_folders(env.root_id)
    assert listing["folders"] == ["そのほか", "小説", "漫画"] and listing["parent"] is None
    assert organizer(env).range_folders(env.root_id, "漫画/作者A") == {
        "root_id": env.root_id, "path": "漫画/作者A", "parent": "漫画", "folders": []}
    with db.transaction(env.conn) as c:
        c.execute("UPDATE library_roots SET online = 0")
    with pytest.raises(RangeError, match="オフライン"):
        organizer(env).range_folders(env.root_id)


def test_range_path_with_different_case_on_a_case_insensitive_filesystem(env):
    """Windows ではフォルダの大文字小文字を変えて書いても同じフォルダを指す。DB の相対パスの大文字小文字と
    食い違って「巻がありません」にならず、出力先のフォルダもディスク上の綴りになる(agy レビュー)。"""
    cbz(env.root / "Manga" / "Sub" / f"{TITLE} 9巻.cbz", "case9")
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    if not (env.root / "manga").exists():
        pytest.skip("大文字小文字を区別するファイルシステム")
    plan = plan_range(env, "manga/SUB", "{title} 第{number:2}{unit}", "{title}")
    assert plan["scope"]["path"] == "Manga/Sub"
    item = by_from(plan)[f"Manga/Sub/{TITLE} 9巻.cbz"]
    assert item["status"] == "move" and item["output_rel_path"].startswith("Manga/Sub/")


def test_include_cbz_plan_does_not_query_per_volume(env):
    """include_cbz の計画で、1 巻 = 1 CBZ の巻ごとに同じ sources の SELECT を発行しない(agy レビュー)。"""
    statements: list[str] = []

    def count_statements() -> int:
        statements.clear()
        plan_range(env, "漫画", include_cbz=True)
        return sum(s.lstrip().upper().startswith("SELECT") for s in statements)

    env.conn.set_trace_callback(statements.append)
    try:
        before = count_statements()
        for i in range(10, 20):
            cbz(env.root / "漫画" / "多" / f"{TITLE}　{i}巻.cbz", f"many{i}")
        Scanner(env.conn, env.settings).scan()
        works.sync(env.conn)
        after = count_statements()
    finally:
        env.conn.set_trace_callback(None)
    assert after <= before + 2, (before, after)


def test_name_values_does_not_query_per_volume(env):
    """範囲は蔵書全体の巻をまとめて渡すので、巻の数に比例して SQL を発行しない(agy レビュー。N+1 の禁止)。
    作品ごとの項目は作品の数だけ・巻の行と書誌は 500 件ずつまとめて引く。"""
    ids = [r["id"] for r in env.conn.execute("SELECT id FROM volumes")]
    statements: list[str] = []
    env.conn.set_trace_callback(statements.append)
    try:
        once = organizer(env).name_values(ids)
        n_once = len(statements)
        statements.clear()
        many = organizer(env).name_values(ids * 100)  # 重複は 1 回にまとめる
        n_many = len(statements)
    finally:
        env.conn.set_trace_callback(None)
    assert many == once and n_many == n_once
    n_works = env.conn.execute("SELECT COUNT(DISTINCT work_id) FROM work_volumes").fetchone()[0]
    assert n_once <= 2 + 2 * n_works  # 巻の行・書誌の 2 回 + 作品ごとの 2 回(吸収先・項目)
