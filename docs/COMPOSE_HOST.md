# Atlas on a Compose host

Atlas on a home server, run by Docker Compose, updated with the same `atlas update` and the same
Update button as everywhere else. The design is in
[ARCHITECTURE.md, The update of a Compose host](ARCHITECTURE.md#the-update-of-a-compose-host--decided-2026-10-24).

## What gets installed

| Where | What | Whose |
|---|---|---|
| `/usr/local/lib/atlas-host-updater/host_updater.py` | the host updater, copied from the installer's own clone | root, 0755 |
| `/etc/atlas-updater/config.toml` | what it updates and from where | root, 0644 |
| `/etc/atlas-updater/compose.yml` | your Compose file, resolved by `docker compose config` | root, 0600 |
| `/etc/atlas-updater/Dockerfile` | your `Dockerfile.homelab`, when you have one | root, 0644 |
| `/etc/atlas-updater/compose.updater.yml` | the socket's folder and `ATLAS_INSTALLATION=compose` | root, 0644 |
| `/var/lib/atlas-updater/` | its mirror, builds, `/data` snapshots, log and state | root, 0700 |
| `/etc/systemd/system/atlas-host-updater.service` | the service | root |
| `/etc/tmpfiles.d/atlas-updater.conf` | `/run/atlas-updater` made at boot, before Docker | root, 0644 |
| `/usr/local/bin/atlas` | `atlas update` here; other commands in the container | root, 0755 |

Your checkout and its untracked `Dockerfile.homelab` and `docker-compose.homelab.yml` are left as
they are: from now on they are read once, by the installer, and never by root again. The Docker
socket is never mounted into the container, and no `sudo` rule is added: `atlas update` on the host
asks for your password the way `sudo` always does.

## Installing it

On the server (Python 3.11.4 or newer as `python3`, systemd, Docker Compose v2), from a clone of
root's own, never from your checkout: the installer runs as root, and your checkout is yours to
write, and the agent's through you. It refuses to run from a tree root does not own alone.

```bash
sudo git clone --depth 1 https://github.com/marqueskaio/atlas.git /root/atlas-installer
# --checkout: the one holding your Compose file; --help for the rest
sudo /root/atlas-installer/scripts/compose-host/install.sh --checkout /path/to/the/checkout
```

The program root runs, the service and `atlas` come from that clone. From `--checkout` it reads
only the Compose file and `Dockerfile.homelab` to migrate, and the commit that runs (by its owner's
`git`, never root's). Your Compose file is resolved by `docker compose config` as root: read it
before, as you would anything you run with `sudo`.

It says each step. It checks the migrated Compose file with the host updater itself and stops on
any problem, before starting anything. Last, it recreates the Atlas container once on the same
image, so it mounts the socket; `--no-recreate` leaves that for later. `/data` is untouched.

Read `/etc/atlas-updater/compose.yml` once: it is your file resolved, values of its `.env` included.
Edit it there, as root, from now on, and check it after with
`sudo python3 -I /usr/local/lib/atlas-host-updater/host_updater.py validate`.

## The first update

The Atlas running before the install does not know the server's `update.*` methods yet, so its
app shows no button for the server. Update once from the host:

```bash
atlas update
```

From then on the app's Update button, with the server on screen, does the same.

## When CI cannot verify

The host updater offers a commit CI did not verify (GitHub Actions blocked, offline, still
running) with the reason, and updates only on an explicit yes: the box in the app's dialog, or
typing `update anyway` on the host. A commit CI failed is not offered.

## When an update fails

A failed build changes nothing: the server kept running. A failure after the stop puts back the old
image and `/data` from its snapshot; the app says so, with the log. If even that fails, the message
names the snapshot in `/var/lib/atlas-updater/snapshots/`. Its log: `/var/lib/atlas-updater/update.log`,
and `journalctl -u atlas-host-updater`.

## A new host updater

An update never replaces the code root runs. When a commit changes
`src/atlas/installation/host_updater.py` or `scripts/compose-host/`, update root's clone
(`sudo git -C /root/atlas-installer pull --ff-only`) and run its `install.sh` again with the same
`--checkout`: it keeps the files in `/etc/atlas-updater/` and the state, and replaces the program,
the service and `atlas`.
