# agent-sandbox

A self-directed local coding agent, and a sandbox strong enough to let it run
unattended.

The agent reads a charter, writes code, runs its own tests, commits each step,
and keeps going until it declares itself done or hits a budget. It runs behind
`--network none` with no IP stack, talks to exactly one unix socket, and cannot
reach the host, the network, your repositories, or anything you have not handed
it a disposable copy of.

Status: v1 is local-model-only and single-box, targeting celeste (GX10).

> **This is a staged unit, not ClassMix tooling.** It lives in this repo only
> so it could be built on the designated branch. Under the one-unit-per-repo
> doctrine it belongs in its own repo. Extract `agent-sandbox/` wholesale; it
> has no dependency on anything above it.

## The three questions

### 1. What can reach this?

**Nothing inbound.** There is no listener, no port, no HTTP service, so there
is no auth surface. Under `--network none` the container has no IP stack: no
DNS, no TCP, no loopback to your LAN.

**Outbound: one unix socket.** A host-side broker bridges it to the model
daemon on loopback. The agent supplies two strings and nothing else; the broker
builds the entire upstream request from its own configuration, so there is no
field that could carry a hostname, a model name, or a URL. `broker.py` refuses
unknown fields rather than ignoring them.

**Cross-box inference is refused by default.** Both the broker and the
orchestrator reject a non-loopback model endpoint, because Ollama ships without
authentication and a LAN-reachable inference endpoint is an unauthenticated
service on your home network. If you want celeste to serve the Mac, forward it
over SSH and point the harness at the local end of the tunnel.

**The honest boundary:** the model can reach it. Whatever the model emits
becomes commands inside the container. The model is untrusted input and the
container is the authentication boundary. There is no second one, and no
prompt-level instruction should be mistaken for one.

### 2. What can it do once it's there?

Writable: `/work` (a disposable copy) and a 256MB noexec tmpfs at `/tmp`.
That is the complete list.

Removed, each flag for a specific reason, all in `container_command()` in
`harness/run.py`:

| Flag | What it removes |
|---|---|
| `--network none` | Any ability to send a packet. Not a policy, an absent interface |
| `--user` non-root | Root inside the container, even when the harness runs as root |
| `--cap-drop ALL` | CAP_CHOWN, CAP_DAC_OVERRIDE, CAP_SETUID and the rest |
| `--security-opt no-new-privileges` | Escalation via a setuid binary |
| `--read-only` | Modifying the image, installing anything, persisting outside `/work` |
| `--tmpfs /tmp:noexec` | Executing a payload written to `/tmp` |
| no docker socket mounted | The classic container-to-host-root escape |
| `--pid/--ipc private` | Seeing, signalling, or sharing memory with host processes |
| `--cpus/--memory/--pids-limit` | Starving the box; fork bombs |
| `/opt/agent:ro` | Editing its own loop, budget, or tool allowlist |

**Blast radius if it turns hostile:** it destroys its own copy and burns
cycles to the cgroup ceiling. That is the whole list. The live worktree is
never mounted, the workspace has no git remote so a commit has nowhere to go,
and no credential, key, or config is reachable.

### 3. What happens if it's wrong or malicious?

**Wrong is the expected case.** An unattended model writes plausible code that
fails and then reports success. Three structural answers:

- **The harness records outcomes, the agent does not.** Real exit codes go in
  the journal. After the loop ends, the host re-runs the test suite in a
  *fresh* container against a *read-only* mount and records that exit code
  itself. The agent's "done" is filed as a claim next to a measurement that
  either backs it or does not, and `report.json` shows both.
- **Budgets are enforced three times, in three places.** The in-container loop
  counts iterations; the broker, in a different process outside the sandbox,
  counts requests, characters and wall clock; the orchestrator holds an
  authoritative host-side wall clock and stops the container. No single
  compromised component removes the stop.
- **Destructive cleanup is sandboxed by construction.** `harness/scratch.py`
  implements the three gates from the 2026-08-18 incident and its j150
  amendment: refuse non-absolute before resolving, containment by `parents`
  rather than string prefix, and registry membership. Never
  `ignore_errors=True`. Ten tests fire every refusal path and assert a
  bystander directory survives each one.

**Compromised dependency:** the image is built once from a Dockerfile pinning
the base by digest, not tag. That build is the only networked step in the
system. At runtime there is no network, so a malicious postinstall has neither
a fetch path nor an exfil path. `scripts/build.sh --check-digest` tells you
when upstream moves the tag; it deliberately does not edit the pin for you.

**Tampered payload:** model output never executes on the host. The only thing
crossing back out is a git branch in a directory you read. That review is the
single chokepoint and it stays human.

**What this does not protect against:** a kernel-level container escape. This
is Docker isolation, not a VM. On celeste that is one layer; on the Mac,
Docker's own Linux VM adds a second. If you ever point this at a model you
have reason to distrust rather than one that is merely unreliable, run it on a
box you would be willing to reinstall.

## Architecture

```
celeste (GX10), one box, nothing on the LAN
├── ollama serve                    127.0.0.1:11434, loopback only
├── harness/run.py                  host orchestrator, authoritative wall clock
│    └── broker/broker.py           binds runs/<ts>/scratch/sock-*/model.sock (0600)
│         caps requests + chars + seconds, refuses unknown fields
└── docker run --network none       NO IP STACK
     ├── /opt/agent          ro     the loop, unmodifiable from inside
     ├── /work               rw     disposable copy, no git remote
     ├── /tmp               tmpfs   noexec, nosuid, vanishes with container
     └── /run/model.sock     rw     the only egress that exists
```

## Running it

```bash
# 1. Build the image. The only networked step, kept separate on purpose.
#    Nothing in this unit ever pipes a download into a shell.
scripts/build.sh

# 2. Prove containment on THIS box. Every check tries the forbidden thing and
#    requires it to be denied, because a control exists only if it fires.
tests/test_containment.sh

# 3. Pull a coding model, loopback only.
ollama pull qwen2.5-coder:32b

# 4. Run it.
python3 harness/run.py \
  --charter CHARTER.example.md \
  --model qwen2.5-coder:32b \
  --iterations 40 \
  --minutes 90
```

Everything lands in `runs/<timestamp>/`: the `workspace` with the agent's git
history, `broker.jsonl` with every model call, and `report.json` with what
measurably happened. Review with `git -C runs/<ts>/workspace log -p`.

To point it at a copy of an existing project, `--seed <dir>`. The directory is
**copied**, never mounted, and its `.git` is excluded so no remote comes with
it. There is no code path that hands a container a bind mount of a repo you
care about.

## Tests

```bash
scripts/test-all.sh
```

| Suite | Count | Needs docker | Proves |
|---|---|---|---|
| `harness/scratch.py` | 10 | no | every delete-guard refusal fires; bystander survives |
| `broker/broker.py` | 10 | no | caps fire; unknown fields and model overrides refused |
| `agent/agent.py` | 21 | no | parsing, path escapes, binary allowlist, honest commits |
| `harness/run.py` | 19 | no | every containment flag present; seed copied not mounted |
| `tests/test_loop_integration.py` | 7 | no | the real loop over a real socket, end to end |
| `tests/test_containment.sh` | ~30 | **yes** | each denial actually holds in a live container |

As of 2026-09-11 the 67 non-docker tests pass. **The containment proofs have
not been run**, because the machine this was written on has the docker CLI but
no daemon. They are the checks that matter most, so run them on celeste before
the first real run. `scripts/test-all.sh` exits 3 rather than 0 when they are
skipped, so an unproven sandbox cannot read as a green one.

Four defects were found by these tests during the build and fixed: a root
harness would have produced a root sandbox; a chowned workspace made git
refuse the harness's own reads; a git failure was silently reported as
"nothing to commit", which would have produced runs with empty histories and
no error anywhere; and a containment check that would have passed vacuously
because the path it tested did not exist.

## Honest limits

- **A local model will disappoint you on long-horizon work.** It will rewrite
  the same function several ways, break a passing test and declare victory,
  and lose track of what it already tried. The journal and the independent
  verification exist so you can see that happening rather than be told a story
  about it.
- **This cannot close a gate that needs hands on hardware.** Anything of the
  form "Add-to-Home-Screen on the iPad works" is not reachable from a sealed
  container. Expect a branch worth reviewing, not a feature ready to ship.
- **v1 is single-box.** Cross-box inference is refused, not unimplemented.
  Adding it means SSH tunnels and a reason, not an open port.

## v2, when v1 has actually run

- The 4080 laptop as a second, smaller model reviewing the main agent's diffs.
  An independent critic is a good role for a weaker model.
- The Mac Mini as always-on scheduler and journal host.
- Both over SSH tunnels. Never an open Ollama port.
