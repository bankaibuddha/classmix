"""End-to-end proof of the loop, with no docker and no model required.

Runs the REAL agent loop against the REAL broker over a REAL unix socket,
with a scripted stand-in for the model. Everything between the socket and
the journal is the production code path; only the model's replies are
canned.

This exists because the containment tests need docker and the unit tests
only check components in isolation. The interesting failures live in the
seams: does an unparseable reply waste the run, does a failing test get
recorded honestly, does the budget actually stop the loop, does a "done"
claim get filed as a claim rather than believed.

    python3 tests/test_loop_integration.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

UNIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UNIT_ROOT / "agent"))
sys.path.insert(0, str(UNIT_ROOT / "broker"))
sys.path.insert(0, str(UNIT_ROOT / "harness"))

import agent as A  # noqa: E402
import broker as B  # noqa: E402
import run as R  # noqa: E402


class ScriptedModel:
    """Replies from a fixed list, then repeats the last one forever.

    Repeating rather than raising is deliberate: it models the real failure
    mode of a local model that gets stuck, so the budget has something to
    actually stop.
    """

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls: list[str] = []

    def __call__(self, system: str, prompt: str) -> str:
        self.calls.append(prompt)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]


class LoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.workspace = self.base / "workspace"
        R.seed_workspace(self.workspace, "Build a thing and test it.", None)
        self.registry = R.ScratchRegistry(self.base / "scratch", prefix="sock-")
        self.socket_dir = self.registry.make()
        self.socket = self.socket_dir / "model.sock"
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self._tmp.cleanup()

    def start(self, replies, **budget):
        model = ScriptedModel(replies)
        broker = B.Broker(model, B.Budget(**budget), journal_path=self.base / "broker.jsonl")
        self.server = B.serve(self.socket, broker)
        return model

    def loop(self, max_iterations=10):
        workspace = A.Workspace(self.workspace)
        code = A.run_loop(
            workspace,
            charter="Build a thing and test it.",
            max_iterations=max_iterations,
            socket_path=self.socket,
            model_timeout=30.0,
        )
        entries = []
        journal = self.workspace / ".agent" / "journal.jsonl"
        for line in journal.read_text(encoding="utf-8").splitlines():
            entries.append(json.loads(line))
        return code, entries

    # -- the happy path, end to end -----------------------------------

    def test_writes_code_runs_tests_and_commits(self):
        self.start(
            [
                '<action name="write_file" path="adder.py">\n'
                "def add(a, b):\n    return a + b\n"
                "</action>",
                '<action name="write_file" path="tests/test_adder.py">\n'
                "import sys, unittest\n"
                "sys.path.insert(0, '.')\n"
                "from adder import add\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n\n"
                "if __name__ == '__main__':\n    unittest.main()\n"
                "</action>",
                '<action name="run" cmd="python3 -m unittest discover -s tests">\n</action>',
                '<action name="done">\ntests pass\n</action>',
            ]
        )
        code, entries = self.loop()
        self.assertEqual(code, 0)

        # The files really exist on disk.
        self.assertTrue((self.workspace / "adder.py").is_file())
        self.assertIn("def add", (self.workspace / "adder.py").read_text())

        # The test run really happened and really passed.
        runs = [e for e in entries if e.get("action") == "run"]
        self.assertEqual(len(runs), 1)
        self.assertIn("exit code: 0", runs[0]["summary"])

        # Each write produced a real commit.
        commits = [e["commit"] for e in entries if e.get("commit")]
        self.assertEqual(len(commits), 2)
        log = R.git_in(self.workspace, "log", "--oneline")
        self.assertEqual(log.returncode, 0, log.stderr)
        self.assertEqual(len(log.stdout.strip().splitlines()), 3)  # seed + 2

        report = R.summarize(self.workspace)
        self.assertEqual(report["commands_nonzero_exit"], 0)
        self.assertTrue(report["agent_claimed_done"])

    # -- the honest-reporting path ------------------------------------

    def test_a_failing_test_is_recorded_as_failing(self):
        # The core anti-self-deception property: the agent says it is done,
        # the recorded exit code says the tests failed, and the summary keeps
        # both instead of taking the agent's word.
        self.start(
            [
                '<action name="write_file" path="tests/test_broken.py">\n'
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_fails(self):\n"
                "        self.assertEqual(1, 2)\n\n"
                "if __name__ == '__main__':\n    unittest.main()\n"
                "</action>",
                '<action name="run" cmd="python3 -m unittest discover -s tests">\n</action>',
                '<action name="done">\neverything passes, shipping it\n</action>',
            ]
        )
        code, entries = self.loop()
        self.assertEqual(code, 0)

        runs = [e for e in entries if e.get("action") == "run"]
        self.assertIn("exit code: 1", runs[0]["summary"])

        report = R.summarize(self.workspace)
        self.assertTrue(report["agent_claimed_done"])
        self.assertIn("everything passes", report["agent_claim"])
        # The claim and the measurement disagree, and the report shows it.
        self.assertEqual(report["commands_nonzero_exit"], 1)

    # -- the pathological paths ---------------------------------------

    def test_unparseable_replies_do_not_wedge_the_loop(self):
        self.start(
            [
                "I think the best approach here is to refactor the entire module.",
                '<action name="write_file" path="ok.py">\nx = 1\n</action>',
                '<action name="done">\nwrote it\n</action>',
            ]
        )
        code, entries = self.loop()
        self.assertEqual(code, 0)
        unparseable = [e for e in entries if e.get("action") == "unparseable"]
        self.assertEqual(len(unparseable), 1)
        self.assertTrue((self.workspace / "ok.py").is_file())

    def test_broker_request_cap_stops_a_stuck_agent(self):
        # The model repeats one action forever. The in-container iteration cap
        # is 50, but the broker's cap of 3 is outside the container and fires
        # first. This is the control that does not depend on the loop.
        model = self.start(
            ['<action name="note">\nthinking about it\n</action>'],
            max_requests=3,
        )
        code, entries = self.loop(max_iterations=50)
        self.assertEqual(code, 0)
        self.assertEqual(len(model.calls), 3, "broker must stop calling the model")
        refusal = [e for e in entries if e.get("event") == "broker_refused"]
        self.assertEqual(len(refusal), 1)
        self.assertEqual(refusal[0]["kind"], "budget")
        self.assertEqual(R.summarize(self.workspace)["stop_reason"], "broker_refused")

    def test_iteration_cap_stops_the_loop(self):
        self.start(['<action name="note">\nstill thinking\n</action>'], max_requests=500)
        code, entries = self.loop(max_iterations=4)
        self.assertEqual(code, 0)
        notes = [e for e in entries if e.get("action") == "note"]
        self.assertEqual(len(notes), 4)
        self.assertTrue(any(e.get("event") == "iteration_cap_reached" for e in entries))

    def test_agent_cannot_escape_the_workspace_or_edit_the_journal(self):
        self.start(
            [
                '<action name="write_file" path="../../escaped.txt">\ngot out\n</action>',
                '<action name="write_file" path=".agent/journal.jsonl">\n[]\n</action>',
                '<action name="run" cmd="curl http://example.com">\n</action>',
                '<action name="done">\ngave up\n</action>',
            ]
        )
        code, entries = self.loop()
        self.assertEqual(code, 0)

        failed = [e for e in entries if e.get("event") == "action" and not e.get("ok")]
        self.assertEqual(len(failed), 3, "all three attempts must be refused")
        self.assertFalse((self.base / "escaped.txt").exists())
        self.assertFalse((self.workspace.parent / "escaped.txt").exists())
        # The journal is intact and still readable as JSON lines.
        self.assertGreater(len(entries), 3)

    def test_broker_unreachable_is_reported_not_crashed(self):
        # No broker started at all.
        workspace = A.Workspace(self.workspace)
        code = A.run_loop(workspace, "charter", 5, self.socket, 5.0)
        self.assertEqual(code, 3)
        journal = (self.workspace / ".agent" / "journal.jsonl").read_text()
        self.assertIn("broker_unreachable", journal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
