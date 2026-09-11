"""The self-directed loop. Runs INSIDE the sandbox container.

Mounted read-only at /opt/agent, deliberately outside the writable
workspace, so the agent cannot edit its own loop, its own budget, or its
own tool allowlist. The only writable paths are /work and a noexec tmpfs
at /tmp.

The loop is: read the charter, look at what past iterations did, ask the
model for exactly one action, execute it, record the real result, commit.
Repeat until the charter is satisfied, the iteration cap is hit, or the
broker stops answering.

Two things this file is careful about, both from briefings/REGS-COMMON.md:

  - The harness records outcomes, the model does not. Every action's real
    exit code and output go in the journal. A model claiming its tests pass
    is a hypothesis; the recorded exit code is the measurement.
  - The iteration cap here is the FIRST enforcer, not the only one. The
    broker outside the container enforces its own caps and cannot be
    reached or edited from in here.

Self-tests (no model, no container, no network required):

    python3 agent/agent.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

SOCKET_PATH = Path(os.environ.get("AGENT_MODEL_SOCKET", "/run/model.sock"))
WORK = Path(os.environ.get("AGENT_WORK", "/work"))
STATE_DIR_NAME = ".agent"

# Binaries the agent may invoke. Inside the sandbox arbitrary execution is
# acceptable by construction, so this list is not the security boundary --
# the container is. It exists to keep the journal legible and to stop the
# model wasting iterations on tools the image does not have. Note what is
# absent and not installed anyway: curl, wget, ssh, nc.
ALLOWED_BINARIES = frozenset(
    {
        "python3", "python", "pytest", "git", "ls", "cat", "grep", "rg",
        "wc", "find", "head", "tail", "diff", "sort", "uniq", "tree",
        "echo", "pwd", "mkdir", "true", "false", "node",
    }
)

MAX_FILE_READ_BYTES = 60_000
MAX_OUTPUT_CHARS = 8_000
MAX_TREE_ENTRIES = 300
JOURNAL_TAIL = 12

ACTION_RE = re.compile(
    r"<action\s+name=[\"'](?P<name>[a-z_]+)[\"'](?P<attrs>[^>]*)>"
    r"(?P<body>.*?)</action>",
    re.DOTALL,
)
ATTR_RE = re.compile(r"(?P<key>[a-z_]+)=[\"'](?P<value>[^\"']*)[\"']")

SYSTEM_PROMPT = """\
You are a self-directed software engineer working alone inside a sealed
sandbox. You have a writable workspace at /work and no network access of
any kind. Nobody is watching each step; a human will review your git
history at the end.

Work in small, verified increments. Write a test before or alongside the
code it covers, then actually run it. Never claim something passes without
having run it in a previous action and seen the exit code.

Reply with EXACTLY ONE action block and nothing else. No preamble, no
explanation outside the block, no second action.

Available actions:

<action name="list_dir" path=".">
</action>

<action name="read_file" path="src/thing.py">
</action>

<action name="write_file" path="src/thing.py">
full new contents of the file, replacing it entirely
</action>

<action name="run" cmd="python3 -m unittest discover -s tests">
</action>

<action name="note">
a short observation for your own journal, when you need to think before acting
</action>

<action name="done">
why you believe the charter is satisfied, and what you verified
</action>

Rules that matter:
- write_file replaces the whole file. Read it first if you are editing.
- run only these binaries: {binaries}
- There is no network. Do not try to install anything.
- If an action fails, read the error and change approach. Repeating a
  failing action wastes an iteration you do not get back.
"""


class ActionError(Exception):
    """The model's reply could not be turned into one executable action."""


@dataclass
class Action:
    name: str
    body: str
    attrs: dict


def parse_action(reply: str) -> Action:
    """Pull exactly one action out of a model reply.

    Local models pad their output, so leading prose is tolerated. A reply
    with no action at all, or an unknown action name, is an error the loop
    reports back to the model rather than guessing at.
    """
    matches = list(ACTION_RE.finditer(reply))
    if not matches:
        raise ActionError(
            "no <action ...> block found. Reply with exactly one action block."
        )
    match = matches[0]
    name = match.group("name")
    known = {"list_dir", "read_file", "write_file", "run", "note", "done"}
    if name not in known:
        raise ActionError(f"unknown action {name!r}. Known: {sorted(known)}")
    attrs = {m.group("key"): m.group("value") for m in ATTR_RE.finditer(match.group("attrs"))}
    body = match.group("body")
    # Strip one leading and one trailing newline, which the tag format adds,
    # without touching the file's own indentation.
    if body.startswith("\n"):
        body = body[1:]
    if body.endswith("\n"):
        body = body[:-1]
    return Action(name=name, body=body, attrs=attrs)


def safe_path(raw: str, root: Path) -> Path:
    """Resolve a model-supplied path, refusing anything outside the workspace.

    The container already prevents the agent reaching the host, so this is
    defence in depth rather than the boundary. It earns its place by keeping
    the agent from scribbling on its own read-only loop at /opt/agent and
    getting a confusing permission error instead of a clear refusal.
    """
    if not raw or not raw.strip():
        raise ActionError("path attribute is required and cannot be empty")
    candidate = Path(raw.strip())
    if candidate.is_absolute():
        raise ActionError(f"path must be relative to the workspace, got {raw!r}")
    root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ActionError(f"path {raw!r} escapes the workspace")
    state = (root / STATE_DIR_NAME).resolve()
    if resolved == state or state in resolved.parents:
        raise ActionError(
            f"path {raw!r} is inside {STATE_DIR_NAME}/, which is the harness journal, "
            "not yours to write"
        )
    return resolved


def check_command(cmd: str) -> list[str]:
    """Split a command and check its binary against the allowlist."""
    if not cmd or not cmd.strip():
        raise ActionError("cmd attribute is required for the run action")
    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        raise ActionError(f"cmd is not parseable: {exc}") from exc
    if not parts:
        raise ActionError("cmd is empty")
    for token in parts:
        if token in {"|", "||", "&&", ";", ">", ">>", "<", "&"}:
            raise ActionError(
                f"shell operator {token!r} is not supported; run one command per action"
            )
    binary = Path(parts[0]).name
    if binary not in ALLOWED_BINARIES:
        raise ActionError(
            f"binary {binary!r} is not allowed. Allowed: {sorted(ALLOWED_BINARIES)}"
        )
    return parts


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n...[{omitted} chars omitted]...\n{text[-half:]}"


class Workspace:
    """Every filesystem and subprocess effect the agent can have."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = self.root / STATE_DIR_NAME
        self.state.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.state / "journal.jsonl"

    # -- actions -------------------------------------------------------

    def list_dir(self, action: Action) -> str:
        target = safe_path(action.attrs.get("path", "."), self.root)
        if not target.is_dir():
            raise ActionError(f"not a directory: {self.rel(target)}")
        entries = []
        for child in sorted(target.iterdir()):
            if child.name in {".git", STATE_DIR_NAME, "__pycache__"}:
                continue
            if child.is_dir():
                entries.append(f"{self.rel(child)}/")
            else:
                entries.append(f"{self.rel(child)} ({child.stat().st_size} bytes)")
            if len(entries) >= MAX_TREE_ENTRIES:
                entries.append("...[truncated]")
                break
        return "\n".join(entries) if entries else "(empty)"

    def read_file(self, action: Action) -> str:
        target = safe_path(action.attrs.get("path", ""), self.root)
        if not target.is_file():
            raise ActionError(f"no such file: {self.rel(target)}")
        data = target.read_bytes()[:MAX_FILE_READ_BYTES]
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ActionError(f"{self.rel(target)} is not utf-8 text") from None

    def write_file(self, action: Action) -> str:
        target = safe_path(action.attrs.get("path", ""), self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.is_file()
        target.write_text(action.body, encoding="utf-8")
        verb = "overwrote" if existed else "created"
        lines = action.body.count("\n") + 1 if action.body else 0
        return f"{verb} {self.rel(target)} ({lines} lines, {len(action.body)} bytes)"

    def run(self, action: Action) -> str:
        parts = check_command(action.attrs.get("cmd", ""))
        started = time.monotonic()
        try:
            completed = subprocess.run(
                parts,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=float(os.environ.get("AGENT_CMD_TIMEOUT", "300")),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except FileNotFoundError:
            raise ActionError(f"{parts[0]!r} is not installed in this image") from None
        except subprocess.TimeoutExpired:
            raise ActionError(f"command timed out: {action.attrs.get('cmd')}") from None
        elapsed = time.monotonic() - started
        # The exit code is the measurement. It goes in the journal whether
        # the model likes it or not.
        return (
            f"exit code: {completed.returncode} (in {elapsed:.1f}s)\n"
            f"--- stdout ---\n{truncate(completed.stdout)}\n"
            f"--- stderr ---\n{truncate(completed.stderr)}"
        )

    def rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root)) or "."
        except ValueError:
            return str(path)

    # -- journal and git ----------------------------------------------

    def append_journal(self, entry: dict) -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def read_journal(self, tail: int = JOURNAL_TAIL) -> list[dict]:
        if not self.journal_path.is_file():
            return []
        entries = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries[-tail:]

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "git",
                # Scoped, never global. The workspace uid normally matches the
                # container uid, but it does not when the harness runs as root
                # and chowns the workspace to the sandbox user. Without this,
                # git refuses the repo as "dubious ownership" and every commit
                # silently stops happening.
                "-c", f"safe.directory={self.root}",
                "-c", "user.name=agent-sandbox",
                "-c", "user.email=agent-sandbox@localhost",
                "-c", "commit.gpgsign=false",
                *args,
            ],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )

    def commit(self, message: str) -> tuple[str | None, str | None]:
        """Commit the working tree. Returns (sha, error), exactly one set.

        Three outcomes, kept distinct on purpose: a sha, a clean tree
        (None, None), or an error. A git FAILURE must never be reported as
        "nothing to commit" -- that conflation is how a broken git silently
        produced a run with zero commits and no error recorded anywhere:
        `git status` failed, its stdout was empty, and empty stdout read as
        a clean tree.
        """
        add = self.git("add", "-A")
        if add.returncode != 0:
            return None, f"git add failed: {truncate(add.stderr, 300)}"
        status = self.git("status", "--porcelain")
        if status.returncode != 0:
            return None, f"git status failed: {truncate(status.stderr, 300)}"
        if not status.stdout.strip():
            return None, None  # genuinely nothing to commit
        result = self.git("commit", "-m", message)
        if result.returncode != 0:
            return None, f"git commit failed: {truncate(result.stderr, 300)}"
        head = self.git("rev-parse", "--short", "HEAD")
        if head.returncode != 0:
            return None, "committed, but rev-parse failed"
        return head.stdout.strip(), None


def ask_model(system: str, prompt: str, socket_path: Path, timeout: float) -> dict:
    """One request to the broker over the unix socket. No other egress exists."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        payload = json.dumps({"system": system, "prompt": prompt}) + "\n"
        client.sendall(payload.encode("utf-8"))
        chunks = []
        while not chunks or not chunks[-1].endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    if not chunks:
        raise RuntimeError("broker closed the connection without replying")
    return json.loads(b"".join(chunks).decode("utf-8"))


def build_prompt(workspace: Workspace, charter: str, iteration: int, cap: int,
                 last: str | None) -> str:
    journal = workspace.read_journal()
    history_lines = []
    for entry in journal:
        if entry.get("event") != "action":
            continue
        summary = entry.get("summary", "")
        history_lines.append(
            f"  #{entry.get('iteration')} {entry.get('action')} "
            f"{entry.get('target', '')} -> {truncate(summary, 240)}"
        )
    tree = workspace.list_dir(Action("list_dir", "", {"path": "."}))
    parts = [
        f"# Charter\n\n{charter.strip()}",
        f"# Where you are\n\nIteration {iteration} of {cap}. Workspace tree:\n\n{tree}",
    ]
    if history_lines:
        parts.append("# What you already did\n\n" + "\n".join(history_lines))
    if last:
        parts.append(f"# Result of your last action\n\n{truncate(last)}")
    parts.append("Reply with exactly one action block.")
    return "\n\n".join(parts)


def run_loop(workspace: Workspace, charter: str, max_iterations: int,
             socket_path: Path, model_timeout: float) -> int:
    system = SYSTEM_PROMPT.format(binaries=", ".join(sorted(ALLOWED_BINARIES)))
    handlers = {
        "list_dir": workspace.list_dir,
        "read_file": workspace.read_file,
        "write_file": workspace.write_file,
        "run": workspace.run,
    }
    last_result: str | None = None
    workspace.append_journal({"event": "run_start", "max_iterations": max_iterations})

    for iteration in range(1, max_iterations + 1):
        prompt = build_prompt(workspace, charter, iteration, max_iterations, last_result)
        try:
            response = ask_model(system, prompt, socket_path, model_timeout)
        except Exception as exc:
            workspace.append_journal({"event": "broker_unreachable", "error": repr(exc)})
            print(f"[{iteration}] broker unreachable: {exc}", flush=True)
            return 3

        if not response.get("ok"):
            # The broker refused. Its caps are outside this container and are
            # not negotiable from in here, so the run ends.
            workspace.append_journal(
                {
                    "event": "broker_refused",
                    "kind": response.get("kind"),
                    "error": response.get("error"),
                }
            )
            print(f"[{iteration}] broker refused: {response.get('error')}", flush=True)
            return 0 if response.get("kind") == "budget" else 4

        reply = response.get("text", "")
        try:
            action = parse_action(reply)
        except ActionError as exc:
            last_result = f"ERROR: {exc}"
            workspace.append_journal(
                {
                    "event": "action",
                    "iteration": iteration,
                    "action": "unparseable",
                    "ok": False,
                    "summary": str(exc),
                    "reply_head": truncate(reply, 600),
                }
            )
            print(f"[{iteration}] unparseable reply: {exc}", flush=True)
            continue

        target = action.attrs.get("path") or action.attrs.get("cmd") or ""

        if action.name == "done":
            workspace.append_journal(
                {
                    "event": "done",
                    "iteration": iteration,
                    "claim": action.body,
                }
            )
            print(f"[{iteration}] agent declared done", flush=True)
            print(action.body, flush=True)
            # Its claim is a claim. The harness verdict is computed on the
            # host from recorded exit codes, not from this message.
            return 0

        if action.name == "note":
            last_result = "noted"
            workspace.append_journal(
                {
                    "event": "action",
                    "iteration": iteration,
                    "action": "note",
                    "ok": True,
                    "summary": truncate(action.body, 600),
                }
            )
            print(f"[{iteration}] note: {truncate(action.body, 160)}", flush=True)
            continue

        try:
            result = handlers[action.name](action)
            ok = True
        except ActionError as exc:
            result = f"ERROR: {exc}"
            ok = False
        except OSError as exc:
            result = f"ERROR: {exc}"
            ok = False

        last_result = result
        sha = commit_error = None
        if ok and action.name == "write_file":
            sha, commit_error = workspace.commit(
                f"agent iteration {iteration}: {target}"
            )
            if commit_error:
                # A failing git is a harness defect, not the agent's problem,
                # and it must be visible rather than a quietly empty history.
                print(f"[{iteration}] COMMIT FAILED: {commit_error}", flush=True)

        workspace.append_journal(
            {
                "event": "action",
                "iteration": iteration,
                "action": action.name,
                "target": target,
                "ok": ok,
                "commit": sha,
                "commit_error": commit_error,
                "summary": truncate(result, 2000),
            }
        )
        print(f"[{iteration}] {action.name} {target} -> {'ok' if ok else 'failed'}", flush=True)

    workspace.append_journal({"event": "iteration_cap_reached", "iterations": max_iterations})
    print(f"iteration cap reached at {max_iterations}", flush=True)
    return 0


# -- self-tests ---------------------------------------------------------


class ParseTests(unittest.TestCase):
    def test_parses_a_clean_action(self):
        action = parse_action('<action name="read_file" path="a/b.py">\n</action>')
        self.assertEqual(action.name, "read_file")
        self.assertEqual(action.attrs["path"], "a/b.py")

    def test_tolerates_model_padding(self):
        reply = 'Sure! Here is my next step:\n\n<action name="list_dir" path=".">\n</action>\n\nHope that helps.'
        self.assertEqual(parse_action(reply).name, "list_dir")

    def test_takes_the_first_of_several_actions(self):
        reply = '<action name="note">one</action><action name="done">two</action>'
        self.assertEqual(parse_action(reply).name, "note")

    def test_preserves_body_indentation(self):
        body = "def f():\n    return 1\n"
        action = parse_action(f'<action name="write_file" path="f.py">\n{body}</action>')
        self.assertEqual(action.body, "def f():\n    return 1")

    def test_rejects_missing_and_unknown_actions(self):
        for reply in ("I think we should refactor everything.", '<action name="rm_rf">x</action>'):
            with self.assertRaises(ActionError):
                parse_action(reply)


class PathTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_accepts_a_relative_path(self):
        self.assertEqual(safe_path("src/a.py", self.root), self.root / "src" / "a.py")

    def test_refuses_absolute_paths(self):
        with self.assertRaises(ActionError):
            safe_path("/etc/passwd", self.root)

    def test_refuses_traversal_out_of_the_workspace(self):
        for raw in ("../escape.txt", "src/../../escape.txt", "a/b/../../../x"):
            with self.assertRaises(ActionError):
                safe_path(raw, self.root)

    def test_refuses_writing_the_harness_journal(self):
        for raw in (".agent", ".agent/journal.jsonl"):
            with self.assertRaises(ActionError):
                safe_path(raw, self.root)

    def test_refuses_empty_path(self):
        with self.assertRaises(ActionError):
            safe_path("   ", self.root)


class CommandTests(unittest.TestCase):
    def test_allows_listed_binaries(self):
        self.assertEqual(
            check_command("python3 -m unittest discover"),
            ["python3", "-m", "unittest", "discover"],
        )

    def test_refuses_unlisted_binaries(self):
        for cmd in ("curl http://example.com", "wget x", "ssh host", "bash -c 'x'", "rm -rf /"):
            with self.assertRaises(ActionError):
                check_command(cmd)

    def test_refuses_shell_operators(self):
        for cmd in ("python3 a.py | grep x", "git add . && git commit", "cat a > b"):
            with self.assertRaises(ActionError):
                check_command(cmd)

    def test_refuses_path_prefixed_escape(self):
        with self.assertRaises(ActionError):
            check_command("/usr/bin/curl http://example.com")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_then_read_round_trip(self):
        self.ws.write_file(Action("write_file", "hello\n", {"path": "a.txt"}))
        self.assertEqual(self.ws.read_file(Action("read_file", "", {"path": "a.txt"})), "hello\n")

    def test_run_records_the_real_exit_code(self):
        result = self.ws.run(Action("run", "", {"cmd": "python3 -c 'import sys; sys.exit(3)'"}))
        self.assertIn("exit code: 3", result)

    def test_run_captures_failure_output(self):
        self.ws.write_file(Action("write_file", "raise SystemExit('boom')\n", {"path": "f.py"}))
        result = self.ws.run(Action("run", "", {"cmd": "python3 f.py"}))
        self.assertIn("exit code: 1", result)
        self.assertIn("boom", result)

    def test_commit_returns_a_sha_for_real_changes(self):
        subprocess.run(["git", "init", "-q", "-b", "agent"], cwd=self.ws.root, check=True)
        self.ws.write_file(Action("write_file", "x = 1\n", {"path": "a.py"}))
        sha, error = self.ws.commit("first")
        self.assertIsNone(error)
        self.assertRegex(sha or "", r"^[0-9a-f]{7,}$")

    def test_clean_tree_is_distinguished_from_a_git_failure(self):
        subprocess.run(["git", "init", "-q", "-b", "agent"], cwd=self.ws.root, check=True)
        self.ws.write_file(Action("write_file", "x = 1\n", {"path": "a.py"}))
        self.ws.commit("first")
        sha, error = self.ws.commit("nothing changed since")
        self.assertEqual((sha, error), (None, None), "a clean tree is not an error")

    def test_git_failure_is_reported_not_silently_swallowed(self):
        # No git repo at all. The old code read this as "nothing to commit"
        # and produced a run with an empty history and no error anywhere.
        sha, error = self.ws.commit("into the void")
        self.assertIsNone(sha)
        self.assertIsNotNone(error, "a git failure must not look like a clean tree")

    def test_journal_round_trip(self):
        self.ws.append_journal({"event": "action", "iteration": 1, "summary": "x"})
        self.assertEqual(self.ws.read_journal()[-1]["summary"], "x")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--socket", type=Path, default=SOCKET_PATH)
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=int(os.environ.get("AGENT_MAX_ITERATIONS", "40")),
    )
    parser.add_argument("--model-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)

    if args.self_test:
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite(
            loader.loadTestsFromTestCase(case)
            for case in (ParseTests, PathTests, CommandTests, WorkspaceTests)
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    workspace = Workspace(args.work)
    charter_path = workspace.state / "CHARTER.md"
    if not charter_path.is_file():
        print(f"no charter at {charter_path}", file=sys.stderr)
        return 2
    charter = charter_path.read_text(encoding="utf-8")

    if not args.socket.exists():
        print(f"no broker socket at {args.socket}", file=sys.stderr)
        return 3

    return run_loop(
        workspace, charter, args.max_iterations, args.socket, args.model_timeout
    )


if __name__ == "__main__":
    raise SystemExit(main())
