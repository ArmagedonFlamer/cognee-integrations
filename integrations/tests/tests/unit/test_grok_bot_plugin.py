"""Static checks for the Grok Bot plugin, which ships no code of its own.

Grok Bot reads Cursor-format plugins: `.cursor-plugin/plugin.json`, `mcp.json` and
`skills/<name>/SKILL.md`, listed in the repository-root
`.cursor-plugin/marketplace.json`. A typo in any of them fails silently in the
host (the plugin, the server or a skill just does not appear), so check them here.
"""

import json
import re
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[3]
REPO = INTEGRATIONS.parent
PLUGIN = INTEGRATIONS / "grok-bot"
KEBAB = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
HOSTED_MCP_URL = "https://mcp.aws.cognee.ai/mcp"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _frontmatter(path: Path) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.DOTALL)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_manifest_names_the_plugin_and_matches_the_changelog():
    manifest = _json(PLUGIN / ".cursor-plugin/plugin.json")
    assert KEBAB.match(manifest["name"])
    latest = re.search(r"^## \[([^\]]+)\]", (PLUGIN / "CHANGELOG.md").read_text(), re.M)
    assert latest and latest.group(1) == manifest["version"]


def test_marketplace_lists_the_plugin_at_its_directory():
    marketplace = _json(REPO / ".cursor-plugin/marketplace.json")
    assert KEBAB.match(marketplace["name"])
    assert marketplace["owner"]["name"]
    manifest = _json(PLUGIN / ".cursor-plugin/plugin.json")
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    entry = entries[manifest["name"]]
    assert (REPO / entry["source"]).resolve() == PLUGIN.resolve()


def test_mcp_config_points_at_the_hosted_server_without_credentials():
    servers = _json(PLUGIN / "mcp.json")["mcpServers"]
    assert servers == {"cognee": {"url": HOSTED_MCP_URL}}


def test_every_skill_has_a_matching_name_and_a_description():
    skills = sorted((PLUGIN / "skills").iterdir())
    assert [skill.name for skill in skills] == [
        "cognee-forget",
        "cognee-recall",
        "cognee-remember",
    ]
    for skill in skills:
        fields = _frontmatter(skill / "SKILL.md")
        assert fields["name"] == skill.name
        assert KEBAB.match(fields["name"])
        assert fields["description"]


def test_skills_use_the_hosted_tools_and_no_local_scripts():
    # The hosted server serves cognee-mcp's tools with cognify_status renamed to
    # check_ingestion_status, and Grok Bot has no plugin scripts to run.
    for skill in (PLUGIN / "skills").iterdir():
        text = (skill / "SKILL.md").read_text()
        tool = skill.name.removeprefix("cognee-")
        assert f"`{tool}`" in text
        assert "scripts/" not in text
        assert "cognify_status" not in text
