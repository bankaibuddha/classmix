"""Guarded scratch directories for the agent-sandbox harness.

This module exists because of the 2026-08-18 incident recorded in
briefings/REGS-COMMON.md: one line of cleanup code,

    shutil.rmtree(Path(r.get("lease", {}).get("snapshot", "x")).parent.parent,
                  ignore_errors=True)

resolved to the repo root and deleted the motif unit's entire working tree,
silently, because ignore_errors=True never raised and never logged.

The j150 amendment showed the obvious fix is not enough: a string-prefix
test against gettempdir() is defeated by CWD anchoring, because .resolve()
turns a relative path into a real one and the scratch CWD lives under the
temp root. So this module implements the three independent gates that do
hold, in this order:

  1. Refuse a non-absolute path BEFORE calling .resolve().
  2. Containment by parents, not string prefix: root in path.parents AND
     path != root.
  3. Registry membership: the path must be one THIS registry created.

Plus: never ignore_errors. Failures raise. Every refusal is a named
exception so a caller cannot mistake a refusal for a successful delete.

Run this file directly to fire every refusal path and see the victim still
standing afterwards:

    python3 harness/scratch.py
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path


class DeleteRefused(Exception):
    """Raised when a delete target fails any of the three gates."""


class ScratchRegistry:
    """Creates scratch directories under one root and deletes only those.

    Nothing this registry did not create is ever a delete target, however
    well-formed the path looks.
    """

    def __init__(self, root: Path | str, prefix: str = "scratch-"):
        root = Path(root)
        if not root.is_absolute():
            raise ValueError(f"registry root must be absolute, got {root!r}")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.prefix = prefix
        self._created: set[Path] = set()

    def make(self, suffix: str = "") -> Path:
        """Create a scratch directory under the root and register it."""
        path = Path(
            tempfile.mkdtemp(prefix=self.prefix, suffix=suffix, dir=str(self.root))
        ).resolve()
        self._created.add(path)
        return path

    def owns(self, path: Path | str) -> bool:
        try:
            self._check(Path(path))
        except DeleteRefused:
            return False
        return True

    def remove(self, path: Path | str) -> Path:
        """Delete a registered scratch directory, or refuse and say why.

        Returns the resolved path that was deleted. Never swallows errors:
        a failed delete raises rather than reporting success.
        """
        target = self._check(Path(path))
        shutil.rmtree(target)  # deliberately NOT ignore_errors
        self._created.discard(target)
        return target

    def remove_all(self) -> list[Path]:
        removed = []
        for path in sorted(self._created):
            if path.exists():
                removed.append(self.remove(path))
            else:
                self._created.discard(path)
        return removed

    # -- gates ---------------------------------------------------------

    def _check(self, path: Path) -> Path:
        # Gate 1: refuse relative BEFORE resolving. Resolution is what
        # turns a relative string into a real target anchored at the CWD,
        # which is exactly how rmtree(".") happened.
        if not path.is_absolute():
            raise DeleteRefused(
                f"refusing relative path {str(path)!r}: a delete target must "
                "be absolute before it is resolved"
            )

        resolved = path.resolve()

        # Gate 2: containment by parents, not string prefix. A prefix test
        # is defeated by CWD anchoring; a parents test is not. The
        # inequality stops anyone deleting the root itself.
        if resolved == self.root:
            raise DeleteRefused(
                f"refusing the registry root itself: {resolved}"
            )
        if self.root not in resolved.parents:
            raise DeleteRefused(
                f"refusing {resolved}: not contained in registry root {self.root}"
            )

        # Gate 3: registry membership. Being in the right place is not
        # enough; this process must have created it.
        if resolved not in self._created:
            raise DeleteRefused(
                f"refusing {resolved}: not created by this registry"
            )

        return resolved


class GuardTests(unittest.TestCase):
    """Fires every refusal path on every run, and proves the victim lives.

    A guard is not landed until a test proves the original defect is
    refused, so the first test below is the literal 2026-08-18 expression.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.registry = ScratchRegistry(self.base / "runs")
        # The bystander. Every refusal test asserts this still exists.
        self.victim = self.base / "victim"
        self.victim.mkdir()
        (self.victim / "take-records.txt").write_text("573 records\n")

    def tearDown(self):
        self._tmp.cleanup()

    def assertVictimStanding(self):
        self.assertTrue(self.victim.exists(), "bystander directory was deleted")
        self.assertEqual(
            (self.victim / "take-records.txt").read_text(), "573 records\n"
        )

    def test_refuses_the_2026_08_18_expression(self):
        # Path("x").parent.parent is Path("."), the CWD. This is the exact
        # shape that wiped the motif working tree.
        doomed = Path("x").parent.parent
        with self.assertRaises(DeleteRefused) as ctx:
            self.registry.remove(doomed)
        self.assertIn("relative", str(ctx.exception))
        self.assertVictimStanding()

    def test_refuses_relative_path_anchored_under_root(self):
        # j150's hole: a relative path that .resolve() would anchor
        # somewhere legitimate-looking still gets refused at gate 1.
        with self.assertRaises(DeleteRefused):
            self.registry.remove("runs/scratch-whatever")
        self.assertVictimStanding()

    def test_refuses_absolute_path_outside_root(self):
        with self.assertRaises(DeleteRefused) as ctx:
            self.registry.remove(self.victim)
        self.assertIn("not contained", str(ctx.exception))
        self.assertVictimStanding()

    def test_refuses_the_root_itself(self):
        with self.assertRaises(DeleteRefused) as ctx:
            self.registry.remove(self.registry.root)
        self.assertIn("root itself", str(ctx.exception))
        self.assertTrue(self.registry.root.exists())
        self.assertVictimStanding()

    def test_refuses_unregistered_path_inside_root(self):
        # Right place, wrong provenance. Someone else's scratch dir, or one
        # left by a previous run, is not this process's to delete.
        stranger = self.registry.root / "scratch-not-mine"
        stranger.mkdir()
        with self.assertRaises(DeleteRefused) as ctx:
            self.registry.remove(stranger)
        self.assertIn("not created by this registry", str(ctx.exception))
        self.assertTrue(stranger.exists())
        self.assertVictimStanding()

    def test_refuses_traversal_back_out_of_root(self):
        mine = self.registry.make()
        escape = mine / ".." / ".." / "victim"
        with self.assertRaises(DeleteRefused):
            self.registry.remove(escape)
        self.assertVictimStanding()

    def test_deletes_what_it_created(self):
        mine = self.registry.make()
        (mine / "junk.txt").write_text("junk")
        self.assertTrue(mine.exists())
        removed = self.registry.remove(mine)
        self.assertEqual(removed, mine)
        self.assertFalse(mine.exists())
        self.assertVictimStanding()

    def test_second_delete_of_same_path_is_refused(self):
        mine = self.registry.make()
        self.registry.remove(mine)
        with self.assertRaises(DeleteRefused):
            self.registry.remove(mine)

    def test_failed_delete_raises_rather_than_reporting_success(self):
        mine = self.registry.make()
        shutil.rmtree(mine)  # vanish behind the registry's back
        with self.assertRaises(FileNotFoundError):
            self.registry.remove(mine)

    def test_registry_root_must_be_absolute(self):
        with self.assertRaises(ValueError):
            ScratchRegistry("relative/runs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
