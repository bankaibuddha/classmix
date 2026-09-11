# Sandbox image for the self-directed agent.
#
# Built once, deliberately, on a box with network access. At RUN time the
# container has --network none, so this build is the only networked step in
# the whole system. That is why it is a separate, reviewable file and why
# scripts/build.sh never pipes a download into a shell.
#
# Base is pinned by tag AND digest. A tag alone is a moving target: the
# image you reviewed and the image you run are not the same artifact if
# upstream repoints the tag. Refresh the digest deliberately with
# scripts/build.sh --refresh-digest, never silently.
#
# Digest below is the OCI image index, resolved 2026-09-11 against
# registry-1.docker.io and verified to carry linux/arm64v8 (celeste, GX10)
# alongside linux/amd64 (Mac via its Linux VM, and the 4080 box via WSL2),
# so the same line builds on all three:
#
#   docker-content-digest: sha256:782412e8...2254
#   platforms: linux/386, linux/amd64, linux/arm64v8, linux/armv7, linux/ppc64le

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

# git is load-bearing: the agent's git history IS the reviewable output.
# Everything else here is a reading tool. No compilers, no package managers
# beyond what the base ships, nothing that speaks a network protocol.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git=1:2.39.* \
        ripgrep \
    && apt-get purge -y --auto-remove \
    && rm -rf /var/lib/apt/lists/*

# Make the "no network tools" claim true rather than assumed. The base image
# ships none of these today; this line means a future base change cannot
# quietly reintroduce one. tests/test_containment.sh verifies the result
# from outside, because a claim needs the command behind it.
RUN for tool in curl wget nc netcat ncat telnet ssh scp sftp rsync ftp; do \
        find / -xdev -name "$tool" -type f -delete 2>/dev/null || true; \
    done

# Non-root by default. The run script additionally pins --user to the host
# uid so files written in /work are owned by the operator, not by root.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 1000 agent || true

# The root filesystem is mounted read-only at run time, so nothing may
# expect to write outside /work and the /tmp tmpfs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_TERMINAL_PROMPT=0

WORKDIR /work

# The loop itself is bind-mounted read-only at /opt/agent by the run script
# rather than baked in, so you can edit and re-run it without a rebuild and
# the agent still cannot modify it.
CMD ["python3", "/opt/agent/agent.py"]
