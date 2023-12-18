from cfg import get_config


def test_dummy(monkeypatch):
    monkeypatch.setenv("FOO", "BAR")
    foo = get_config("FOO")
    assert foo == "BAR"
