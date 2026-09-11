# agent-sandbox — ROADMAP

Unit charter: a self-directed local coding agent that can be left running,
with containment strong enough that "left running" is a reasonable thing to
do. The 2026-08-18 wipe is the standing argument for why the sandbox comes
before the agent. Owner: the Commander. Drafted 2026-09-11.

## NOW — in flight

- [x] v1 harness shipped — broker, loop, orchestrator, delete guard —
  gate: 71 non-docker tests pass (passed 2026-09-11)
- [ ] Containment proven on celeste — gate: `tests/test_containment.sh`
  reports zero failures on the GX10, every denial firing. UNPROVEN: written
  on a box with the docker CLI and no daemon, so the checks that matter
  most have never executed
- [ ] First real run — gate: `charters/lemon-looker.md` run end to end,
  `report.json` produced, git history reviewable
- [ ] REGS-COMMON deployed from the doctrine master — gate:
  `verify-parity.sh` exits 0 for this unit

## NEXT — scoped, not cut

- Measure where a local model degrades — how many iterations before it
  starts rewriting the same function, breaking passing tests, or declaring
  victory against a red suite — gate: three runs at different model sizes
  with the divergence point recorded from the journal, not from impression
- Critic tier — the 4080 laptop running a smaller model that reviews the
  main agent's diffs, over an SSH tunnel, never an open port — gate: the
  critic catches a defect the main agent's own suite passed
- Mac Mini as scheduler and journal host — gate: a run dispatched and
  collected without touching celeste directly
- Charter library — one per target repo, each carrying the command behind
  every premise per the briefing reg

## LATER — direction

- Hosted-engine path behind an explicit flag, with the weakened containment
  printed at start and a hard budget cap required before it will run
- Resume a stopped run from its journal rather than starting over
- Multi-agent: two sandboxes on one charter, diffs compared

## Graduation log

- 2026-09-11 — unit chartered; v1 harness entered NOW already done (built
  the same day the unit stood up). Five defects found by its own tests
  during the build, all fixed before first run.
