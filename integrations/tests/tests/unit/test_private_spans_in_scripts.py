"""The skill scripts sanitize what they send, on the CLI fallback too (SDK-1100).

``cognee-remember.sh`` and ``cognee-search.sh`` fall back to ``cognee-cli`` when
the server cannot be reached. A stub ``cognee-cli`` on PATH records the argv it
was handed: a ``<private>`` span or a recognised secret must not be in it.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

SECRET = "SENTINEL-6d1c"


@pytest.fixture
def cli_argv(tmp_path):
    """PATH with a recording ``cognee-cli`` (and a ``uv`` that runs its command)."""
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("needs bash and POSIX shims")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "cli-argv.txt"
    (bin_dir / "cognee-cli").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{log}"\n')
    (bin_dir / "uv").write_text('#!/bin/sh\n[ "$1" = run ] && shift\nexec "$@"\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return bin_dir, log


@pytest.mark.parametrize(
    "script,argv",
    [
        ("cognee-remember.sh", [f"keep <private>{SECRET}</private> password=hunter2"]),
        ("cognee-search.sh", [f"find <private>{SECRET}</private> token=hunter2", "5", "--graph"]),
    ],
)
def test_cli_fallback_gets_the_sanitized_text(
    suite, temp_home, closed_port_url, cli_argv, script, argv
):
    bin_dir, log = cli_argv
    env = {
        **os.environ,
        "HOME": str(temp_home),
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        "COGNEE_BASE_URL": closed_port_url,
        "COGNEE_API_KEY": "test-key",
    }
    result = subprocess.run(
        ["bash", str(suite.scripts_dir / script), *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert log.exists(), f"cognee-cli never ran: {result.stderr}"
    sent = log.read_text()
    assert "[private]" in sent
    assert SECRET not in sent and "hunter2" not in sent
