#!/usr/bin/env bash
# The app's SSH tunnel against a real sshd (step 15, docs/ARCHITECTURE.md, Several machines): Atlas's
# image with sshd beside it (sshd.Dockerfile), a key made for the run, and the app's own
# `SshTunnel` and `RemoteMachine` driven by electron/ssh-tunnel.sshd.test.ts through the system's
# `ssh`: Atlas there reached with its token, and a site up there opened for the preview.
#
#   ATLAS_TEST_IMAGE=atlas:ci tests/docker/ssh-tunnel.sh      (from the repository's root)
#
# The test hands `ssh` a config file of its own (the run's key and known hosts) with `-F`; in use,
# the user's ~/.ssh/config and agent apply.
set -euo pipefail
# Git Bash on Windows would rewrite the container's paths in `docker exec`.
export MSYS_NO_PATHCONV=1

image="${ATLAS_TEST_IMAGE:-atlas:local}"
name="atlas-sshd-$$"
work="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

docker build -q -t atlas-sshd:test --build-arg BASE="$image" -f tests/docker/sshd.Dockerfile tests/docker >/dev/null
ssh-keygen -q -t ed25519 -N '' -C atlas-test -f "$work/key"
token="$(head -c 24 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
docker run -d --name "$name" -p 127.0.0.1::2222 \
  -e AUTHORIZED_KEY="$(cat "$work/key.pub")" -e ATLAS_SERVER_TOKEN="$token" atlas-sshd:test >/dev/null
port="$(docker port "$name" 2222/tcp | head -n 1 | sed 's/.*://')"

# Windows' own ssh reads Windows paths (this runs in Git Bash there too).
native() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
chmod 600 "$work/key"
# Windows' ssh takes a key only its owner can read, which a mode does not say there.
if command -v icacls >/dev/null; then
  icacls "$(native "$work/key")" /inheritance:r /grant:r "${USERNAME}:F" >/dev/null
fi
cat > "$work/config" <<EOF
Host *
  IdentityFile $(native "$work/key")
  IdentitiesOnly yes
  UserKnownHostsFile $(native "$work/known_hosts")
EOF

ready=""
for _ in $(seq 90); do
  if docker exec "$name" /app/.venv/bin/python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18790/health')" 2>/dev/null; then
    ready=yes
    break
  fi
  sleep 1
done
if [ -z "$ready" ]; then
  docker logs "$name"
  echo "Atlas did not start in the SSH machine." >&2
  exit 1
fi

cd apps/desktop
ATLAS_TEST_SSHD="ssh://atlas@127.0.0.1:$port" \
  ATLAS_TEST_SSH_CONFIG="$(native "$work/config")" \
  ATLAS_TEST_SSHD_TOKEN="$token" \
  npx vitest run --project electron electron/ssh-tunnel.sshd.test.ts
