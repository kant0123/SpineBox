import pytest

from spinebox.archive.safety import entry_name_problem, normalize_entry_name


@pytest.mark.parametrize("name", [
    "../x.jpg", "a/../../x.jpg", "a\\..\\x.jpg", "/abs.jpg", "\\abs.jpg", "C:\\x.jpg", "c:x.jpg",
    "\\\\server\\share\\x.jpg", "//server/share/x.jpg", "a\x00b.jpg", "",
])
def test_unsafe_names_rejected(name):
    assert entry_name_problem(name)


@pytest.mark.parametrize("name", ["a/b.jpg", "..foo.jpg", "a..b/c.jpg", "第1巻/001.jpg", "dir/"])
def test_safe_names_accepted(name):
    assert entry_name_problem(name) is None


def test_normalize():
    assert normalize_entry_name("a\\b\\c.jpg") == "a/b/c.jpg"
    assert normalize_entry_name("./a//b.jpg") == "a/b.jpg"
    assert normalize_entry_name("dir\\") == "dir/"
