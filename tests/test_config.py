from pathlib import Path

import pytest

from spinebox.config import CONFIG_ENV, GIB, ConfigError, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    s = load_settings()
    assert s.library_roots == ()
    assert s.data_dir == tmp_path / "data"
    assert s.cache_limit_bytes == 2 * GIB
    assert (s.host, s.port, s.sevenzip_path) == ("127.0.0.1", 8000, "7z")
    assert s.limits.max_depth == 3


def test_env_var_and_relative_paths(tmp_path, monkeypatch):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    conf = conf_dir / "spinebox.toml"
    abs_root = tmp_path / "abs_comics"  # Windows では /srv/x は絶対パスではないので OS 依存にしない
    conf.write_text(
        f'library_roots = ["books", "{abs_root.as_posix()}"]\n'
        'data_dir = "state"\ncache_limit_bytes = 1024\nhost = "0.0.0.0"\nport = 9000\n'
        '[limits]\nmax_entries = 5\nmax_depth = 0\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV, str(conf))
    monkeypatch.chdir(tmp_path)
    s = load_settings()
    # 相対パスはカレントではなく設定ファイルの場所が起点
    assert s.library_roots[0] == conf_dir / "books"
    assert s.library_roots[1] == abs_root
    assert s.data_dir == conf_dir / "state"
    assert (s.cache_limit_bytes, s.host, s.port) == (1024, "0.0.0.0", 9000)
    assert (s.limits.max_entries, s.limits.max_depth) == (5, 0)


def test_config_local_toml_in_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    (tmp_path / "config.local.toml").write_text('port = 8123\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_settings().port == 8123


def test_missing_env_config_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, str(tmp_path / "nope.toml"))
    with pytest.raises(ConfigError):
        load_settings()


@pytest.mark.parametrize("body", [
    'library_roots = "E:/Books"',
    'library_roots = [1]',
    'cache_limit_bytes = 0',
    'cache_limit_bytes = true',
    'port = "80"',
    '[limits]\nmax_entries = -1',
    'not toml ===',
])
def test_invalid_values(tmp_path, body):
    conf = tmp_path / "c.toml"
    conf.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(conf)


def test_example_config_parses():
    s = load_settings(ROOT / "config.example.toml")
    assert s.library_roots == ()
    assert s.cache_limit_bytes == 2 * GIB
    assert s.host == "127.0.0.1"


def _run_main(monkeypatch, tmp_path, host):
    import sys
    import types

    from spinebox import __main__ as entry

    conf = tmp_path / "c.toml"
    conf.write_text(f'host = "{host}"\nport = 9001\ndata_dir = "{(tmp_path / "d").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV, str(conf))
    calls = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: calls.append(kw)))
    assert entry.main() == 0
    return calls


def test_main_warns_only_when_listening_beyond_loopback(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, "127.0.0.1") == [{"host": "127.0.0.1", "port": 9001}]
    assert capsys.readouterr().err == ""
    _run_main(monkeypatch, tmp_path, "0.0.0.0")
    err = capsys.readouterr().err
    assert "0.0.0.0:9001" in err and "インターネット" in err


def test_print_bind_prints_host_and_port_without_starting(monkeypatch, tmp_path, capsys):
    # 常駐ラッパーが、起動前に孤児がポートを握っていないか調べるために使う
    import sys
    import types

    from spinebox import __main__ as entry

    conf = tmp_path / "c.toml"
    conf.write_text(f'host = "0.0.0.0"\nport = 9002\ndata_dir = "{(tmp_path / "d").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV, str(conf))
    calls = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: calls.append(kw)))
    assert entry.main(["--print-bind"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "0.0.0.0 9002\n" and captured.err == "" and calls == []  # 注意文も出さず、起動しない


def test_print_bind_reports_a_config_error(monkeypatch, tmp_path, capsys):
    from spinebox import __main__ as entry

    conf = tmp_path / "c.toml"
    conf.write_text('port = "x"\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV, str(conf))
    assert entry.main(["--print-bind"]) == 2
    assert capsys.readouterr().out == ""
