<!-- BEGIN COMMON REGS (deployed, do not edit here) -->
<!-- PROVENANCE - canonical common regs, doctrine unit.

This file is the MASTER. It is deployed, byte-for-byte, into each unit's
`briefings/REGS-COMMON.md`, wrapped in the BEGIN/END COMMON REGS markers
that `verify-parity.sh` looks for. Edit HERE, in the doctrine unit, then run
`deploy-<node>.sh`. `verify-parity.sh` hash-compares every deployed copy
against this file and exits non-zero on drift. Do not edit a deployed copy.

Adopted 2026-08-23 by JOB-05 "HOME ADDRESS" (OPERATION SOVEREIGN phase 2)
from the motif unit's `briefings/REGS-COMMON.md`, which was the fleet's only
copy - the reason every dispatch in every unit had to send a troop into
another unit's repo to read its own rules.

  source, as read at adoption : 315 lines, CRLF
    sha256 c59c470061db842789311ce685df2203fdfb5600d2e1e23ec4db58bca286fa64
  same source, CRLF -> LF     : (this repo pins `* text=auto eol=lf`; see
                                 .gitattributes and the 2026-07-29 scar)
    sha256 68c96f256d30f69b6d102a1173130e13415d71115e52bfbff79a9e771055c85d

Delta from that source: line endings normalized to LF, this provenance block
prepended, and FOUR hardcoded absolute paths into the motif unit replaced by
unit-relative wording - the whole point of the operation, and required
because this file now also lives in units where a drive-letter path to the
motif repo means nothing. NO rule was added, removed, weakened or reworded.
The four, by section:

  1. Environment, daemon start line: the absolute motif path before
     `\vendor\fl_studio_mcp_src` became `<motif unit root>`.
  2. Live-job status, the status-log path: the absolute motif path became
     "the motif unit's `out/job_status.log`".
  3. Process, the commit rule: "<motif repo> is a git repo. Do not commit"
     became "Your unit's repo is a git repo. Do not commit". This
     generalizes exactly - every unit is a git repo and no troop commits in
     any of them.
  4. Destructive cleanup, the 2026-08-18 incident narrative: "the entire
     working tree of <motif repo>" became "the entire working tree of the
     motif unit". The FACT is unchanged; only the address form is.

Resolve "<motif unit root>" through `units.json` in this repo - the one
registry, and the one file in the fleet allowed to carry another unit's
absolute path.

SCOPE, stated once so a troop in a non-Motif unit is not misled: the
Environment and Measurement-rules sections are FL-Studio/audio-specific and
bind the motif unit's measurement jobs; the interpreter line names motif's
conda env, and other units use their own (doctrine: plain `python`, stdlib
only, offline). Every other section - killing a process you started, state
fingerprinting, briefing premises citing their command, destructive cleanup
sandboxed by construction, one-file-one-writer, harness generations, and the
OPERATION AUDIT rules - is stack-wide and binds every unit. Those
motif-specific assumptions were LISTED, not rewritten, per JOB-05
deliverable 1: they are rules other units already work around, and silently
generalizing them was explicitly out of scope.
-->

# Common regs — read before any measurement job

Applies to every JOB-xx briefing in this folder. Violating these has cost
whole sessions before; they are all measured facts, not style.

## Environment
- Interpreter: `C:\Users\twood\.conda\envs\motif312\python.exe`. Never the
  WindowsApps `python` (MSIX-virtualised; pip writes invisible).
- Self-test convention: modules run their own tests as `__main__`, no pytest.
  Synthetic-signal tests must pass with NO audio device and NO FL running.
- The FL bridge is optional at every layer. New code must degrade cleanly
  (clear error dict, never a crash) when FL/daemon/loopback is absent.
- Daemon is a separate process: `cd "<motif unit root>\vendor\fl_studio_mcp_src"`
  then `<python> -m fl_studio_mcp.daemon`. FL must be on FL ASIO or WASAPI
  loopback hears silence.
- MCP_Apply must be armed BY HAND once per FL session or note writes no-op.
  Never automate FL's menus — synthetic clicks crash the engine.
- No new dependencies without a stated reason. Audio stack is
  pyaudiowpatch + numpy, already bundled.

## Measurement rules (learned the hard way — HANDOFF.md §2)
- Rewind before every capture; consecutive reads otherwise cover different
  slices of the loop.
- Capture the FULL loop: 8 bars at 140 BPM = 13.7 s. A 2 s window misses 85%.
- Settle before reading any live value (meters, displays, params).
- Use percentiles, not running max — one glitch sample poisons a max.
- A read of exactly 0.0 with no corroboration = phantom; ignore, never act.
- Run `soundstage.name_cohesion` before trusting any insert name.
- Restore all state you touch (solo/mute/route levels/params) — snapshot
  first, restore after, even on the error path.
- Keep a known-good signal to test analysis against; an analysis that calls
  the known-good result noise is wrong however principled.
- **Parameter-exact is not patch-exact** (JOB-10, Harmor): a 0-off snapshot
  rewrite can sound nothing like the entry patch — the preset POINTER
  carries non-exposed state. Any job that navigates presets must walk the
  pointer home and verify restore BY AUDIO, not by parameter table.
- **Preset stepping is a non-reversing WRITE** (JOB-08): full snapshot +
  pointer-walk restore around any preset navigation.
- **The mixer's built-in EQ speaks NORMALIZED, not dB/Hz** (learned
  2026-08-01, the sitting): `mixer_eq_set(gain=, freq=)` takes 0..1
  (gain 0.5 = flat, span ±18 dB); passing real units (-3.0 dB, 63 Hz)
  clamps to full-cut-at-max-frequency garbage. Go through
  `mixops.apply_track_eq` / `_teq_db_to_norm` — never raw values. Third
  member of the units family (vol normalized vs dB; instant peaks vs
  watch) — before writing ANY mixer value, name its unit out loud.
- **The bridge cannot SAVE the project** (learned 2026-07-31, JOB-43's
  patterns): printed patterns persist only if the Commander saves — no
  save API, and menu automation is banned. Any live-FL job that changes
  project state (patterns, names, markers) ends its report with an
  explicit SAVE THE PROJECT line for the Commander, and Control repeats
  it to him directly — JOB-43's entire A/B deliverable survived only in
  an "untitled" autosave because nobody said "save."
- **Self-test fixtures must speak FL's dialect**: live param names truncate
  at 30 chars — any name-matching test that only uses hand-written full
  names will pass while the live path fails (JOB-10 QA finding).

## Live-job status (added 2026-07-28 — a healthy troop got killed as "locked up")

Any job holding FL/the daemon MUST append a one-line timestamped status to
the motif unit's `out/job_status.log` at every phase change and at least
every 90 seconds during long sweeps/captures:
`[HH:MM:SS] JOB-NN phase: what is happening now, expected duration`.
The user watches this file to distinguish "working" from "hung". Silence
longer than ~2 minutes in that file while holding FL is a defect.

## Killing a process you started (added 2026-08-02 — a QA pass nearly took the fleet down)

**NEVER `Stop-Process -Name python` (or `-Name cmd`, or any bare name).** It
kills EVERY process with that image name on the box, not the one you started.
Standing on helios right now, under a python image name: the **Eagle Eye
agent** (the Commander's remote monitor), **motif-live-sync** (the Mac
mirror), the Commander's **live panel**, and the **FL Studio MCP daemon**.
A name-filtered kill is a fleet outage wearing a cleanup command's costume.

Credited to SrA Morgan's j96 verification pass, which ran exactly that command
while verifying the edition gate. **Nothing died — and that is the point:** the
three daemons happened to run under `pythonw.exe` while the filter matched
`python.exe`. A naming coincidence was the only thing between that command and
the Commander's monitor. Luck is not a guardrail, so it is now a reg.

**The rule:** record the PID of anything you start, then kill BY PID, and
verify the PID is yours before firing — read its `CommandLine` back and match
it against what you launched (`--no-scan`, your port, your script path). If the
identity check fails, REFUSE and report; do not widen the filter to make it
work.

```powershell
# right: verify identity, then kill exactly that pid
$p = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
if ($p.CommandLine -like '*gui/server.py*' -and $p.CommandLine -like '*--no-scan*') {
    Stop-Process -Id $id -Force
} else { "PID $id is not mine -- refusing" }
```

**Then prove the fleet survived**: list the surviving processes and confirm the
long-lived ones are still up. A cleanup step that cannot show what it spared
has not finished. And a job that leaves its throwaways RUNNING has also not
finished — stop them and confirm the ports are free (same j96 pass left two
panels listening and handed cleanup to the user; the briefing said otherwise).

## "Verified at HEAD" is meaningless for anything data-dependent (added 2026-08-02)

Git records the code and records the STATE not at all. `styles/refs/`,
`history/`, `out/`, `bindings.json`, `config.json`, `backends.json`,
`.scan_manifest.json`, the preset DBs and curve stores are all gitignored,
mutable, and load-bearing — and 12+ modules read them from inside their own
self-tests.

**What this cost, measured, in one day.** The suite ran GREEN (exit 0, 56 ok)
before 15:45. Control bounced the panel at 15:56 to recover the FL bridge;
`scan.py` runs on panel boot and rewrote the reference library at 16:00. The
suite ran RED after 16:00 with **no code change** — `patchdesign`'s j67
counterfactual arm measured 0 residual collisions where it asserts ≥1. Both
readings were correct. The assertion did not break; **its fixture moved.** A
troop then "verified pre-existing at HEAD" by checking out the touched SOURCE
files — a method that structurally cannot see this, because none of that state
is tracked. Roughly three job-equivalents went into chasing one undocumented
side effect.

**The rules:**

- **Fingerprint before and after any verdict you intend to quote.**
  `python tools/statecheck.py` prints the mutable surface; `--save` then
  `--diff` turns "it was green earlier" from a memory into a measurement.
  Paste the relevant lines into any report or briefing making a
  data-dependent claim.
- **"Pre-existing" must name the state it is pre-existing AGAINST.** A
  source-file checkout proves nothing about a test that draws from the
  library. Say "pre-existing at `<git sha>` + `<statecheck fingerprint>`" or
  do not say it.
- **Reading state is not depending on it — the verdict must FLIP.** Flag a
  test as rig-dependent only when you have shown its result change by varying
  state alone. A test that writes a temp file under `out/` is not
  rig-dependent.
- **Worse than rig-dependent: SELF-PERTURBING.** If drawing from a store also
  writes to it (a cache written as a side effect of being read), two
  consecutive runs are not measuring the same inputs and running it twice
  cannot establish anything.
- **`scan.py` runs on PANEL BOOT.** Starting the panel rewrites the reference
  library. Use `--no-scan` for any throwaway instance, and never start the
  panel during a measurement job.
- **Restore exactly, and prove it by hash.** You are operating on the
  Commander's live library.

## Briefing premises cite the command that produced them (added 2026-08-02)

Control's rule, written after three briefings in one day carried a claim the
troop then had to refute. **Every factual premise in a briefing carries the
command that produced it, run at CUT TIME — not recalled from memory, the
turnover, or a memory file.** If Control cannot produce the command, the claim
is written as a QUESTION, not a defect (which the audit reg already required;
this is how it is enforced).

Measured on the same day: j81's four premises were each taken live at cut time
and all four survived contact. j96's and j99's came from prose and both were
wrong — j96's whole defect did not exist, and j99's structural claim was seven
days stale and refuted in the first hour.

Troops: a briefing premise with no command behind it is a hypothesis. Treat it
as one, say so, and proceed against reality.

## Process
- Log the shipped result in `CHANGELOG.md` (dated, prose, with the
  measurement behind every claim). No narration in chat.
- Your unit's repo is a git repo. Do not commit — leave changes for review.
- If a claim in this briefing contradicts the code you find, trust the code,
  say so in your report, and proceed against reality.

## A completion needs a verdict (added 2026-09-01 — ST04, JOB-09 VERDICT REQUIRED)

ST04 DOUBLE TIME measured the QA desk's real failure mode: not slow, but
**skipped under load**. The desk issued 25 same-day verdicts on the
30-job peak day, yet 6 of that day's 32 jobs fell off the ledger before
QA ever saw them, and 13 jobs fleet-wide (~10%) carry no recorded
verification at all. Doubling delivery on that same tracking mechanically
doubles the unverified count — the precondition the Commander ordered
closed (B) before delivery volume rises (A).

**The rule: an INDEX row may not assert completion (`done`, `closed`,
`complete`) without a verification token in the same row** — `QA PASS`,
`QA ACCEPT`, or `QA ACCEPT-WITH-NOTES`. No token, no closed state — a row
that reads "done" and nothing else is not done, it is untracked.

**A job that genuinely cannot be independently verified closes as
`UNVERIFIED-CLOSED (<reason>)`.** This is a legitimate, honest state, not
a failure — the reason is what makes it honest, and its absence is
itself a violation (a bare `UNVERIFIED-CLOSED` is an escape hatch, not a
disposition). Use it when the verifying party, resource, or window is
gone, not as a substitute for asking the desk.

**The control that fires:** `tools/qa_check.py` reads a unit's
`briefings/INDEX.md`, flags every completion with no token, and exits 1
on any. Read-only, stdlib, no unit knowledge — the same house pattern as
`unitcheck.py` and `jobid_check.py`. It is 9 days younger than the
ledger it was measured against and has not yet been load-tested at 2×
delivery; treat a clean run as "nothing missed today," not as a
guarantee the mechanism holds at double the volume.

## Harness generations stay out of the record (added 2026-07-31)

A verification/measurement run that generates takes on the live tree
must NOT write to `history/entries/` (or must move its entries to an
isolated path before exiting). The taste, praised and recent-names
systems all read history as the Commander's own record — a test take
in there is a lie to every one of them, and it once had the Commander
asking whether someone else was using his app. Use isolated copies
(the JOB-34/37 method) or clean up after yourself, and say in the
report which you did.

## Two rules from OPERATION AUDIT (adopted 2026-07-31)

1. **Hypotheses are questions, not defects.** A briefing whose Mission
   rests on an inference (a smell, a suspicion, a reconstructed window)
   is cut as RECON asking the question — never as a defect claim. All
   three framing failures in the audit's 15-job sample were inferences
   stated as fact; every measured-artifact framing survived blind
   review. Refusing such a premise with evidence remains a pass.
2. **Self-tests pin their defect.** A code job's self-test must fail if
   the briefed defect is left unfixed — an assertion on the defect
   itself, not just the new code's happy path. Control's QA may spot-
   check this by inspection (the audit found 1 of 10 decoupled).

## Two more, from the outside consultant's review (adopted 2026-07-31)

3. **A control exists only if it fires.** Before asserting a mechanism
   exists (an exploration budget, a kill switch, a gate), show it FIRING
   — scaffolding with a place to fire from is not a control. The audit
   found "exploration controls" we believed in at ratio 0:3 against the
   praise pool; the belief was scaffolding.
4. **Reconstructed records say so.** A ledger row backfilled after the
   fact carries an explicit [reconstructed] flag distinguishing it from
   rows logged live — otherwise a future audit inherits the same gap one
   layer down, unable to know which entries to trust.

## Destructive cleanup is sandboxed by construction (adopted 2026-08-18, written in blood)

On 2026-08-18 a troop's own self-test deleted the entire working tree of
the motif unit. Not an attack, not a rogue agent: one line of cleanup
code in `tools/sitting_kit.py`.

```python
shutil.rmtree(Path(r.get("lease", {}).get("snapshot", "x")).parent.parent,
              ignore_errors=True)
```

Its runs were mocked, so they carried no lease snapshot. The `.get(...,"x")`
fallback made `Path("x")`, whose `.parent` is `.` — the CWD, which was the
repo root. The line became `shutil.rmtree(".")`, executed once per run in a
loop, and `ignore_errors=True` meant it never raised, never logged, and
never failed the test while it worked through the tree. Roughly 573 take
records and the praise log were lost permanently; everything else survived
only because GitHub held the commits and the Mac mirror is scp-only and
never propagates deletions.

The rules, binding on every troop and on Control's briefings:

1. **A delete target must be absolute and provably under a temp root.**
   Resolve it, then assert `str(target.resolve()).startswith(str(tmp_root
   .resolve()))` before the call. A path derived from a `.get()` fallback,
   a default sentinel, or any relative string is not a delete target.
2. **Never `rmtree(..., ignore_errors=True)` on a path you have not just
   proven is a temp dir.** That flag is what turned a bug into an
   unlogged, unstoppable wipe. Prefer `TemporaryDirectory()` as a context
   manager and delete nothing by hand.
3. **Self-tests run with a scratch CWD**, never the repo root. A cleanup
   bug must be able to hit nothing but an empty sandbox.
4. **Control's duty:** every briefing whose deliverable writes, snapshots,
   or cleans up carries this trap by name. The traps in that day's
   briefings covered live-FL safety, territory, and determinism — every
   hazard already seen — and omitted the one that actually fired. Traps
   are not only a memory of past incidents; they are a survey of what the
   deliverable can reach.
5. **Before a troop's self-test is accepted, grep its diff for `rmtree`,
   `unlink`, `remove`, `rmdir`.** Any hit is read line by line.

### Amendment, 2026-08-21 (j150): the first guard was not enough

Control's initial fix used `str(target).startswith(str(gettempdir()))` over
an already-`.resolve()`d path. West proved that hole live: `.resolve()`
anchors a RELATIVE path to the CWD, and rule 3 puts the scratch CWD *under*
`%TEMP%` -- so the literal 2026-08-18 expression passed the guard and
resolved to the user's whole `AppData\Local`. Latent only because no
relative path happened to reach it.

The pattern that holds, and the one to copy (`_rm_scratch` in
tools/sitting_kit.py) -- three independent gates, in this order:

1. **Refuse a non-absolute path BEFORE calling `.resolve()`.** Resolution is
   what turns a relative string into a real target; check first.
2. **Containment, not string prefix:** `tmp_root in rp.parents` AND
   `rp != tmp_root`. A prefix test is defeated by CWD anchoring; a parents
   test is not, and the inequality stops anyone deleting the temp root.
3. **Registry membership:** the path must be one THIS process created via
   its own `mkdtemp` wrapper. Nothing else is ever a delete target.

Plus: never `ignore_errors=True` (failures are logged and returned), and a
self-test that fires every refusal path on every run, showing the victim
still standing afterwards.

Corollary for Control: a guard is not landed until a test proves the
original defect is refused. Control's first guard shipped without one.

## One file, one writer — and never a whole-file write (added 2026-08-21)

j153 and j154 were dispatched into `offline_gen.py` at the same time, in
different regions, each told to stay in its own. At 12:08:33 one of them
wrote the file WHOLE from a copy it had read before the other landed, and
15 of the other's edits vanished. It was caught only because the affected
troop happened to run `--help` afterwards; nothing failed, nothing warned.

Control's error first: two jobs in one file is a dispatch decision, and
"stay in your region" is not a mechanism. The rules:

1. **Control does not dispatch two concurrent jobs into the same file.**
   Different regions of one file is still the same file. Queue the second.
2. **Troops never write a source file whole.** Every change is a targeted
   edit against the content as it is on disk at that moment. An in-memory
   copy taken minutes ago is stale by definition when siblings are live.
3. **Re-read immediately before writing** anything a sibling could have
   touched, and grep for the sibling's markers afterwards.
4. If you discover you clobbered someone, **say so plainly and re-apply
   their work on top of yours** — do not restore a backup that silently
   drops your own, and do not stay quiet because the tests pass. West
   re-applied four hunks surgically and reported it; that is the standard.

## Harness generations leave BOTH the file and the index row (added 2026-08-21)

The existing reg says harness generations stay out of the Commander's
record. Troops have been honouring half of it: deleting the entry file and
leaving the index row. On 2026-08-21 that produced **16 phantom rows** in
`history/index.json` from a single afternoon of concurrent jobs — the exact
corruption class `tests/history_concurrency.py` exists to assert against,
introduced by the cleanup meant to protect the record.

1. **Remove from both, or from neither.** An entry file without its index
   row is corruption, not tidiness.
2. **Prefer never creating one.** Stub `history.record` in a harness rather
   than recording and deleting; j154 did this after its first bare run and
   it is the cheaper discipline.
3. **Prove it before you report done:** `python tools/ledger_audit.py
   --report` must say VERDICT: HEALTHY. It is read-only, takes seconds, and
   names phantom rows explicitly.
4. If you find phantom rows you did not create, report them — do not mend
   another job's mess mid-flight, and do not stay silent because your own
   rows are clean. j154 reported 13 it had not made; that is the standard.

## Sealed voice corpus: owner's eyes only (folded in 2026-10-22 from the motif unit's j181 SEALED ROOM, added there 2026-09-06)

A voice corpus and every draft derived from it are the Commander's eyes
only. Control reads metadata (paths, sizes, counts, summary lines); troops
read nothing there; no agent copies, greps, cats, or quotes a corpus file.
In the motif unit, `tools/corpus_guard.py` is the control that fires; any
unit that holds such a corpus names its own guard before a troop works near
it. Folded into the shared regs on the Commander's word (My Desk,
2026-10-22: "Fold Motif's edits into the shared copy"); it was the motif
copy's only rule not already in this master.
<!-- END COMMON REGS -->
