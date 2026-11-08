import pytest

from spinebox.mojibake import fix_mojibake
from spinebox.naming import parse_number, parse_work_name, trailing_number
from spinebox.organize.cleanup import clean_name


# --- 文字化けの検出と修復 -------------------------------------------------------------

@pytest.mark.parametrize("original, wrong, right", [
    # 欧文 / DOS コードページで誤読された Shift_JIS (実例)
    ("週刊少年サンデー 2023年09号", "cp932", "cp437"),
    ("週覚少年サンデー 2023年09号", "cp932", "cp437"),
    ("魔都精兵のスレイブ 第14巻", "cp932", "cp437"),
    ("東京物語", "cp932", "cp437"),
    ("ある作品 第05話", "cp932", "cp437"),
    ("ある作品 第05話", "cp932", "cp850"),
    ("お気楽領主の楽しい領地防衛 第2巻", "cp932", "latin-1"),
    # 欧文コードページで誤読された UTF-8
    ("ある作品 第05話", "utf-8", "latin-1"),
    ("ある作品 第05話", "utf-8", "cp437"),
    ("お気楽領主の楽しい領地防衛 第2巻", "utf-8", "latin-1"),
    # Shift_JIS として誤読された UTF-8 (縺九￥...)
    ("かくかくしかじか 第2巻", "utf-8", "cp932"),
    ("こんにちは", "utf-8", "cp932"),
])
def test_fix_mojibake_various_patterns(original, wrong, right):
    broken = original.encode(wrong).decode(right)
    fixed, note = fix_mojibake(broken)
    assert fixed == original
    assert note is not None
    assert "文字化け" in note


def test_fix_mojibake_specific_user_reported_cases():
    # ユーザー報告の実例 1: CP437 でデコードされた少年サンデー
    s = "ÅTèoÅ¡öNâTâôâfü[ 2023öN09ìå"
    fixed, note = fix_mojibake(s)
    assert fixed == "週覚少年サンデー 2023年09号"
    assert note is not None

    # ユーザー報告の実例 2: CP437 でデコードされた魔都精兵のスレイブ
    s2 = "魔都精兵のスレイブ 第14巻相当 2023年08月14日更新".encode("cp932").decode("cp437")
    fixed2, note2 = fix_mojibake(s2)
    assert fixed2 == "魔都精兵のスレイブ 第14巻相当 2023年08月14日更新"
    assert note2 is not None


# --- 誤判定防止 (本物の欧文単語や正常な日本語を壊さない) ------------------------------

@pytest.mark.parametrize("name", [
    # 欧文のアクセント文字付き単語
    "café au lait",
    "Ünïcödé",
    "Amélie",
    "naïve",
    "résumé",
    "François",
    "München",
    "Hôtel de Paris",
    "Schrödinger",
    "Götterdämmerung",
    "Renée",
    "São Paulo",
    "Zürich",
    "El Niño",
    # 正常な日本語
    "東京",
    "鬼滅の刃 23",
    "かくかくしかじか 第1巻",
    "ある作品 第05話",
    "ﾃｽﾄ",
    "Ｒｅ：ゼロ",
    "English Title Only",
    "12345",
])
def test_fix_mojibake_does_not_break_legitimate_names(name):
    fixed, note = fix_mojibake(name)
    assert note is None
    assert fixed == name


# --- naming との連携 -----------------------------------------------------------------

def test_naming_extracts_from_mojibake():
    # 魔都精兵のスレイブ 第14巻 の CP437 化け文字列
    broken = "魔都精兵のスレイブ 第14巻".encode("cp932").decode("cp437")

    # 1. parse_number
    num = parse_number(broken)
    assert num is not None
    assert num.kind == "volume"
    assert num.start == 14
    assert num.end == 14
    assert num.evidence == "第14巻"

    # 2. parse_work_name
    work = parse_work_name(broken)
    assert work is not None
    assert work.title == "魔都精兵のスレイブ"
    assert work.norm == "魔都精兵のすれいぶ"
    assert work.number is not None
    assert work.number.start == 14

    # 3. trailing_number
    broken_trailing = "魔都精兵のスレイブ 14".encode("cp932").decode("cp437")
    tn = trailing_number(broken_trailing)
    assert tn is not None
    assert tn.number == 14
    assert tn.base == "魔都精兵のスレイブ"


# --- cleanup との連携 ----------------------------------------------------------------

def test_cleanup_name_fixes_cp437_mojibake():
    broken = "魔都精兵のスレイブ 第14巻".encode("cp932").decode("cp437")
    cleaned = clean_name(broken + ".cbz")
    assert cleaned.name == "魔都精兵のスレイブ 第14巻.cbz"
    assert any("文字化け" in c for c in cleaned.changes)
