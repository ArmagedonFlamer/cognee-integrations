"""SDK-1109: rendering contract and frozen 1.6.21 compatibility oracle."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from utils.isolation import build_env
from utils.statusline import write_json

NOW = 1_800_000_000
SESSION = "test-session"


@pytest.fixture
def sl(suite, statusline, monkeypatch):
    if suite.name != "claude-code":
        pytest.skip("Claude Code presentation only")
    monkeypatch.delenv("COGNEE_STATUSLINE_STYLE", raising=False)
    monkeypatch.setenv("COGNEE_PLUGIN_DATASET", "manual")
    monkeypatch.setenv("COGNEE_BASE_URL", "https://memory.example.test")
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setattr(statusline, "_unicode_output", lambda: True)
    monkeypatch.setattr(statusline.time, "time", lambda: NOW)
    write_json(statusline._USER_SETTINGS, {"enabledPlugins": {"cognee-memory@cognee": True}})
    write_json(
        statusline._SERVER_READY_PATH,
        {
            "state": "ready",
            "checked_at": NOW,
            "ready_at": NOW,
            "base_url": "https://memory.example.test",
        },
    )
    write_json(statusline._SESSIONS_DIR / SESSION / "stats.json", {"entries_injected": 124})
    write_json(statusline._RECALL_DIR / f"{SESSION}.json", marker())
    return statusline


def marker(**changes):
    return {
        "session_id": "server-session",
        "session_key": SESSION,
        "ts": NOW - 20,
        "hits": {"session": 8},
        "session_totals": {"turns": 46, "turns_with_hits": 34},
        **changes,
    }


def plain(sl, line):
    return sl._ANSI.sub("", line)


@pytest.mark.parametrize("theme,color", [("dark", 141), ("light", 93)])
def test_palette_and_meaning(sl, monkeypatch, theme, color):
    monkeypatch.setenv("COGNEE_STATUSLINE_THEME", theme)
    line = sl._tetris_line(SESSION)
    assert f"\033[38;5;{color}m■" in line
    assert f"\033[38;5;{color}mcognee: manual" in line
    assert f"\033[38;5;{color}mcloud" in line
    assert "●" not in line
    assert "[124 recalled]" in line and "8 hits" in line and "34/46 turns" in line
    assert "▰▰▰▰▱" in line


def test_width_and_history(sl, monkeypatch):
    audit = sl._RECALL_DIR.parent / "recall-audit.log"
    audit.write_text(
        "\n".join(json.dumps(marker(ts=NOW - 28 + i, hits={"session": i})) for i in range(8))
    )
    at80 = sl._tetris_line(SESSION)
    assert sl._width(at80) <= 80
    assert "▰" in at80
    monkeypatch.setenv("COLUMNS", "120")
    assert sl._tetris_line(SESSION) == at80
    monkeypatch.setenv("COLUMNS", "60")
    line = sl._tetris_line(SESSION)
    assert sl._width(line) <= 60 and "▰" not in line
    assert all(text in line for text in ("■", "manual", "cloud", "124 recalled"))
    monkeypatch.setenv("COLUMNS", "20")
    assert sl._width(sl._tetris_line(SESSION)) <= 20


def test_stateless_drop_and_shimmer(sl, monkeypatch):
    write_json(sl._RECALL_DIR / f"{SESSION}.json", marker(ts=NOW))
    initial = sl._tetris_line(SESSION, now=NOW)
    assert initial == sl._tetris_line(SESSION, now=NOW)
    assert "▔" in initial and "\033[38;5;183m[124 recalled]" in initial
    assert "▀" in sl._tetris_line(SESSION, now=NOW + 2)
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert "\033[38;2;208;180;255m[124 recalled]" in sl._tetris_line(SESSION, now=NOW)
    assert sl._tetris_line(SESSION, now=NOW + 10) == sl._tetris_line(SESSION, now=NOW + 60)
    assert "█" in sl._tetris_line(SESSION, now=NOW + 10)
    assert sl._animation_phase(marker(hits={"session": 0}, ts=NOW), NOW) is None


def test_history_is_bounded_attributable_and_handles_rotation(sl):
    root = sl._RECALL_DIR.parent
    rows = [marker(ts=NOW - 40 + i, hits={"session": i}) for i in range(12)]
    (root / "recall-audit.log.1").write_text("\n".join(map(json.dumps, rows)))
    (root / "recall-audit.log").write_text(
        "x" * (sl._HISTORY_BYTES + 10)
        + "\n"
        + json.dumps(marker(session_id="someone-else", hits={"session": 999}))
        + "\n{broken\n"
    )
    assert sl._recent_hits(SESSION, marker()) == [5, 6, 7, 8, 9, 10, 11, 8]
    assert sl._recent_hits("../outside", marker()) == []
    (root / "recall-audit.log.1").unlink()
    assert sl._recent_hits(SESSION, marker()) == [8]
    assert sl._recent_hits(SESSION, {}) == []


@pytest.mark.parametrize(
    "turns,hits,expected", [(0, 0, "▱▱▱▱▱"), (1, 1, "▰▰▰▰▰"), (46, 34, "▰▰▰▰▱"), (3, 0, "▱▱▱▱▱")]
)
def test_ratio(sl, turns, hits, expected):
    assert sl._ratio_bar(turns, hits) == expected
    assert sl._stack([0, 1, 4, 8], None) == "·▁▄█"


@pytest.mark.parametrize("mode", ["full", "false"])
def test_counts_modes(sl, monkeypatch, mode):
    monkeypatch.setenv("COGNEE_STATUSLINE_COUNTS", mode)
    monkeypatch.setenv("COLUMNS", "120")
    line = sl._tetris_line(SESSION)
    assert "[124 recalled]" in line
    assert ("recall 8s/0t/0g/0a" in line) == (mode == "full")
    assert "▰" not in line


def test_failure_and_semantic_colors(sl, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setattr(sl, "_status_prefix", lambda _: sl._fail_glyph("server_error"))
    monkeypatch.setattr(sl, "_credits_segment", lambda: " · \033[32mcredits: $5.00\033[0m")
    monkeypatch.setattr(sl, "_update_segment", lambda: " · \033[33mupdate\033[0m")
    line = sl._tetris_line(SESSION)
    assert "\033[1;31m✕ (server_error)" in line
    assert "\033[32mcredits: $5.00" in line and "\033[33mupdate" in line
    assert "■" not in line


def test_render_exception_falls_back(sl, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": SESSION})))

    def broken(_):
        raise ValueError("partial marker")

    monkeypatch.setattr(sl, "_tetris_line", broken)
    expected = sl._classic_line(SESSION)
    sl.main()
    assert capsys.readouterr().out == expected


def run_renderer(suite, home, source, payload, **extra):
    env = build_env(suite, home)
    env.update(COGNEE_PLUGIN_DATASET="manual", COLUMNS="80", PYTHONPATH=str(suite.scripts_dir))
    env.update(extra)
    return subprocess.run(
        [sys.executable, str(source)],
        input=payload.encode(),
        capture_output=True,
        env=env,
        timeout=10,
    )


@pytest.mark.parametrize(
    "payload",
    ["", "garbage", "[]", '{"workspace":42}', '{"session_id":"../outside"}', '{"session_id":"."}'],
)
def test_bad_input_exits_cleanly(sl, suite, temp_home, payload):
    result = run_renderer(suite, temp_home, sl.__file__, payload, COLUMNS="20")
    assert result.returncode == 0 and result.stderr == b""
    assert len(result.stdout.splitlines()) == 1
    assert sl._width(result.stdout.decode()) <= 20


def test_cp1252_ascii(sl, suite, temp_home):
    result = run_renderer(
        suite,
        temp_home,
        sl.__file__,
        json.dumps({"session_id": SESSION}),
        PYTHONIOENCODING="cp1252",
        COLUMNS="120",
    )
    assert result.returncode == 0 and result.stderr == b""
    output = result.stdout.decode("ascii")
    assert "[124 recalled]" in output and "#" in output


@pytest.mark.parametrize("counts", ["true", "full", "false"])
@pytest.mark.parametrize("url", ["http://localhost:8011", "https://memory.example.test"])
def test_classic_byte_identical_to_1_6_21(sl, suite, temp_home, counts, url):
    # Frozen verbatim from cognee-integrations 962c486; do not regenerate on changes.
    baseline = Path(__file__).resolve().parents[2] / "fixtures" / "statusline_1_6_21.py"
    env = {
        "COGNEE_STATUSLINE_STYLE": "classic",
        "COGNEE_STATUSLINE_COUNTS": counts,
        "COGNEE_BASE_URL": url,
    }
    payload = json.dumps({"session_id": SESSION})
    old = run_renderer(suite, temp_home, baseline, payload, **env)
    new = run_renderer(suite, temp_home, sl.__file__, payload, **env)
    assert old.returncode == new.returncode == 0
    assert old.stderr == new.stderr == b""
    assert old.stdout == new.stdout and old.stdout


@pytest.mark.parametrize("corrupt", ["{partial", "[]", '{"entries_injected":"bad"}'])
def test_corrupt_stats_keep_bar(sl, corrupt):
    (sl._SESSIONS_DIR / SESSION / "stats.json").write_text(corrupt)
    assert "[0 recalled]" in sl._tetris_line(SESSION)


def test_timestamp_iso_and_no_history_writes(sl):
    before = {str(p): p.read_bytes() for p in sl._RECALL_DIR.parent.rglob("*") if p.is_file()}
    assert sl._timestamp("2027-01-15T08:00:00Z") == NOW
    sl._tetris_line(SESSION)
    after = {str(p): p.read_bytes() for p in sl._RECALL_DIR.parent.rglob("*") if p.is_file()}
    assert before == after


def test_failure_at_60_keeps_identity_and_count(sl, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")
    monkeypatch.setattr(sl, "_status_prefix", lambda _: sl._fail_glyph("server_error"))
    line = sl._tetris_line(SESSION)
    assert sl._width(line) <= 60
    assert all(value in line for value in ("server_error", "manual", "cloud", "124 recalled"))


def test_home_without_markers(sl, suite, tmp_path):
    fresh = tmp_path / "empty-home"
    write_json(
        fresh / ".claude" / "settings.json", {"enabledPlugins": {"cognee-memory@cognee": True}}
    )
    result = run_renderer(suite, fresh, sl.__file__, "{}")
    assert result.returncode == 0 and result.stderr == b""
    assert "[0 recalled]" in result.stdout.decode()
    assert len(result.stdout.splitlines()) == 1
