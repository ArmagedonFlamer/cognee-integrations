"""The smaller openclaw-parity changes: config keys, chunk size, 402, the env file
and the spawned server's working directory."""

import io
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _char_helpers import fake_backend, make_provider  # noqa: E402
from cognee_integration_hermes import config as config_mod  # noqa: E402
from cognee_integration_hermes import env_file  # noqa: E402
from cognee_integration_hermes import exit_watcher as ew  # noqa: E402
from cognee_integration_hermes import server_bootstrap as sb  # noqa: E402
from cognee_integration_hermes.http_backend import CogneeHttpError  # noqa: E402
from test_http_backend import FakeOpener, _backend  # noqa: E402


class TestConfigKeys(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in (
                "COGNEE_RECALL_PASSAGE_CHARS",
                "COGNEE_RECALL_CONTEXT_CHARS",
                "COGNEE_CHUNK_SIZE",
            ):
                os.environ.pop(name, None)
            config = config_mod.load_config(tempfile.mkdtemp())
        self.assertEqual(config["recall_passage_chars"], 2000)
        self.assertEqual(config["recall_context_chars"], 12000)
        self.assertEqual(config["chunk_size"], 0)

    def test_environment_and_junk(self):
        env = {
            "COGNEE_RECALL_PASSAGE_CHARS": "0",
            "COGNEE_RECALL_CONTEXT_CHARS": "-5",
            "COGNEE_CHUNK_SIZE": "1024",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = config_mod.load_config(tempfile.mkdtemp())
        self.assertEqual(config["recall_passage_chars"], 0)  # 0 disables
        self.assertEqual(config["recall_context_chars"], 12000)  # junk -> default
        self.assertEqual(config["chunk_size"], 1024)


class TestChunkSize(unittest.TestCase):
    def test_unset_sends_nothing(self):
        with fake_backend() as fake:
            provider = make_provider()
            provider.handle_tool_call("cognee_remember", {"content": "x"})
            self.assertNotIn("chunk_size", fake.only_call("remember_permanent"))

    def test_configured_size_reaches_the_backend(self):
        with fake_backend() as fake:
            provider = make_provider(config={"chunk_size": 1024})
            provider.handle_tool_call("cognee_remember", {"content": "x"})
            self.assertEqual(fake.only_call("remember_permanent")["chunk_size"], 1024)

    def test_http_sends_the_form_field_only_when_set(self):
        opener = FakeOpener({"/api/v1/remember": {"status": "completed"}})
        _backend(opener).remember_permanent(
            text="t", dataset="d", session_ids=[], timeout=5, chunk_size=1024
        )
        self.assertEqual(opener.multipart_fields("/api/v1/remember")["chunk_size"], "1024")
        opener = FakeOpener({"/api/v1/remember": {"status": "completed"}})
        _backend(opener).remember_permanent(text="t", dataset="d", session_ids=[], timeout=5)
        self.assertNotIn("chunk_size", opener.multipart_fields("/api/v1/remember"))


class TestLlmBudgetExhausted(unittest.TestCase):
    def test_inline_improve_402_is_not_a_breaker_failure(self):
        with fake_backend() as fake:
            fake.errors["improve"] = CogneeHttpError(402, "budget")
            provider = make_provider()
            with self.assertLogs("cognee_integration_hermes.provider", "WARNING") as logs:
                provider.on_session_end([])
        self.assertEqual(provider._consecutive_failures, 0)
        self.assertIn("LLM budget is exhausted (402)", "\n".join(logs.output))

    def test_the_exit_watcher_says_why(self):
        state = {"url": "http://127.0.0.1:1", "session_id": "hermes_s1", "improve": True}
        error = urllib.error.HTTPError("u", 402, "Payment Required", {}, io.BytesIO(b""))

        def post(url, path, payload, **kwargs):
            if path == "/api/v1/improve":
                raise error
            return 200

        logged = []
        with (
            mock.patch.object(ew, "_post", side_effect=post),
            mock.patch.object(ew, "_log", side_effect=logged.append),
        ):
            ew.fire(state)
        self.assertTrue(any("budget is exhausted (402)" in line for line in logged), logged)


class TestEnvFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / ".env"

    def test_parse(self):
        self.path.write_text(
            "# comment\nexport LLM_API_KEY='sk-1'\nLLM_MODEL=\"gpt\"\nBAD LINE\n=x\nA-B=1\n",
            encoding="utf-8",
        )
        self.assertEqual(
            env_file.parse_env_file(self.path), {"LLM_API_KEY": "sk-1", "LLM_MODEL": "gpt"}
        )

    def test_exports_win_and_dangerous_names_are_skipped(self):
        self.path.write_text("LLM_API_KEY=file\nLLM_MODEL=m\nPATH=/evil\nLD_PRELOAD=x\n")
        env = {"LLM_API_KEY": "exported"}
        added = env_file.apply_env_file(env, self.path)
        self.assertEqual(env, {"LLM_API_KEY": "exported", "LLM_MODEL": "m"})
        self.assertEqual(added, ["LLM_MODEL"])

    def test_cognee_env_file_overrides_the_location(self):
        with mock.patch.dict(os.environ, {"COGNEE_ENV_FILE": str(self.path)}):
            self.assertEqual(env_file.env_file_path(), self.path)

    def test_a_missing_file_adds_nothing(self):
        env = {}
        self.assertEqual(env_file.apply_env_file(env, self.path), [])
        self.assertEqual(env, {})


class TestServerSpawn(unittest.TestCase):
    def _spawn(self, environ, env_text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env_path = Path(tmp.name) / ".env"
        env_path.write_text(env_text, encoding="utf-8")
        workdir = Path(tmp.name) / "state"
        environ = {**environ, "COGNEE_ENV_FILE": str(env_path)}
        with (
            mock.patch.dict(os.environ, environ, clear=False),
            mock.patch.object(sb, "server_workdir", return_value=str(workdir)),
            mock.patch.object(sb.subprocess, "Popen") as popen,
        ):
            sb._spawn(8011, "", "", os.devnull)
        return popen.call_args.kwargs, workdir

    def test_the_server_runs_in_the_plugin_state_dir(self):
        kwargs, workdir = self._spawn({}, "")
        self.assertEqual(kwargs["cwd"], str(workdir))
        self.assertNotEqual(kwargs["cwd"], os.getcwd())

    def test_the_env_file_reaches_the_server_but_exports_win(self):
        os.environ.pop("CACHING", None)
        kwargs, _ = self._spawn(
            {"LLM_MODEL": "exported"},
            "LLM_MODEL=file\nLLM_API_KEY=from-file\nCACHING=false\nPATH=/evil\n",
        )
        env = kwargs["env"]
        self.assertEqual(env["LLM_MODEL"], "exported")
        self.assertEqual(env["LLM_API_KEY"], "from-file")
        # The file outranks the plugin's own defaults, like an export would.
        self.assertEqual(env["CACHING"], "false")
        self.assertNotEqual(env["PATH"], "/evil")

    def test_the_default_workdir_is_under_the_shared_state_dir(self):
        with mock.patch.object(sb, "SHARED_PLUGIN_STATE_DIR", Path(tempfile.mkdtemp())) as root:
            self.assertEqual(sb.server_workdir(), str(root / "hermes"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
