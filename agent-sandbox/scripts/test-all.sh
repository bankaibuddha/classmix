#!/usr/bin/env bash
# Run every check this unit has. Nothing here needs a network or a model.
#
# The containment proofs need docker and the built image, so they are run
# last and their absence is reported as SKIPPED rather than passed. A skipped
# containment proof is not a green containment proof, and this script says so
# out loud rather than exiting 0 and letting you assume otherwise.

set -uo pipefail

UNIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$UNIT_ROOT"
FAILED=()

suite() {
  local label="$1"; shift
  printf '\n=== %s ===\n' "$label"
  if "$@" >/tmp/suite-out.txt 2>&1; then
    tail -3 /tmp/suite-out.txt
  else
    tail -30 /tmp/suite-out.txt
    FAILED+=("$label")
  fi
}

suite "delete guard (harness/scratch.py)"        python3 harness/scratch.py
suite "broker (broker/broker.py)"                python3 broker/broker.py --self-test
suite "agent loop (agent/agent.py)"              python3 agent/agent.py --self-test
suite "orchestrator (harness/run.py)"            python3 harness/run.py --self-test
suite "loop end-to-end (tests/test_loop_integration.py)" \
      python3 tests/test_loop_integration.py

printf '\n=== containment proofs (need docker) ===\n'
CONTAINMENT="unknown"
if ! command -v docker >/dev/null 2>&1; then
  CONTAINMENT="SKIPPED: docker not installed"
elif ! docker info >/dev/null 2>&1; then
  CONTAINMENT="SKIPPED: docker daemon not reachable"
elif ! docker image inspect "${IMAGE:-agent-sandbox:v1}" >/dev/null 2>&1; then
  CONTAINMENT="SKIPPED: image not built (run scripts/build.sh)"
else
  if tests/test_containment.sh; then
    CONTAINMENT="PASSED"
  else
    CONTAINMENT="FAILED"
    FAILED+=("containment proofs")
  fi
fi
echo "$CONTAINMENT"

printf '\n========================================\n'
if [[ ${#FAILED[@]} -gt 0 ]]; then
  printf 'FAILED: %s\n' "${FAILED[*]}"
  exit 1
fi
echo "all unit and integration suites passed"
if [[ "$CONTAINMENT" != "PASSED" ]]; then
  echo
  echo "BUT containment is UNPROVEN on this box (${CONTAINMENT})."
  echo "Do not run the agent until tests/test_containment.sh passes here."
  exit 3
fi
echo "containment proven. Safe to run the agent."
