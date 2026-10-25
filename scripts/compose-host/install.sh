#!/usr/bin/env bash
# The host updater of a Compose host (docs/COMPOSE_HOST.md, docs/ARCHITECTURE.md, The update of a
# Compose host). Run it once with sudo from a clone of root's own, naming the checkout that holds
# the Compose file, and again to bring a newer host updater:
#
#   sudo /root/atlas-installer/scripts/compose-host/install.sh --checkout /path/to/the/checkout
#
# What it does, each step said as it runs:
#   1. copies host_updater.py, from this script's own tree, to /usr/local/lib/atlas-host-updater/,
#      root's;
#   2. migrates the Compose file (docker-compose.homelab.yml, else docker-compose.yml) into
#      /etc/atlas-updater/compose.yml, resolved by `docker compose config` so it stands alone, and
#      the Dockerfile.homelab beside it, when there is one, into /etc/atlas-updater/Dockerfile. The
#      originals stay where they are, untouched;
#   3. writes compose.updater.yml (the socket's folder and ATLAS_INSTALLATION=compose) and
#      config.toml, and records the commit that runs now;
#   4. checks it all with the host updater itself, and stops there on any problem;
#   5. installs and starts atlas-host-updater.service, with a tmpfiles.d entry that makes its
#      socket's folder at boot, before Docker starts the container that mounts it; and
#      /usr/local/bin/atlas, whose `atlas update` is the host updater's;
#   6. recreates the Atlas container once, on the same image, so it mounts the socket.
#
# It never runs git as root in the checkout, never deletes a file of it, and never mounts the
# Docker socket into the container. A migrated file already in /etc/atlas-updater is kept: edit it
# there, as root.
set -euo pipefail

# DESTDIR stages every file under another root, for the tests: nothing is started then.
DESTDIR=${DESTDIR:-}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The tree this script came with: the program root will run is copied from here, never from
# --checkout, which its user (and the agent, through them) writes.
SOURCE=$(cd "$HERE/../.." && pwd)
LIB=/usr/local/lib/atlas-host-updater
ETC=/etc/atlas-updater
STATE=/var/lib/atlas-updater
UNIT=/etc/systemd/system/atlas-host-updater.service
TMPFILES=/etc/tmpfiles.d/atlas-updater.conf
SHIM=/usr/local/bin/atlas
SHIM_MARK=atlas-compose-host-shim

checkout=$SOURCE
compose=""
dockerfile=""
project=atlas
service=atlas
image=atlas:local
volume=""
uid=10001
repository=https://github.com/marqueskaio/atlas.git
github=marqueskaio/atlas
branch=main
recreate=1

say() { printf '%s\n' "$*"; }
fail() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
sudo scripts/compose-host/install.sh [options]
  --checkout DIR      the checkout holding the Compose file (default: this script's)
  --compose FILE      the Compose file to migrate (default: docker-compose.homelab.yml, else
                      docker-compose.yml, in the checkout)
  --dockerfile FILE   the Dockerfile to migrate (default: Dockerfile.homelab when there is one;
                      without one each update builds with the commit's own Dockerfile)
  --project NAME      the Compose project (default: atlas)
  --service NAME      the service (default: atlas)
  --image NAME:TAG    the image the service runs (default: atlas:local)
  --volume NAME       the volume at /data (default: <project>_atlas-home)
  --uid N             the container's user id (default: 10001)
  --repository URL    where the updates come from (default: the Atlas repository on GitHub)
  --github OWNER/NAME whose GitHub Actions verify a commit (default: marqueskaio/atlas)
  --no-recreate       do not recreate the container now (it mounts the socket on its next start)
EOF
}

parse() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --checkout) checkout=$2; shift 2 ;;
            --compose) compose=$2; shift 2 ;;
            --dockerfile) dockerfile=$2; shift 2 ;;
            --project) project=$2; shift 2 ;;
            --service) service=$2; shift 2 ;;
            --image) image=$2; shift 2 ;;
            --volume) volume=$2; shift 2 ;;
            --uid) uid=$2; shift 2 ;;
            --repository) repository=$2; shift 2 ;;
            --github) github=$2; shift 2 ;;
            --no-recreate) recreate=0; shift ;;
            -h | --help) usage; exit 0 ;;
            *) usage >&2; fail "unknown option $1" ;;
        esac
    done
    volume=${volume:-${project}_atlas-home}
    if [ -z "$compose" ]; then
        if [ -f "$checkout/docker-compose.homelab.yml" ]; then
            compose=$checkout/docker-compose.homelab.yml
        else
            compose=$checkout/docker-compose.yml
        fi
    fi
    if [ -z "$dockerfile" ] && [ -f "$checkout/Dockerfile.homelab" ]; then
        dockerfile=$checkout/Dockerfile.homelab
    fi
    for name in "$project" "$service" "$volume"; do
        [[ "$name" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || fail "$name is not a Compose name."
    done
    [[ "$uid" =~ ^[0-9]+$ ]] || fail "--uid takes a number."
    # Written into root's TOML and YAML as they are: the host updater's own shapes, nothing more.
    [[ "$image" =~ ^[a-z0-9][a-z0-9_./-]*:[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] \
        || fail "$image is not an image's name:tag."
    [[ "$github" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$ ]] || fail "$github is not OWNER/NAME."
    [[ "$repository" =~ ^(https://|/)[A-Za-z0-9_./:@%+~-]+$ ]] \
        || fail "$repository is not an https:// address or an absolute folder."
    [ -f "$compose" ] || fail "$compose does not exist."
}

require() {
    [ -n "$DESTDIR" ] || [ "$(id -u)" = 0 ] || fail "run it with sudo: it installs a root service."
    for program in docker git python3 sha256sum; do
        command -v "$program" >/dev/null || fail "$program is not installed."
    done
    if [ -z "$DESTDIR" ]; then
        command -v systemctl >/dev/null || fail "systemd is needed for the host updater's service."
    fi
    python3 -I -c 'import sys, tarfile; sys.exit(not (sys.version_info >= (3, 11) and hasattr(tarfile, "data_filter")))' \
        || fail "the host updater needs Python 3.11.4 or newer as python3."
    docker compose version >/dev/null || fail "Docker Compose v2 (docker compose) is not installed."
    [ -n "$DESTDIR" ] || trusted_source
}

# The files root will run, and every folder above them, are root's and written by nobody else:
# else whoever writes them would choose what root runs.
trusted_source() {
    local path
    for path in "$SOURCE/src/atlas/installation/host_updater.py" "$HERE/atlas" \
        "$HERE/atlas-host-updater.service" "$HERE/install.sh"; do
        while :; do
            [ "$(stat -c %u "$path")" = 0 ] \
                || fail "$path is not root's: run the installer from a clone of root's own."
            [ $((0$(stat -c %a "$path") & 022)) = 0 ] \
                || fail "$path is written by others than root: run the installer from root's clone."
            [ "$path" = / ] && break
            path=$(dirname "$path")
        done
    done
}

# The commit that runs now, read by the checkout's owner: root never runs git in a tree its user
# writes, where a hook or a setting of theirs would run as root.
running_commit() {
    local owner
    owner=$(stat -c %U "$checkout")
    if [ "$owner" = "$(id -un)" ]; then
        git -C "$checkout" rev-parse HEAD
    else
        sudo -u "$owner" git -C "$checkout" rev-parse HEAD
    fi
}

install_helper() {
    install -d -m 0755 "$DESTDIR$LIB"
    install -m 0755 "$SOURCE/src/atlas/installation/host_updater.py" "$DESTDIR$LIB/host_updater.py"
    say "Host updater: $LIB/host_updater.py, sha256 $(sha256sum "$DESTDIR$LIB/host_updater.py" | cut -d' ' -f1)"
}

migrate() {
    install -d -m 0755 "$DESTDIR$ETC"
    if [ -e "$DESTDIR$ETC/compose.yml" ]; then
        say "Kept $ETC/compose.yml (edit it there, as root)."
    else
        local resolved
        resolved=$(mktemp)
        # Resolved whole (its .env, its relative paths), so the copy stands alone, as JSON (which
        # Compose reads as YAML). Its `build` sections go: they name the checkout, and the host
        # updater builds the exported commit itself. So do `env_file` and `label_file`, whose
        # values are inlined already: Compose would read those files again at each start. It may
        # hold the .env's values: root's, 0600.
        docker compose --project-name "$project" --file "$compose" config --format json \
            | python3 -I -c 'import json, sys
config = json.load(sys.stdin)
for service in config.get("services", {}).values():
    for key in ("build", "env_file", "label_file"):
        service.pop(key, None)
json.dump(config, sys.stdout, indent=2)' >"$resolved"
        install -m 0600 "$resolved" "$DESTDIR$ETC/compose.yml"
        rm -f "$resolved"
        say "Migrated $compose to $ETC/compose.yml; the original stays."
    fi
    if [ -n "$dockerfile" ] && [ ! -e "$DESTDIR$ETC/Dockerfile" ]; then
        install -m 0644 "$dockerfile" "$DESTDIR$ETC/Dockerfile"
        say "Migrated $dockerfile to $ETC/Dockerfile; the original stays."
    fi
}

write_override() {
    local file=$DESTDIR$ETC/compose.updater.yml
    cat >"$file" <<EOF
# Written by scripts/compose-host/install.sh (docs/COMPOSE_HOST.md), after compose.yml: the host
# updater's socket folder, and the installation kind Atlas reads. Never the Docker socket.
services:
  $service:
    image: $image
    environment:
      ATLAS_INSTALLATION: compose
    volumes:
      - type: bind
        source: /run/atlas-updater
        target: /run/atlas-updater
        read_only: true
EOF
    chmod 0644 "$file"
}

write_config() {
    local file=$DESTDIR$ETC/config.toml
    if [ -e "$file" ]; then
        say "Kept $ETC/config.toml."
        return
    fi
    local docker_file=""
    if [ -n "$dockerfile" ] || [ -e "$DESTDIR$ETC/Dockerfile" ]; then
        docker_file=$ETC/Dockerfile
    fi
    cat >"$file" <<EOF
# The host updater's settings (docs/COMPOSE_HOST.md). Root's: it refuses them otherwise.
repository = "$repository"
github = "$github"
branch = "$branch"
project = "$project"
service = "$service"
image = "$image"
compose_files = ["$ETC/compose.yml", "$ETC/compose.updater.yml"]
# "" builds each update with the commit's own Dockerfile.
dockerfile = "$docker_file"
volume = "$volume"
container_uid = $uid
EOF
    chmod 0644 "$file"
    say "Wrote $ETC/config.toml."
}

record_state() {
    local commit=$1
    install -d -m 0700 "$DESTDIR$STATE"
    if [ -e "$DESTDIR$STATE/state.json" ]; then
        say "Kept $STATE/state.json."
        return
    fi
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fail "the checkout's commit ($commit) is not a commit."
    printf '{"deployed": "%s"}\n' "$commit" >"$DESTDIR$STATE/state.json"
    chmod 0600 "$DESTDIR$STATE/state.json"
    # The image that runs now, also by its commit, so the first update can always go back to it.
    if docker image inspect "$image" >/dev/null 2>&1; then
        docker tag "$image" "${image%:*}:${commit:0:12}"
    fi
    say "Recorded the commit that runs: ${commit:0:12}."
}

# Checked before anything is written: an `atlas` of someone else's is never replaced.
check_shim() {
    local file=$DESTDIR$SHIM
    if [ -e "$file" ] && ! grep -q "$SHIM_MARK" "$file"; then
        fail "$SHIM exists and is not this installer's: move it away first."
    fi
}

install_shim() {
    local file=$DESTDIR$SHIM
    install -d -m 0755 "$(dirname "$file")"
    install -m 0755 "$HERE/atlas" "$file"
    say "Installed $SHIM: \`atlas update\` updates Atlas here; other commands run in its container."
}

install_unit() {
    install -d -m 0755 "$(dirname "$DESTDIR$UNIT")"
    install -m 0644 "$HERE/atlas-host-updater.service" "$DESTDIR$UNIT"
    # The socket's folder at boot, before Docker restarts the container whose bind mount needs it.
    install -d -m 0755 "$(dirname "$DESTDIR$TMPFILES")"
    printf '%s\n' "# Written by scripts/compose-host/install.sh: the host updater's socket folder." \
        "d /run/atlas-updater 0755 root root -" >"$DESTDIR$TMPFILES"
    chmod 0644 "$DESTDIR$TMPFILES"
    if [ -z "$DESTDIR" ]; then
        systemd-tmpfiles --create "$TMPFILES"
        systemctl daemon-reload
        systemctl enable atlas-host-updater.service
        systemctl restart atlas-host-updater.service
    fi
    say "Started atlas-host-updater.service."
}

recreate_container() {
    if [ "$recreate" = 0 ] || [ -n "$DESTDIR" ]; then
        say "The container mounts the socket the next time it is recreated."
        return
    fi
    say "Recreating Atlas's container on the same image, so it mounts the socket (a moment)."
    docker compose --project-name "$project" --file "$ETC/compose.yml" \
        --file "$ETC/compose.updater.yml" up --detach --no-build --pull never "$service"
}

main() {
    parse "$@"
    require
    check_shim
    local commit
    commit=$(running_commit)
    install_helper
    migrate
    write_override
    write_config
    record_state "$commit"
    if [ -z "$DESTDIR" ]; then
        python3 -I "$LIB/host_updater.py" validate \
            || fail "fix what the host updater said in $ETC, as root, and run this again."
    fi
    install_unit
    install_shim
    recreate_container
    say "Done. On this host \`atlas update\` updates Atlas; in the app, the Update button does."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
