#!/usr/bin/env bash
# Live containment proofs. Requires docker and the built image.
#
# The unit tests assert the FLAGS are present. This script proves the flags
# have the EFFECT they are supposed to have, by running the real container
# and watching each capability actually be denied. Reg, from the outside
# consultant's review: a control exists only if it fires. Scaffolding with a
# place to fire from is not a control, so every check below tries the thing
# and requires it to fail.
#
# Usage: tests/test_containment.sh

set -uo pipefail

UNIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-agent-sandbox:v1}"
PASS=0
FAIL=0

# Same flags as harness/run.py container_command(), minus the socket mount.
# The /opt/agent mount is included on purpose: without it, the "cannot write
# to its own loop" check below would pass because the path does not exist,
# which proves nothing about whether a read-only mount holds.
SANDBOX=(
  docker run --rm
  --network none
  --user 1000:1000
  --cap-drop ALL
  --security-opt no-new-privileges
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=64m
  -v "${UNIT_ROOT}/agent:/opt/agent:ro"
  -e "HOST_HOME=${HOME:-/root}"
  -e "HOST_UNIT_ROOT=${UNIT_ROOT}"
  --pids-limit 128
  --memory 1g
  --pid private
  --ipc private
)

ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n     -> %s\n' "$1" "${2:-}"; }

# Expect the command inside the sandbox to FAIL (non-zero). That failure is
# the control firing.
denied() {
  local label="$1"; shift
  local output
  if output=$("${SANDBOX[@]}" "$IMAGE" "$@" 2>&1); then
    bad "$label" "command SUCCEEDED but should have been denied: ${output:0:200}"
  else
    ok "$label"
  fi
}

# Expect the command to succeed. Proves the sandbox is still usable, not
# merely locked down: a sandbox where nothing works proves nothing.
allowed() {
  local label="$1"; shift
  local output
  if output=$("${SANDBOX[@]}" "$IMAGE" "$@" 2>&1); then
    ok "$label"
  else
    bad "$label" "should have worked: ${output:0:200}"
  fi
}

echo "containment proofs for ${IMAGE}"
echo

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "image ${IMAGE} not built. Run scripts/build.sh first." >&2
  exit 2
fi

echo "network:"
# No IP stack at all. socket() on AF_INET must fail, not merely time out.
denied "cannot open an outbound TCP socket" \
  python3 -c 'import socket; socket.create_connection(("1.1.1.1",443),timeout=5)'
denied "cannot resolve DNS" \
  python3 -c 'import socket; socket.gethostbyname("pypi.org")'
denied "cannot reach the host model port" \
  python3 -c 'import socket; socket.create_connection(("127.0.0.1",11434),timeout=5)'
denied "pip cannot install anything" \
  python3 -m pip install --no-input requests

echo
echo "network tooling absent from the image:"
for tool in curl wget nc ncat telnet ssh scp rsync ftp; do
  denied "no ${tool} binary" python3 -c \
    "import shutil,sys; sys.exit(0 if shutil.which('${tool}') else 1)"
done

echo
echo "privilege:"
denied "not running as root" \
  python3 -c 'import os,sys; sys.exit(0 if os.getuid()==0 else 1)'
denied "cannot write to the root filesystem" \
  python3 -c 'open("/escape","w").write("x")'
denied "cannot write to /usr" \
  python3 -c 'open("/usr/bin/evil","w").write("x")'
# The mount must exist for this to mean anything, so check that first.
if ! "${SANDBOX[@]}" "$IMAGE" python3 -c 'open("/opt/agent/agent.py").read()' >/dev/null 2>&1; then
  bad "loop is mounted at /opt/agent" "mount missing; the tamper check below would be vacuous"
else
  ok "loop is mounted at /opt/agent"
fi
denied "cannot write to its own loop directory" \
  python3 -c 'open("/opt/agent/agent.py","a").write("# tampered")'
denied "cannot chown (CAP_CHOWN dropped)" \
  python3 -c 'import os; os.chown("/tmp",0,0)'
denied "no docker socket to escalate through" \
  python3 -c 'import os,sys; sys.exit(0 if os.path.exists("/var/run/docker.sock") else 1)'
denied "cannot execute from the noexec tmpfs" \
  python3 -c 'import os,subprocess; open("/tmp/x.sh","w").write("#!/bin/sh\necho hi\n"); os.chmod("/tmp/x.sh",0o755); subprocess.run(["/tmp/x.sh"],check=True)'

echo
echo "host isolation:"
denied "cannot see host processes" \
  python3 -c 'import os,sys; pids=[p for p in os.listdir("/proc") if p.isdigit()]; sys.exit(0 if len(pids)>20 else 1)'
# Hardcoding a host path would make this check pass by accident on any box
# whose layout differs. Pass the REAL paths in and assert they are not
# reachable: the unit root is genuinely on this host, and /opt/agent is
# mounted FROM under it, so if the host path itself were visible that would
# be a real leak rather than a guess that happened to miss.
denied "host home is not reachable" \
  python3 -c 'import os,sys; sys.exit(0 if os.path.exists(os.environ["HOST_HOME"]) else 1)'
denied "host unit root is not reachable" \
  python3 -c 'import os,sys; sys.exit(0 if os.path.exists(os.environ["HOST_UNIT_ROOT"]) else 1)'
denied "no conventional host mount points" \
  python3 -c 'import os,sys; sys.exit(0 if any(os.path.exists(p) for p in ("/host","/hostfs","/mnt/host")) else 1)'

echo
echo "still usable (a sandbox where nothing works proves nothing):"
allowed "python runs"       python3 -c 'print(1+1)'
allowed "git is available"  git --version
allowed "/tmp is writable"  python3 -c 'open("/tmp/ok","w").write("x")'
# The real check: the loop's own self-tests pass INSIDE the sandbox, under
# every flag above. This is what proves the lockdown did not break the agent.
allowed "agent self-tests pass in the sandbox" python3 /opt/agent/agent.py --self-test

echo
echo "----------------------------------------"
echo "  ${PASS} passed, ${FAIL} failed"
if [[ $FAIL -gt 0 ]]; then
  echo "  CONTAINMENT NOT PROVEN. Do not run the agent."
  exit 1
fi
echo "  containment proven for ${IMAGE}"
