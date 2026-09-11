# agent-sandbox — unit INDEX

Unit stood up 2026-09-11 under the one-unit-per-repo doctrine. Extracted
from a branch of the ClassMix repo, where it was built for want of
anywhere else to put it, via `git subtree split --prefix=agent-sandbox`.
History preserved; ClassMix keeps none of it.

Mission: a self-directed local coding agent and a sandbox strong enough to
run it unattended. v1 is local-model-only and single-box on celeste (GX10).

Security doctrine: the container is the authentication boundary, because
the model is untrusted input. No IP stack in the sandbox (`--network none`),
one unix socket to a host-side broker that builds the entire upstream call
itself, no LAN exposure, and cross-box inference refused rather than
merely discouraged. Budgets enforced three times in three processes.

`briefings/REGS-COMMON.md` is NOT yet deployed here. It is the doctrine
master's file, copied byte-for-byte and hash-compared by
`verify-parity.sh`. Deploy it from the doctrine unit before the first
dispatch; do not hand-write a copy.

| JOB | title | category | rank | status | territory |
|-----|-------|----------|------|--------|-----------|
| — | (none dispatched) | | | | |
