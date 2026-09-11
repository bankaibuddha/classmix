"""Host-side orchestrator. Starts the broker, runs the sandbox, judges it.

Everything security-relevant about this system is either in broker.py or in
the container_command() function below. That function is the whole
containment story in one readable block, deliberately, instead of spread
across a compose file where flags are easy to lose.

Three invariants this file exists to hold:

  1. The live worktree is NEVER mounted. A seed directory is COPIED into a
     run workspace the harness created. There is no code path that hands a
     container a bind mount of a repo you care about, so the 2026-08-18
     failure mode cannot reach anything but a disposable copy.
  2. The agent's verdict is not the verdict. After the loop ends, the host
     re-runs the project's tests in a FRESH container and records that exit
     code itself. The agent's "done" message is filed as a claim next to a
     measurement that either backs it or does not.
  3. Budgets are enforced twice, in two processes. The in-container loop
     counts iterations; the broker out here counts requests, characters and
     wall clock and stops answering regardless of what the loop believes.

Usage:

    python3 harness/run.py --charter CHARTER.example.md --model qwen2.5-coder:32b
    python3 harness/run.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT_ROOT = HERE.parent
DEFAULT_IMAGE = "agent-sandbox:v1"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"

# The unprivileged uid baked into the image by docker/Dockerfile.
IMAGE_UID = 1000
IMAGE_GID = 1000

sys.path.insert(0, str(UNIT_ROOT / "harness"))
from scratch import DeleteRefused, ScratchRegistry  # noqa: E402

IGNORED_SEED = shutil.ignore_patterns(
    ".git", ".agent", "__pycache__", "*.pyc", ".venv", "node_modules", "runs"
)


class PreflightError(Exception):
    """A precondition failed. Nothing has been started yet."""


@dataclass
class Limits:
    """Every bound on the run, in one place, so none of them is implicit."""

    iterations: int = 40
    minutes: float = 90.0
    broker_requests: int = 200
    broker_chars: int = 8_000_000
    cpus: str = "4"
    memory: str = "4g"
    pids: int = 256
    tmpfs_size: str = "256m"
    cmd_timeout: int = 300

    @property
    def seconds(self) -> float:
        return self.minutes * 60.0


def git_in(workspace: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run git against the workspace from the host side.

    The -c flags are per-invocation on purpose. When the harness runs as root
    the workspace is chowned to the sandbox uid, and git then refuses it as
    "dubious ownership". The documented fix git suggests is `git config
    --global --add safe.directory`, which would permanently edit the
    operator's own git configuration on the host. This harness does not get
    to do that, so the exception is scoped to this one command instead.
    """
    return subprocess.run(
        [
            "git",
            "-c", f"safe.directory={workspace}",
            "-c", "user.name=agent-sandbox",
            "-c", "user.email=agent-sandbox@localhost",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=check,
    )


def container_user() -> tuple[int, int]:
    """The uid:gid the sandbox runs as. Never root, even when the host is.

    Pinning to the invoking uid is what keeps workspace files owned by the
    operator instead of by root. But that reasoning inverts when the harness
    itself is run as root (a CI runner, a container, someone's sudo habit):
    passing --user 0:0 would then hand the sandbox exactly the privilege
    every other flag here is removing. So uid 0 falls back to the
    unprivileged uid baked into the image, and seed_workspace() chowns the
    workspace so that user can still write to it.
    """
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        return IMAGE_UID, IMAGE_GID
    return uid, gid


def container_command(
    *,
    name: str,
    image: str,
    workspace: Path,
    agent_dir: Path,
    socket_path: Path,
    limits: Limits,
) -> list[str]:
    """The containment story, in one block.

    Read this as a list of capabilities REMOVED. Each flag is here because
    of a specific thing the agent would otherwise be able to do.
    """
    return [
        "docker", "run", "--rm", "--name", name,

        # No IP stack at all. Not a firewall, not an allowlist, not a proxy
        # policy that could have a bug: there is no interface to send a
        # packet on. No DNS, no LAN, no internet, no other container.
        "--network", "none",

        # Non-root, and normally the invoking user's uid, so files written
        # into the workspace are owned by the operator rather than by root
        # and nothing in there needs sudo to clean up. See container_user()
        # for why a root harness does NOT get a root sandbox.
        "--user", "%d:%d" % container_user(),

        # No capabilities, and no path to acquire any. Drops CAP_CHOWN,
        # CAP_DAC_OVERRIDE, CAP_SETUID and the rest; no-new-privileges makes
        # a setuid binary in the image inert.
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",

        # Read-only root filesystem. The agent cannot modify the image it is
        # running in, install anything, or leave anything behind outside the
        # two writable paths below.
        "--read-only",

        # The only writable places. /tmp is a tmpfs so it vanishes with the
        # container, and noexec means a payload written there cannot be
        # executed from there.
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={limits.tmpfs_size}",
        "-v", f"{workspace}:/work:rw",

        # The loop itself, read-only. The agent cannot edit its own
        # iteration cap, its own tool allowlist, or its own journal writer.
        "-v", f"{agent_dir}:/opt/agent:ro",

        # The single egress: one unix socket to the broker. Note what is NOT
        # mounted here: no docker socket (the classic container-to-root
        # escape), no host filesystem, no ssh keys, no credentials.
        "-v", f"{socket_path}:/run/model.sock:rw",

        # Resource ceilings. A runaway loop burns this much and no more, and
        # cannot fork-bomb the host out of pids.
        "--cpus", limits.cpus,
        "--memory", limits.memory,
        "--memory-swap", limits.memory,
        "--pids-limit", str(limits.pids),

        # No host PID or IPC namespace: the agent cannot see, signal, or
        # share memory with anything on the box.
        "--pid", "private",
        "--ipc", "private",

        "-e", f"AGENT_MAX_ITERATIONS={limits.iterations}",
        "-e", f"AGENT_CMD_TIMEOUT={limits.cmd_timeout}",
        "-w", "/work",
        image,
        "python3", "/opt/agent/agent.py",
    ]


def verify_command(*, name: str, image: str, workspace: Path, cmd: list[str]) -> list[str]:
    """Same containment, fresh container, host-chosen command.

    This is how the harness forms its own verdict. The agent is not running
    and cannot influence this, and the workspace is mounted read-only so the
    verification cannot be one that fixes itself.
    """
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--user", "%d:%d" % container_user(),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "-v", f"{workspace}:/work:ro",
        "--pids-limit", "128",
        "--memory", "2g",
        "-w", "/work",
        image,
        *cmd,
    ]


# -- preflight ----------------------------------------------------------


def preflight(image: str, ollama_url: str, model: str) -> dict:
    """Check every precondition before anything is started or created."""
    facts = {}

    # Policy refusals come FIRST, before any environment check. A refusal
    # that depends on docker being installed is a refusal that silently
    # stops being enforced on a box where something else fails earlier.
    if not (ollama_url.startswith("http://127.0.0.1") or ollama_url.startswith("http://localhost")):
        raise PreflightError(
            f"refusing non-loopback model endpoint {ollama_url!r}.\n"
            "v1 is single-box. Ollama has no authentication, so a LAN-reachable\n"
            "endpoint is an unauthenticated service on your network. For\n"
            "cross-box inference, forward it over SSH and point this at the\n"
            "local end of the tunnel."
        )

    if shutil.which("docker") is None:
        raise PreflightError("docker is not on PATH")

    probe = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True
    )
    if probe.returncode != 0:
        raise PreflightError(
            f"image {image!r} not built. Run: scripts/build.sh"
        )
    facts["image"] = image

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{ollama_url.rstrip('/')}/api/tags", timeout=10) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise PreflightError(
            f"cannot reach the model server at {ollama_url}: {exc}\n"
            "Start it first (`ollama serve`) and pull a coding model."
        ) from exc

    available = [m.get("name", "") for m in tags.get("models", [])]
    if model not in available:
        raise PreflightError(
            f"model {model!r} is not pulled. Available: {available or '(none)'}\n"
            f"Run: ollama pull {model}"
        )
    facts["model"] = model
    facts["models_available"] = available
    return facts


# -- run ----------------------------------------------------------------


def copy_seed(seed: Path, workspace: Path, tracked_only: bool = True) -> tuple[int, str]:
    """Copy a seed directory into the workspace. Returns (file count, method).

    When the seed is a git repository, copy ONLY the files git tracks.

    This is not a tidiness preference, it is the point. A working clone holds
    exactly what its .gitignore exists to keep out of version control: .env
    files, API tokens, local databases, scraped caches, credentials. Those are
    the last things that should be handed to an agent running unattended, and
    "copy the directory, skipping a few well-known names" hands all of them
    over while looking careful. The tracked set is the set the operator has
    already decided is safe to publish, so that is the set the sandbox gets.

    The tradeoff, stated because it will bite someone: a file that is new and
    not yet added to git is invisible to the agent. Commit it first, or pass
    --seed-all to fall back to a filtered directory copy.
    """
    probe = subprocess.run(
        ["git", "-C", str(seed), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    is_repo = probe.returncode == 0 and probe.stdout.strip() == "true"

    workspace.mkdir(parents=True, exist_ok=True)
    if not (tracked_only and is_repo):
        shutil.copytree(seed, workspace, dirs_exist_ok=True, ignore=IGNORED_SEED)
        count = sum(1 for p in workspace.rglob("*") if p.is_file())
        return count, "directory copy (filtered)" if not is_repo else "directory copy (--seed-all)"

    listing = subprocess.run(
        ["git", "-C", str(seed), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    )
    count = 0
    for name in listing.stdout.split("\0"):
        if not name:
            continue
        source = seed / name
        if not source.is_file():  # submodule entries, deleted-but-tracked
            continue
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count, "git-tracked files only"


def seed_workspace(workspace: Path, charter_text: str, seed: Path | None,
                   tracked_only: bool = True) -> None:
    """Build the disposable workspace. The seed is copied, never mounted."""
    workspace.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        seed = seed.resolve()
        if not seed.is_dir():
            raise PreflightError(f"seed is not a directory: {seed}")
        if seed == workspace or seed in workspace.parents or workspace in seed.parents:
            raise PreflightError(
                f"refusing seed {seed}: it contains or is contained by the run "
                "workspace, which would copy a directory into itself"
            )
        # A copy, never a bind mount. The original is not reachable from
        # inside the container at all.
        count, method = copy_seed(seed, workspace, tracked_only)
        print(f"seed:      {count} files from {seed} ({method})")

    state = workspace / ".agent"
    state.mkdir(exist_ok=True)
    (state / "CHARTER.md").write_text(charter_text, encoding="utf-8")

    if not (workspace / ".git").is_dir():
        git_in(workspace, "init", "-q", "-b", "agent", check=True)
    git_in(workspace, "add", "-A", check=True)
    git_in(workspace, "commit", "-q", "-m", "harness: seed workspace and charter",
           "--allow-empty", check=True)

    # No remote, ever. `git init` creates none, and the seed's .git is not
    # copied, so the agent has nothing to push to even if it tried. Asserted
    # here rather than assumed, because inheriting a remote is the one way a
    # sandboxed commit could escape the sandbox.
    remotes = git_in(workspace, "remote")
    if remotes.stdout.strip():
        raise PreflightError(
            f"workspace has git remote(s) {remotes.stdout.split()!r}; refusing to run. "
            "The sandbox must have no push target."
        )

    # If the harness is root, the sandbox runs as IMAGE_UID (see
    # container_user), so hand that uid ownership or it cannot write.
    uid, gid = container_user()
    if os.getuid() == 0:
        for path in [workspace, *workspace.rglob("*")]:
            os.chown(path, uid, gid)


def summarize(workspace: Path) -> dict:
    """Read the journal and report what MEASURABLY happened."""
    journal_path = workspace / ".agent" / "journal.jsonl"
    entries = []
    if journal_path.is_file():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    actions = [e for e in entries if e.get("event") == "action"]
    runs = [e for e in actions if e.get("action") == "run"]
    failing_runs = [e for e in runs if "exit code: 0" not in (e.get("summary") or "")]
    done = next((e for e in entries if e.get("event") == "done"), None)
    stop = next(
        (
            e
            for e in entries
            if e.get("event")
            in {"iteration_cap_reached", "broker_refused", "broker_unreachable", "done"}
        ),
        None,
    )
    return {
        "actions": len(actions),
        "unparseable_replies": sum(1 for e in actions if e.get("action") == "unparseable"),
        "failed_actions": sum(1 for e in actions if not e.get("ok")),
        "commands_run": len(runs),
        "commands_nonzero_exit": len(failing_runs),
        "files_written": sum(1 for e in actions if e.get("action") == "write_file" and e.get("ok")),
        "commits": sum(1 for e in actions if e.get("commit")),
        "agent_claimed_done": bool(done),
        "agent_claim": (done or {}).get("claim"),
        "stop_reason": (stop or {}).get("event", "unknown"),
    }


def run(args: argparse.Namespace) -> int:
    charter_path = Path(args.charter).resolve()
    if not charter_path.is_file():
        raise PreflightError(f"no charter at {charter_path}")
    charter_text = charter_path.read_text(encoding="utf-8")

    facts = preflight(args.image, args.ollama_url, args.model)

    limits = Limits(
        iterations=args.iterations,
        minutes=args.minutes,
        broker_requests=args.broker_requests,
        cpus=args.cpus,
        memory=args.memory,
    )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = (Path(args.runs_dir).resolve() / stamp)
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=False)

    # Scratch registry owns the socket directory. Its three gates are what
    # stand between a cleanup bug and the rest of the disk.
    registry = ScratchRegistry(run_dir / "scratch", prefix="sock-")
    socket_dir = registry.make()
    socket_path = socket_dir / "model.sock"

    seed_workspace(
        workspace,
        charter_text,
        Path(args.seed).resolve() if args.seed else None,
        tracked_only=not args.seed_all,
    )

    broker_journal = run_dir / "broker.jsonl"
    broker_cmd = [
        sys.executable,
        str(UNIT_ROOT / "broker" / "broker.py"),
        "--socket", str(socket_path),
        "--model", args.model,
        "--base-url", args.ollama_url,
        "--journal", str(broker_journal),
        "--max-requests", str(limits.broker_requests),
        "--max-total-chars", str(limits.broker_chars),
        "--max-seconds", str(limits.seconds),
    ]

    print(f"run dir:   {run_dir}")
    print(f"model:     {args.model} via {args.ollama_url}")
    print(f"limits:    {limits.iterations} iterations, {limits.minutes:g} min, "
          f"{limits.broker_requests} broker requests, {limits.cpus} cpus, {limits.memory}")
    print()

    broker_proc = subprocess.Popen(broker_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
    container_name = f"agent-sandbox-{stamp}"
    started = time.monotonic()
    exit_code = 0
    timed_out = False

    try:
        deadline = 0.0
        while not socket_path.exists():
            if broker_proc.poll() is not None:
                output = broker_proc.stdout.read() if broker_proc.stdout else ""
                raise PreflightError(f"broker exited before binding its socket:\n{output}")
            time.sleep(0.1)
            deadline += 0.1
            if deadline > 30:
                raise PreflightError("broker did not bind its socket within 30s")

        cmd = container_command(
            name=container_name,
            image=args.image,
            workspace=workspace,
            agent_dir=UNIT_ROOT / "agent",
            socket_path=socket_path,
            limits=limits,
        )
        print("$ " + " ".join(cmd) + "\n", flush=True)
        try:
            completed = subprocess.run(cmd, timeout=limits.seconds + 60)
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            # Host-side wall clock is authoritative. The in-container loop
            # and the broker each have their own cap; this is the third and
            # it does not depend on either of them behaving.
            print("\nwall clock exceeded, stopping the container", flush=True)
            subprocess.run(["docker", "stop", "-t", "10", container_name],
                           capture_output=True)
            exit_code = 124
    finally:
        broker_proc.terminate()
        try:
            broker_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            broker_proc.kill()
        try:
            registry.remove(socket_dir)
        except (DeleteRefused, OSError) as exc:
            print(f"scratch cleanup refused or failed (nothing deleted): {exc}",
                  file=sys.stderr)

    elapsed = time.monotonic() - started
    report = summarize(workspace)
    report.update(
        {
            "run_dir": str(run_dir),
            "elapsed_s": round(elapsed, 1),
            "container_exit": exit_code,
            "wall_clock_exceeded": timed_out,
            **facts,
        }
    )

    # The harness verdict. The agent is gone; this runs in a fresh container
    # against a read-only mount, so it cannot be the agent's own opinion.
    if args.verify_cmd:
        verify_name = f"agent-verify-{stamp}"
        vcmd = verify_command(
            name=verify_name,
            image=args.image,
            workspace=workspace,
            cmd=args.verify_cmd.split(),
        )
        print(f"\nharness verification: {args.verify_cmd}", flush=True)
        vres = subprocess.run(vcmd, capture_output=True, text=True, timeout=600)
        report["verify_cmd"] = args.verify_cmd
        report["verify_exit"] = vres.returncode
        report["verify_tail"] = (vres.stdout + vres.stderr)[-4000:]
        print(f"verification exit code: {vres.returncode}")

    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n--- what measurably happened ---")
    for key in (
        "actions", "files_written", "commits", "commands_run", "commands_nonzero_exit",
        "unparseable_replies", "failed_actions", "stop_reason", "elapsed_s",
    ):
        print(f"  {key}: {report[key]}")
    if report.get("agent_claimed_done"):
        print(f"\n  agent CLAIMED done: {str(report['agent_claim'])[:400]}")
        if "verify_exit" in report:
            verdict = "backed by verification" if report["verify_exit"] == 0 else "NOT backed by verification"
            print(f"  that claim is {verdict} (exit {report['verify_exit']})")

    log = git_in(workspace, "log", "--oneline", "--no-decorate")
    print(f"\n--- git history in {workspace} ---")
    print(log.stdout.strip() or "(no commits)")
    print(f"\nfull report: {run_dir / 'report.json'}")
    return 0 if exit_code in (0, 124) else exit_code


# -- self-tests ---------------------------------------------------------


class ContainerFlagTests(unittest.TestCase):
    """These assert the containment flags are PRESENT.

    A missing flag is not a style problem, it is the removal of a control,
    and it would not otherwise fail any test. Reg: a control exists only if
    it fires, so the flags get asserted rather than assumed.
    """

    def build(self):
        return container_command(
            name="t", image="img",
            workspace=Path("/runs/1/workspace"),
            agent_dir=Path("/unit/agent"),
            socket_path=Path("/runs/1/scratch/model.sock"),
            limits=Limits(),
        )

    def test_network_is_none(self):
        cmd = self.build()
        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")

    def test_drops_all_capabilities_and_blocks_new_privileges(self):
        cmd = self.build()
        self.assertEqual(cmd[cmd.index("--cap-drop") + 1], "ALL")
        self.assertIn("no-new-privileges", cmd)

    def test_root_filesystem_is_read_only(self):
        self.assertIn("--read-only", self.build())

    def test_runs_as_non_root(self):
        cmd = self.build()
        user = cmd[cmd.index("--user") + 1]
        self.assertNotEqual(user.split(":")[0], "0", "container must not run as root")

    def test_root_harness_does_not_produce_a_root_sandbox(self):
        # The inversion worth guarding: --user $(id -u) is the right flag
        # until the harness is run as root, when it hands the sandbox exactly
        # the privilege every other flag removes.
        from unittest import mock

        with mock.patch.object(os, "getuid", return_value=0), \
             mock.patch.object(os, "getgid", return_value=0):
            self.assertEqual(container_user(), (IMAGE_UID, IMAGE_GID))
            user = self.build()[self.build().index("--user") + 1]
            self.assertEqual(user, f"{IMAGE_UID}:{IMAGE_GID}")

    def test_normal_user_keeps_their_own_uid(self):
        from unittest import mock

        with mock.patch.object(os, "getuid", return_value=501), \
             mock.patch.object(os, "getgid", return_value=20):
            self.assertEqual(container_user(), (501, 20))

    def test_agent_loop_is_mounted_read_only(self):
        mounts = [cmd for cmd in self.build() if ":/opt/agent" in cmd]
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0].endswith(":ro"), "the loop must not be writable")

    def test_docker_socket_is_never_mounted(self):
        # The classic container-to-host-root escape.
        joined = " ".join(self.build())
        self.assertNotIn("docker.sock", joined)
        self.assertNotIn("/var/run/docker", joined)

    def test_only_workspace_tmpfs_and_model_socket_are_writable(self):
        writable = [c for c in self.build() if c.endswith(":rw")]
        self.assertEqual(
            sorted(writable),
            sorted(["/runs/1/workspace:/work:rw", "/runs/1/scratch/model.sock:/run/model.sock:rw"]),
        )

    def test_resource_ceilings_are_set(self):
        cmd = self.build()
        for flag in ("--cpus", "--memory", "--pids-limit"):
            self.assertIn(flag, cmd)

    def test_namespaces_are_private(self):
        cmd = self.build()
        self.assertEqual(cmd[cmd.index("--pid") + 1], "private")
        self.assertEqual(cmd[cmd.index("--ipc") + 1], "private")

    def test_verify_container_mounts_workspace_read_only(self):
        cmd = verify_command(
            name="v", image="img", workspace=Path("/runs/1/workspace"),
            cmd=["python3", "-m", "unittest"],
        )
        self.assertIn("/runs/1/workspace:/work:ro", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")


class PreflightTests(unittest.TestCase):
    def test_refuses_non_loopback_model_endpoint(self):
        for url in ("http://192.168.1.50:11434", "http://celeste.local:11434",
                    "https://10.0.0.4:11434"):
            with self.assertRaises(PreflightError) as ctx:
                preflight("img", url, "m")
            self.assertIn("loopback", str(ctx.exception))


class SeedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_seed_is_copied_not_referenced(self):
        seed = self.base / "source"
        (seed / "src").mkdir(parents=True)
        (seed / "src" / "a.py").write_text("original\n")
        workspace = self.base / "runs" / "1" / "workspace"
        seed_workspace(workspace, "charter", seed)

        # Mutating the copy must not touch the original.
        (workspace / "src" / "a.py").write_text("agent scribbled here\n")
        self.assertEqual((seed / "src" / "a.py").read_text(), "original\n")

    def test_seed_does_not_inherit_a_push_target(self):
        # The invariant that matters is not "no .git" -- git init makes one.
        # It is that the workspace has NO REMOTE, so a commit the agent makes
        # has nowhere to go. Inheriting the seed's remote would be the one
        # way work could leave the sandbox.
        seed = self.base / "source"
        (seed / ".git").mkdir(parents=True)
        (seed / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = git@github.com:someone/private.git\n'
        )
        (seed / "keep.py").write_text("x\n")
        workspace = self.base / "runs" / "2" / "workspace"
        seed_workspace(workspace, "charter", seed)

        self.assertTrue((workspace / "keep.py").is_file(), "real files must be copied")
        remotes = git_in(workspace, "remote", "-v")
        self.assertEqual(remotes.stdout.strip(), "", "workspace must have no push target")
        config = (workspace / ".git" / "config").read_text()
        self.assertNotIn("github.com", config)

    def make_repo_seed(self) -> Path:
        """A seed shaped like a real working clone: tracked code plus the
        untracked, gitignored things a clone actually accumulates."""
        seed = self.base / "repo"
        (seed / "fetchers").mkdir(parents=True)
        (seed / "fetchers" / "fetch.py").write_text("# tracked code\n")
        (seed / "README.md").write_text("# tracked docs\n")
        (seed / ".gitignore").write_text("store/\n*.sqlite\n.env\n")

        # The things .gitignore exists to keep out of version control.
        (seed / ".env").write_text("NTFY_TOPIC=real-secret-topic\n")
        (seed / "listings.sqlite").write_text("binary-ish database contents\n")
        (seed / "store" / "raw").mkdir(parents=True)
        (seed / "store" / "raw" / "cached.raw").write_text("scraped page contents\n")

        for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@t",
                      "commit", "-q", "-m", "initial"]):
            subprocess.run(["git", "-C", str(seed), *args], check=True,
                           capture_output=True)
        return seed

    def test_untracked_secrets_never_reach_the_sandbox(self):
        # The whole reason copy_seed prefers git: a filtered directory copy
        # would hand an unattended agent the .env, the local database and the
        # scraped cache, while looking careful because it skipped .git.
        seed = self.make_repo_seed()
        workspace = self.base / "runs" / "10" / "workspace"
        seed_workspace(workspace, "charter", seed)

        self.assertTrue((workspace / "fetchers" / "fetch.py").is_file())
        self.assertTrue((workspace / "README.md").is_file())
        for leaked in (".env", "listings.sqlite", "store/raw/cached.raw", "store"):
            self.assertFalse(
                (workspace / leaked).exists(),
                f"gitignored {leaked} must not reach the sandbox",
            )
        # And the secret is nowhere in the workspace at all, under any name.
        blob = "".join(
            p.read_text(errors="replace")
            for p in workspace.rglob("*")
            if p.is_file() and ".git/" not in str(p)
        )
        self.assertNotIn("real-secret-topic", blob)

    def test_seed_all_opts_back_into_copying_everything(self):
        seed = self.make_repo_seed()
        workspace = self.base / "runs" / "11" / "workspace"
        seed_workspace(workspace, "charter", seed, tracked_only=False)
        # Opt-in, explicit, and it does what it says.
        self.assertTrue((workspace / ".env").is_file())
        self.assertTrue((workspace / "store" / "raw" / "cached.raw").is_file())

    def test_non_git_seed_falls_back_to_directory_copy(self):
        seed = self.base / "plain"
        seed.mkdir()
        (seed / "a.py").write_text("x\n")
        workspace = self.base / "runs" / "12" / "workspace"
        seed_workspace(workspace, "charter", seed)
        self.assertTrue((workspace / "a.py").is_file())

    def test_copy_seed_reports_which_method_it_used(self):
        seed = self.make_repo_seed()
        count, method = copy_seed(seed, self.base / "runs" / "13" / "ws")
        self.assertEqual(method, "git-tracked files only")
        self.assertEqual(count, 3)  # fetch.py, README.md, .gitignore

    def test_refuses_seed_containing_the_workspace(self):
        workspace = self.base / "outer" / "runs" / "1" / "workspace"
        with self.assertRaises(PreflightError):
            seed_workspace(workspace, "charter", self.base / "outer")

    def test_charter_lands_where_the_agent_looks_for_it(self):
        workspace = self.base / "runs" / "3" / "workspace"
        seed_workspace(workspace, "do the thing", None)
        self.assertEqual((workspace / ".agent" / "CHARTER.md").read_text(), "do the thing")

    def test_workspace_is_a_git_repo_with_a_seed_commit(self):
        workspace = self.base / "runs" / "4" / "workspace"
        seed_workspace(workspace, "charter", None)
        log = git_in(workspace, "log", "--oneline")
        self.assertEqual(log.returncode, 0, log.stderr)
        self.assertIn("seed workspace", log.stdout)


class SummaryTests(unittest.TestCase):
    def test_separates_the_agents_claim_from_the_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            state = workspace / ".agent"
            state.mkdir()
            lines = [
                {"event": "action", "iteration": 1, "action": "write_file",
                 "ok": True, "commit": "abc1234", "summary": "created t.py"},
                {"event": "action", "iteration": 2, "action": "run",
                 "ok": True, "summary": "exit code: 1\nfailures=2"},
                {"event": "done", "iteration": 3, "claim": "all tests pass"},
            ]
            (state / "journal.jsonl").write_text(
                "\n".join(json.dumps(x) for x in lines) + "\n"
            )
            report = summarize(workspace)
            self.assertTrue(report["agent_claimed_done"])
            # The claim says pass; the recorded exit code says otherwise, and
            # the report keeps both rather than believing the agent.
            self.assertEqual(report["commands_nonzero_exit"], 1)
            self.assertEqual(report["files_written"], 1)
            self.assertEqual(report["commits"], 1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--charter", default=str(UNIT_ROOT / "CHARTER.example.md"))
    parser.add_argument("--seed", help="directory to COPY into the workspace (never mounted)")
    parser.add_argument(
        "--seed-all", action="store_true",
        help="copy every file in the seed, not just git-tracked ones. Off by "
             "default because untracked files are where .env, credentials, "
             "local databases and scraped caches live.",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--model", default="qwen2.5-coder:32b")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    parser.add_argument("--runs-dir", default=str(UNIT_ROOT / "runs"))
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--minutes", type=float, default=90.0)
    parser.add_argument("--broker-requests", type=int, default=200)
    parser.add_argument("--cpus", default="4")
    parser.add_argument("--memory", default="4g")
    parser.add_argument(
        "--verify-cmd",
        default="python3 -m unittest discover -s tests -p test_*.py",
        help="run by the HARNESS in a fresh container to form its own verdict",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite(
            loader.loadTestsFromTestCase(case)
            for case in (ContainerFlagTests, PreflightTests, SeedTests, SummaryTests)
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    try:
        return run(args)
    except PreflightError as exc:
        print(f"preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
