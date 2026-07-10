#!/usr/bin/env bash
# Prove the untrusted-bot sandbox is REAL on this host before going public.
# Run as the 'starship' user, from the app checkout, with the same env the
# services use:
#
#   sudo -u starship env $(grep -v '^#' /etc/starship/starship.env | xargs) \
#        deploy/validate-sandbox.sh
#
# (or just `STARSHIP_SANDBOX=docker DOCKER_HOST=... deploy/validate-sandbox.sh`)
#
# It exits non-zero on the FIRST failure, so a green run means every check passed.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
export STARSHIP_SANDBOX=docker

echo "== 1/5  docker daemon reachable =="
docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}'

echo "== 2/5  sandbox reports enabled =="
"$PY" -m starship_duel.arena.sandbox status
"$PY" - <<'EOF'
from starship_duel.arena.sandbox import SandboxSpec
assert SandboxSpec.from_env().enabled, "sandbox not enabled — check STARSHIP_SANDBOX/docker"
print("enabled=True OK")
EOF

echo "== 3/5  ensure the sandbox image is present =="
# --if-missing builds it if possible, else accepts an image that was preloaded
# (docker pull / docker load). Some kernels forbid networked rootless containers,
# so `docker build` can't run there; see deploy/DEPLOY.md.
if ! "$PY" -m starship_duel.arena.sandbox build --if-missing; then
    echo "FAIL: sandbox image is missing and could not be built on this host."
    echo "      Preload it:  docker pull <registry-ref> && docker tag <ref> starship-arena-sandbox"
    echo "             (or)  docker load -i starship-arena-sandbox.tar.gz"
    exit 1
fi
"$PY" -m starship_duel.arena.sandbox status

echo "== 4/5  fail-closed when docker is hidden =="
"$PY" - <<'EOF'
import shutil
from starship_duel.arena import sandbox
# Force docker_available() to report missing, then require isolation.
sandbox._DOCKER_OK = False
try:
    sandbox.SandboxSpec(mode="docker").require_docker()
    raise SystemExit("FAIL: did not fail closed with docker missing")
except sandbox.SandboxError:
    print("fail-closed OK (refuses to run untrusted code raw)")
EOF

echo "== 5/5  end-to-end: a bot actually runs inside a container =="
"$PY" - <<'EOF'
from starship_duel.tournament.accounts import smoke_test
# A trivial well-behaved bot (always takes the first legal action): this
# exercises the full docker run path end to end.
bot = b'from starship_sdk import run\nrun(lambda req: {"index": 0})\n'
ok, msg = smoke_test(bot, "probe.py")
print("smoke_test:", ok, msg)
assert ok, "sandboxed smoke test failed — the container path is broken"
EOF

echo
echo "ALL SANDBOX CHECKS PASSED — isolation is live."
