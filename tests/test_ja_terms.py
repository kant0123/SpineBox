"""公開 DB の英語の語彙を日本語にする対応表(#74)。ネットワークに出ない。"""
from __future__ import annotations

import json

import pytest

from metadata_fakes import FIXTURES
from spinebox import ja_terms
from spinebox.metadata import anilist, mangaupdates


@pytest.mark.parametrize("term, expected", [
    ("Slice of Life", "日常"),
    ("slice of life", "日常"),            # 大文字小文字
    ("Teacher/s", "教師"),                # MangaUpdates の複数形
    ("Teacher", "教師"),
    ("Coming-of-Age", "成長"),            # ハイフンと空白
    ("Biography / Autobiography", "伝記・自伝"),
    ("Biography/Autobiography", "伝記・自伝"),
    ("Ｓｈｏｕｎｅｎ", "少年向け"),          # 全角
    ("日常", "日常"),                     # 日本語の語はそのまま
    ("Unheard-of Tag", None),
    ("", None),
])
def test_term_ja(term, expected):
    assert ja_terms.term_ja(term) == expected


def test_localize_terms_merges_the_same_translation_and_keeps_order():
    terms = ["Yaoi", "Drama", "Shounen Ai", "Unknown", "Boys' Love", "  "]
    assert ja_terms.localize_terms(terms, keep_unknown=False) == ["BL", "ドラマ"]
    assert ja_terms.localize_terms(terms, keep_unknown=True) == ["BL", "ドラマ", "Unknown"]


@pytest.mark.parametrize("name, expected", [
    ("Shueisha", "集英社"), ("overlap", "オーバーラップ"), ("集英社", "集英社"),
    ("Some Small Press", "Some Small Press"),  # 対応が無いローマ字名は固有名なので隠さない
    ("", None), (None, None),
])
def test_publisher_ja(name, expected):
    assert ja_terms.publisher_ja(name) == expected


@pytest.mark.parametrize("text, expected", [
    ("国立国会図書館の内容紹介です。", True),
    ("2 歳で前世の記憶を取り戻した Van は…", True),
    ("Van, the fourth son of a noble, remembers his past life (前世の記憶).", False),  # 題名だけ日本語が混ざる英語
    ("1. Sexaroid Girl (セクサロイドガール)\n2. Twin Steady! (ツインステディ)", False),
    ("日本語の題名(Original Title in English)の紹介", True),
    ("(セクサロイドガール)", False),  # 括弧の中しか無い
    ("", False), (None, False), ("…", False),
])
def test_is_japanese(text, expected):
    assert ja_terms.is_japanese(text) is expected


def test_every_term_in_the_recorded_responses_has_a_translation():
    """記録済みの応答(`tests/fixtures/metadata/`)に出るジャンル・タグ・原版元は、すべて対応表で引ける。"""
    terms: set[str] = set()
    publishers: set[str] = set()
    for path in sorted((FIXTURES / "mangaupdates").glob("*-detail-*.json")):
        item = mangaupdates.parse_detail(path.read_bytes())
        terms |= {*item.genres, *item.tags}
        if item.publisher:
            publishers.add(item.publisher)
    for path in sorted((FIXTURES / "anilist").glob("*-search.json")):
        for item in anilist.parse_search(path.read_bytes()):
            terms |= {*item.genres, *item.tags}
    assert terms and publishers
    assert sorted(t for t in terms if ja_terms.term_ja(t) is None) == []
    assert sorted(p for p in publishers if ja_terms.publisher_ja(p) == p) == []


def test_recorded_english_synopses_are_not_taken_as_japanese():
    """記録済みの AniList・MangaUpdates のあらすじ(英語。日本語の題名が括弧で混ざるものを含む)は日本語と判定しない。"""
    items = [mangaupdates.parse_detail(p.read_bytes()) for p in (FIXTURES / "mangaupdates").glob("*-detail-*.json")]
    items += [i for p in (FIXTURES / "anilist").glob("*-search.json") for i in anilist.parse_search(p.read_bytes())]
    synopses = [i.synopsis for i in items if i.synopsis]
    assert len(synopses) >= 10
    assert [s[:40] for s in synopses if ja_terms.is_japanese(s)] == []


def test_localize_summary_does_not_touch_the_original():
    summary = {"publisher": "Overlap", "genres": ["Shounen", "Rare Genre"], "title": "お気楽領主"}
    out = ja_terms.localize_summary(summary)
    assert out == {"publisher": "オーバーラップ", "genres": ["少年向け", "Rare Genre"], "title": "お気楽領主"}
    assert summary["publisher"] == "Overlap" and json.dumps(summary)
