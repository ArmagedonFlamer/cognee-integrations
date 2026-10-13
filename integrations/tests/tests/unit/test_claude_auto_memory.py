"""Native-memory settings ownership and restoration."""

import json

import pytest


@pytest.fixture
def am(suite, isolated_modules):
    if suite.name != "claude-code":
        pytest.skip("Claude settings")
    return isolated_modules(suite, "_auto_memory")


def test_set_once_restore_missing_value(am, monkeypatch):
    settings, marker = am._paths()
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"statusLine": "mine"}))
    assert "next session" in am.configure()
    assert am.configure() == ""
    assert am.state()["plugin_owned"]
    assert json.loads(settings.read_text()) == {"statusLine": "mine", "autoMemoryEnabled": False}
    monkeypatch.setenv("COGNEE_DISABLE_AUTO_MEMORY", "false")
    assert "restored" in am.configure()
    assert json.loads(settings.read_text()) == {"statusLine": "mine"}
    assert not marker.exists()


def test_explicit_true_is_respected_and_reported_once(am):
    settings, _ = am._paths()
    settings.parent.mkdir(parents=True)
    settings.write_text('{"autoMemoryEnabled":true}')
    assert "unchanged" in am.configure()
    assert am.configure() == ""
    assert settings.read_text() == '{"autoMemoryEnabled":true}'
    assert not am.restore()


def test_preexisting_false_is_not_owned(am):
    settings, marker = am._paths()
    settings.parent.mkdir(parents=True)
    settings.write_text('{"autoMemoryEnabled":false}')
    assert am.configure() == ""
    assert not marker.exists()
    assert not am.restore()


def test_user_edit_after_plugin_write_survives_cleanup(am):
    am.configure()
    settings, marker = am._paths()
    settings.write_text('{"autoMemoryEnabled":true,"theme":"dark"}')
    assert am.restore()
    assert json.loads(settings.read_text()) == {"autoMemoryEnabled": True, "theme": "dark"}
    assert not marker.exists()


def test_corrupt_settings_preserved(am):
    settings, marker = am._paths()
    settings.parent.mkdir(parents=True)
    settings.write_text("{bad")
    with pytest.raises(ValueError):
        am.configure()
    assert settings.read_text() == "{bad"
    assert not marker.exists()


def test_renderer_cleanup_restores_setting(am, suite, isolated_modules):
    am.configure()
    renderer = isolated_modules(suite, "cognee_statusline_render")
    renderer._evict_own_statusline()
    assert am.state()["enabled"] == "default"
