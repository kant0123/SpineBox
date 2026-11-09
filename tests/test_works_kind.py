from spinebox import works


def test_doujinshi_is_a_kind_and_pairs_with_manga_candidates():
    assert "doujinshi" in works.KINDS
    assert works.kind_compatible("doujinshi", "manga")      # 公開 DB は同人誌を漫画として返す
    assert works.kind_compatible("doujinshi", None)
    assert works.kind_compatible(None, "novel")
    assert not works.kind_compatible("doujinshi", "novel")
    assert not works.kind_compatible("manga", "doujinshi")
    assert not works.kind_compatible("manga", "novel")
