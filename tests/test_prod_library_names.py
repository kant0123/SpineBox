"""本番の蔵書(2026-11-09 の初回同期)で見つかった名前の形の読み方と、それによる作品のまとまり。"""
import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip
from spinebox import db
from spinebox.app import create_app
from spinebox.mojibake import fix_mojibake, looks_garbled
from spinebox.naming import parse_number, parse_work_name
from spinebox.scanner import software_doc_reason


@pytest.mark.parametrize("name, title, number", [
    # 版の印の付いた番号
    ("Hiroinwaetsshimashit_04w", "Hiroinwaetsshimashit", 4),
    ("MakainoShuyakuwa_13s", "MakainoShuyakuwa", 13),
    ("DLRAW.NET-劇光仮面1s", "劇光仮面", 1),
    ("Sudachi no Maoujou 05s", "Sudachi no Maoujou", 5),
    ("異世界行っても少年マンガの主人公は1ミリもブレない!!!2s", "異世界行っても少年マンガの主人公は1ミリもブレない!!!", 2),
    # 番号の後ろの著者列・ローマ数字の巻
    ("一瞬で治療していたのに役立たずと追放された天才治癒師、闇ヒーラーとして楽しく生きる（コミック） １ 十乃壱 天 & 菱川 さかく & だぶ竜",
     "一瞬で治療していたのに役立たずと追放された天才治癒師、闇ヒーラーとして楽しく生きる", 1),
    ("現実主義勇者の王国再建記IV 上田悟司 & どぜう丸 & 冬ゆき", "現実主義勇者の王国再建記", 4),
    ("現実主義勇者の王国再建記XIII 上田悟司 & どぜう丸 & 冬ゆき", "現実主義勇者の王国再建記", 13),
    ("転生コロシアム 1 ～最弱スキル～ 転生コロシアム ～最弱スキル～ zunta & はらわたさいぞう", "転生コロシアム", 1),
    # 先頭の印・取り残された括弧・著者の括弧の後ろの配布元
    ("★さわらないで小手指くん", "さわらないで小手指くん", None),
    ("ありふれた日常で世界最強【第37話】 Maga Raw Plus", "ありふれた日常で世界最強", 37),
    ("[安部真弘] DLRAW.AC_ あつまれ！ふしぎ研究部 第01巻", "あつまれ!ふしぎ研究部", 1),
    # 雑誌の号は誌名が作品名で、号は巻の番号にしない
    ("週刊少年サンデー 2021年36-37号", "週刊少年サンデー", None),
    ("週刊少年サンデー 2023年4・5合併号", "週刊少年サンデー", None),
    ("廡姧彮擭僒儞僨乕 2021擭36-37崋", "週刊少年サンデー", None),  # Shift_JIS を GBK として読んだもの
    # 直せる語だけ直した文字化け(著者の括弧は直せない)
    ("DLRAW.NET-[é¦é+éGé¦é-éµé¦] ânâôâcü[ü~âgâëâbâVâà æµ10è¬", "ハンツー×トラッシュ", 10),
    # ローマ字の作品名の後ろの配布の印
    ("matoseiheino jp v02s", "matoseiheino", 2),
    ("matoseiheino_color_v02", "matoseiheino", 2),
    ("Sekaisaiksashinisekai jp", "Sekaisaiksashinisekai", None),
])
def test_names_seen_in_the_production_library(name, title, number):
    wn = parse_work_name(name)
    assert wn.title == title
    assert (wn.number.start if wn.number else None) == number
    assert (parse_number(name).start if parse_number(name) else None) == number


@pytest.mark.parametrize("name", ["Back to the 80s", "狂四郎2030", "聖☆おにいさん", "History of Japan"])
def test_names_that_keep_their_digits_and_marks(name):
    wn = parse_work_name(name)
    assert wn.title == name and wn.number is None


def test_garbled_names_are_not_titles():
    for name in ("ûéôsÉ+ò¦é¦âXâîâCâuæµ14è¬æèôû2023öN08îÄ14ô·ìXÉV",
                 "逡ｰ荳也阜繧ｯ繝ｩ繝輔ヨ縺上ｙ繧峨＠� 5蟾ｻ",
                 "霑ｽ謾ｾ縺輔ｌ縺溯ｻ｢逕滄㍾鬨主｣ｫ縺ｯ繧ｱ繧吶�遏･"):
        assert looks_garbled(fix_mojibake(name)[0])
        assert parse_work_name(name) is None
    for name in ("Amélie Poulain", "café crème", "Pokémon Adventures", "進撃の巨人", "天国大魔境", "Ñandú",
                 "château gâteau grâce", "Crème brûlée à la française", "São Paulo", "Ελληνικά", "Москва",
                 "动物人间", "进击的巨人", "海贼王 航海王", "鬼灭之刃"):  # 中国語は GBK に戻しても cp932 のかなにならない
        assert not looks_garbled(name) and fix_mojibake(name)[1] is None


def test_software_docs_are_not_novels():
    assert software_doc_reason("README.txt", [])
    assert software_doc_reason("CHANGELOG.txt", [])
    assert software_doc_reason("readme_jp.txt", [])
    assert software_doc_reason("memo.txt", ["memo.txt", "briss.exe"])
    assert software_doc_reason("History of Japan.txt", []) is None
    assert software_doc_reason("異世界転生.txt", ["異世界転生.txt"]) is None


# --- 作品のまとまり ------------------------------------------------------------------------


def cbz(lib, rel, pages=3):
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    write_zip(path, [(f"{i:03d}.jpg", page_bytes(f"{rel}/{i}")) for i in range(pages)])


@pytest.fixture
def env(make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()

    def scan():
        settings = make_settings(lib)
        client = TestClient(create_app(settings, frontend_dist=None))
        client.post("/api/scan")
        return settings, client
    return lib, scan


def titles(client):
    return sorted((w["title"], w["volume_count"]) for w in client.get("/api/works").json()["works"])


def test_a_garbled_volume_does_not_split_a_japanese_folder(env):
    """直せない文字化けの巻があっても、日本語のフォルダの下のローマ字の巻はそのフォルダの作品になる。"""
    lib, scan = env
    cbz(lib, "ハンツー×トラッシュ/ハンツー×トラッシュ 1.cbz")
    cbz(lib, "ハンツー×トラッシュ/ハンツー×トラッシュ 2.cbz")
    cbz(lib, "ハンツー×トラッシュ/DLRAW.NET-Hantsu Torasshu vol 01-18/Hantsu Torasshu v03.cbz")
    cbz(lib, "ハンツー×トラッシュ/DLRAW.NET-Hantsu Torasshu vol 01-18/Hantsu x Torasshu v04.cbz")
    cbz(lib, "ハンツー×トラッシュ/DLRAW.NET-Hantsu Torasshu vol 01-18/ûéôsÉ+ò¦é¦âXâîâCâuæµ14è¬.cbz")
    _, client = scan()
    assert titles(client) == [("ハンツー×トラッシュ", 5)]


def test_volumes_with_author_lists_and_roman_numerals_join_their_folder(env):
    lib, scan = env
    for n in ("I", "II", "III"):
        cbz(lib, f"現実主義勇者の王国再建記/現実主義勇者の王国再建記{n} 上田悟司 & どぜう丸 & 冬ゆき.cbz")
    for n in (1, 2):
        cbz(lib, f"転生コロシアム ~最弱スキル~/転生コロシアム {n} ～最弱スキル～ 転生コロシアム ～最弱スキル～ zunta & はらわた.cbz")
    _, client = scan()
    assert titles(client) == [("現実主義勇者の王国再建記", 3), ("転生コロシアム ~最弱スキル", 2)]
    w = next(w for w in client.get("/api/works").json()["works"] if w["title"] == "現実主義勇者の王国再建記")
    assert w["max_volume"] == 3


def test_magazine_issues_become_one_work(env):
    lib, scan = env
    for issue in ("2021年25号", "2021年26号", "2021年36-37号", "2022年4・5合併号"):
        cbz(lib, f"雑誌/週刊少年サンデー {issue}.cbz")
    _, client = scan()
    assert titles(client) == [("週刊少年サンデー", 4)]


def test_marked_volumes_join_the_same_series(env):
    lib, scan = env
    for n in ("04w", "05w", "06w"):
        cbz(lib, f"ヒロインは絶望しました。/Hiroin wa Zetsubo Shimashita v04-06/Hiroinwaetsshimashit_{n}.cbz")
    cbz(lib, "まとめ/劇光仮面1s.cbz")
    cbz(lib, "まとめ/劇光仮面2s.cbz")
    _, client = scan()
    assert titles(client) == [("ヒロインは絶望しました。", 3), ("劇光仮面", 2)]


def test_software_docs_in_the_library_are_skipped_with_a_reason(env):
    lib, scan = env
    (lib / "noInstallProg/Soft/briss").mkdir(parents=True)
    (lib / "noInstallProg/Soft/briss/CHANGELOG.txt").write_text("v0.9", encoding="utf-8")
    (lib / "noInstallProg/Soft/briss/notes.txt").write_text("x", encoding="utf-8")
    (lib / "noInstallProg/Soft/briss/briss.exe").write_bytes(b"MZ")
    (lib / "小説").mkdir()
    (lib / "小説/異世界転生.txt").write_text("本文", encoding="utf-8")
    settings, client = scan()
    assert titles(client) == [("異世界転生", 1)]
    conn = db.connect(settings.db_path)
    try:
        reasons = [r[0] for r in conn.execute("SELECT reason FROM scan_issues")]
    finally:
        conn.close()
    assert sum(r.startswith("登録不要") for r in reasons) == 2


def test_subtitle_variants_of_a_japanese_title_are_merged(env):
    """作品名に区切りと副題を足しただけの作品は同じ作品(区切りの無い続き `…農家の日常` は別作品)。"""
    lib, scan = env
    cbz(lib, "a/フシノカミ 8.cbz")
    cbz(lib, "b/[著者] フシノカミ ~辺境から始める文明再生記 第01巻.cbz")
    cbz(lib, "b/[著者] フシノカミ ~辺境から始める文明再生記 第02巻.cbz")
    cbz(lib, "c/[著者] 異世界のんびり農家 第01巻.cbz")
    cbz(lib, "d/[著者] 異世界のんびり農家の日常 第01巻.cbz")
    _, client = scan()
    assert titles(client) == [("フシノカミ ~辺境から始める文明再生記", 3), ("異世界のんびり農家", 1),
                              ("異世界のんびり農家の日常", 1)]
    w = next(w for w in client.get("/api/works").json()["works"] if w["title"].startswith("フシノカミ"))
    merges = client.get(f"/api/works/{w['id']}").json()["merges"]
    assert [(m["rule"], m["status"]) for m in merges] == [("alias", "auto")]


def test_romaji_spelling_variants_in_one_archive_join_the_japanese_work(env):
    lib, scan = env
    write_zip(lib / "Sudachi no mao-jo v01s-05s.zip", [
        *((f"Sudachi_no_Maoujou_05s/{i}.jpg", page_bytes(f"s5{i}")) for i in range(2)),
        *((f"[森下真] すだちの魔王城 第0{n}巻/{i}.jpg", page_bytes(f"s{n}{i}")) for n in (1, 2) for i in range(2)),
    ])
    _, client = scan()
    assert titles(client) == [("すだちの魔王城", 3)]


def test_a_romaji_folder_name_inside_a_japanese_folder_merges_a_romaji_only_work(env):
    """日本語のフォルダの中のローマ字のフォルダ名は別名に残り、別の場所にそのローマ字の名前だけで届いた巻の
    作品を統合する(実データ: `DLRAW.AC_100_files` … の章 60 冊)。"""
    lib, scan = env
    cbz(lib, "漫画/異世界のんびり素材採取生活/Isekai_nonbiri_sozai_saishu_seikatsu_v01-03/[著者] 異世界のんびり素材採取生活 第01巻.cbz")
    cbz(lib, "漫画/異世界のんびり素材採取生活/Isekai_nonbiri_sozai_saishu_seikatsu_v01-03/[著者] 異世界のんびり素材採取生活 第02巻.cbz")
    cbz(lib, "temp/Isekai nonbiri sozai saishu seikatsu/Isekai nonbiri sozai saishu seikatsu v04.cbz")
    _, client = scan()
    assert titles(client) == [("異世界のんびり素材採取生活", 3)]
