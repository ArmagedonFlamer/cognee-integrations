"""Client-side automatic capture controls. Explicit remember is independent."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
from pathlib import PurePosixPath

_FALSE = {"0", "false", "no", "off"}
_PATH_KEYS = {"file_path", "filepath", "path", "paths", "notebook_path", "filename"}
_DENY_PATHS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    ".npmrc",
    ".netrc",
    "*/.aws/credentials",
    "*/.ssh/*",
    "*.p12",
    "*.pfx",
    "secrets.*",
    "credentials.*",
)
# The last word of a secret-bearing name. ``secret`` may carry a ``_key`` /
# ``_access_key`` suffix (``SECRET_KEY``, ``aws_secret_access_key``), and the
# AWS / npm names a plain suffix test misses are spelled out: ``access_key_id``,
# camelCase ``SessionToken`` / ``SecretAccessKey`` / ``_authToken``.
_SECRET_WORD = (
    r"secret(?:[_-]?(?:access[_-]?)?key)?|token|password|passwd|api[_-]?key"
    r"|access[_-]?key(?:[_-]?id)?|session[_-]?token|auth[_-]?token"
)
_SECRET_KEY = re.compile(rf"(?i)^(?:authorization|x-api-key|_auth|(?:.*[_-])?(?:{_SECRET_WORD}))$")
_RULES = (
    (
        "private-key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    ),
    ("connection", r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://[^\s\"'<>]+"),
    ("authorization", r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"),
    (
        "credential",
        rf"(?i)[\"']?\b(?:(?:[\w-]*[_-])?(?:{_SECRET_WORD}|x-api-key)|_auth)"
        r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    ),
    (
        "vendor-key",
        r"\b(?:sk-(?:proj-|ant-)?|gh[pousr]_|github_pat_|xox[baprs]-|whsec_)[A-Za-z0-9_-]{16,}"
        r"|\bnpm_[A-Za-z0-9]{36}\b|\bpypi-[A-Za-z0-9_-]{50,}",
    ),
    (
        "aws-key-id",
        r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|APKA|ABIA|ACCA)[A-Z0-9]{16}\b",
    ),
    ("bcrypt", r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}"),
)
_COMPILED = [(kind, re.compile(pattern)) for kind, pattern in _RULES]
# ``Tool(prefix:*)`` borrows Claude Code's permission spelling, so the pattern a
# user already writes to allow a command also selects whether it is remembered.
_COMMAND_PATTERN = re.compile(r"^(?P<tool>[^()\s]+)\((?P<prefix>[^()]*):\*\)$")


class CapturePatternError(ValueError):
    """A ``COGNEE_CAPTURE_TOOLS`` / ``COGNEE_CAPTURE_DENY_TOOLS`` entry is not understood."""


def capture_enabled() -> bool:
    return os.environ.get("COGNEE_CAPTURE", "true").strip().lower() not in _FALSE


def _list_env(name: str, *, separator: str = ",") -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        values = json.loads(raw)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"{name} must be an array of strings")
        return values
    return [part.strip() for part in raw.split(separator) if part.strip()]


def _glob_match(name: str, pattern: str) -> bool:
    """``fnmatch.fnmatchcase``, with an unusable glob reported as a pattern error.

    Python 3.9's fnmatch hands some globs (a reversed range such as ``[C-A]``) to
    ``re`` unchanged, which rejects them; 3.10+ turn them into "matches nothing".
    Without this the 3.9 ``re.error`` escaped the hook as a crash instead of the
    usual "pattern not understood" report.
    """
    try:
        return fnmatch.fnmatchcase(name, pattern)
    except re.error as exc:
        raise CapturePatternError(
            f"capture pattern {pattern!r} is not a valid glob: {exc}"
        ) from exc


def _sensitive_path(value) -> bool:
    if isinstance(value, (tuple, list)):
        return any(_sensitive_path(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/").lower()
    name = PurePosixPath(normalized).name
    return any(
        _glob_match(name, pattern.lower())
        or _glob_match("/" + normalized.lstrip("/"), pattern.lower())
        for pattern in (*_DENY_PATHS, *_list_env("COGNEE_CAPTURE_DENY_PATHS"))
    )


# Shell control characters that end a word for the secret-file check:
# ``cat .env|grep``, ``cat<.env``, ``$(cat .env)``, ``a;cat .env``.
_SHELL_SPLIT = re.compile(r"[;|&()<>`]")
_HOME_VAR = re.compile(r"^\$(?:HOME|\{HOME\})(?=/|\Z)")


def _shell_words(command: str) -> list[str]:
    """Split a shell command into words, POSIX-quoted; whitespace if it will not parse."""
    try:
        words = shlex.split(command)
    except ValueError:  # unbalanced quote, trailing backslash
        words = command.split()
    pieces = []
    for word in words:
        for piece in _SHELL_SPLIT.split(word):
            if piece:
                pieces.append(_HOME_VAR.sub("~", piece))
    return pieces


def _command_reads_secret(params) -> bool:
    """Whether a shell command names a deny-listed path as one of its words.

    A word-level check, not a shell interpreter: ``cat ~/.aws/credentials``,
    ``less "$HOME/.ssh/id_rsa"`` and ``source .env.local`` are caught, but a
    path built at run time (``cat $AWS_DIR/credentials``, ``cat ~/.aws/cred*``)
    is not, and neither is a file read by a program without naming it.
    """
    command = params.get("command") if isinstance(params, dict) else None
    if not isinstance(command, str):
        return False
    return any(_sensitive_path(word) for word in _shell_words(command))


def _has_sensitive_path(value) -> bool:
    if isinstance(value, dict):
        return any(
            (str(key).lower() in _PATH_KEYS and _sensitive_path(item)) or _has_sensitive_path(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_sensitive_path(item) for item in value)
    return False


def _parse_pattern(pattern: str) -> tuple[str, str | None]:
    """Split ``Tool(prefix:*)`` into (tool glob, command prefix); plain names get None."""
    if "(" not in pattern and ")" not in pattern:
        return pattern, None
    match = _COMMAND_PATTERN.match(pattern)
    prefix = match.group("prefix").strip() if match else ""
    if not prefix:
        # Matching nothing is how ``Bash(rg:*)`` used to switch capture off for
        # every tool without a word of warning; a typo has to be loud.
        raise CapturePatternError(
            f"capture tool pattern {pattern!r} is not understood; "
            "use a tool name, an fnmatch glob, or Tool(command-prefix:*)"
        )
    return match.group("tool"), prefix


def _tool_patterns(env_name: str, default: list[str]) -> list[tuple[str, str | None]]:
    """Parse every entry up front so a bad one fails whether or not it is reached."""
    try:
        entries = _list_env(env_name, separator="|") or default
    except ValueError as exc:  # a JSON array that does not parse, or holds non-strings
        raise CapturePatternError(f"{env_name} is not a valid JSON array: {exc}") from exc
    try:
        return [_parse_pattern(pattern) for pattern in entries]
    except CapturePatternError as exc:
        raise CapturePatternError(f"{env_name}: {exc}") from exc


def _matches(tool: str, prefix: str | None, name: str, params) -> bool:
    if not _glob_match(name, tool):
        return False
    if prefix is None:
        return True
    command = params.get("command") if isinstance(params, dict) else None
    if not isinstance(command, str):
        return False
    # The prefix has to end on a word boundary: ``Bash(git:*)`` keeps ``git status``
    # and drops ``gitx weird``. Compound commands (``cd x && git status``) are
    # matched on their first word only; this is a prefix test, not a shell parser.
    command = command.lstrip()
    if not command.startswith(prefix):
        return False
    return len(command) == len(prefix) or command[len(prefix)].isspace()


def allow_tool(name: str, params) -> bool:
    """Whether a tool call may be captured.

    Raises :class:`CapturePatternError` when an allow or deny entry cannot be
    parsed, so a misspelled pattern never silently disables capture.
    """
    if not capture_enabled():
        return False
    allowed = _tool_patterns("COGNEE_CAPTURE_TOOLS", ["*"])
    denied = _tool_patterns("COGNEE_CAPTURE_DENY_TOOLS", [])
    if not any(_matches(tool, prefix, name, params) for tool, prefix in allowed):
        return False
    if any(_matches(tool, prefix, name, params) for tool, prefix in denied):
        return False
    return not (_has_sensitive_path(params) or _command_reads_secret(params))


def capture_pattern_error() -> str:
    """The message for a malformed capture tool pattern in the environment, or ''.

    Cheap enough for a per-prompt hook; lets a hook whose output the user sees
    report a problem that only surfaces inside the silent PostToolUse path.
    """
    try:
        _tool_patterns("COGNEE_CAPTURE_TOOLS", ["*"])
        _tool_patterns("COGNEE_CAPTURE_DENY_TOOLS", [])
    except CapturePatternError as exc:
        return str(exc)
    return ""


def redact(value):
    """Redact before truncation so a clipped private key cannot escape matching."""
    if os.environ.get("COGNEE_CAPTURE_REDACT", "true").strip().lower() in _FALSE:
        return value
    if isinstance(value, str):
        for kind, pattern in _COMPILED:
            value = pattern.sub(f"[redacted:{kind}]", value)
        # User-supplied regexes are JSON strings; commas are valid regex syntax.
        raw = os.environ.get("COGNEE_CAPTURE_REDACT_PATTERNS", "").strip()
        if raw:
            patterns = _list_env("COGNEE_CAPTURE_REDACT_PATTERNS", separator="\n")
            for pattern in patterns:
                value = re.sub(pattern, "[redacted:custom]", value)
        return value
    if isinstance(value, dict):
        return {
            key: "[redacted:credential]" if _SECRET_KEY.match(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
