# Deploying Starship Duel safely on a public VPS

This app runs **arbitrary native code submitted by strangers** (Python/C++ bots).
The entire security model rests on running every submission inside a locked-down
**rootless-Docker** container, and never exposing the app process directly. Follow
this in order; do **not** open the firewall until the sandbox is validated (step 7).

Target host in these notes: **Ubuntu 24.04**, domain
**`starships.mopmacaque.com`**.

Threat model recap:
- Untrusted bot code → contained by rootless Docker (`--network none`, read-only
  rootfs, `--cap-drop ALL`, non-root, mem/cpu/pid caps). A container escape lands
  as the unprivileged `starship` user, **not root**.
- Web app compromise → the app runs as `starship`, binds loopback only, and TLS is
  terminated by Caddy. No paid API keys live on the host.
- Network → only 22/80/443 inbound; the app port 8000 is never exposed.

---

## 0. DNS

Point `starships.mopmacaque.com` (A / AAAA) at the VPS public IP **before** step 6
so Let's Encrypt can validate.

## 1. System user + directories

```sh
sudo adduser --system --group --shell /bin/bash --home /home/starship starship
sudo mkdir -p /opt/starship-duel /var/lib/starship /etc/starship /var/log/caddy
sudo chown -R starship:starship /opt/starship-duel /var/lib/starship
```

## 2. Code + virtualenv

```sh
sudo -u starship git clone https://github.com/pakhomovee/starship-duel /opt/starship-duel
cd /opt/starship-duel
sudo -u starship python3 -m venv .venv
sudo -u starship .venv/bin/pip install -U pip
sudo -u starship .venv/bin/pip install -r requirements.txt
```

> The tournament/web stack needs `fastapi uvicorn python-multipart networkx choix`
> (and the RL extras if you use them). `requirements.txt` covers all of it.

## 3. Rootless Docker (the real security boundary)

```sh
sudo apt-get update
sudo apt-get install -y docker.io uidmap dbus-user-session fuse-overlayfs slirp4netns
sudo systemctl disable --now docker.service docker.socket || true   # no rootful daemon

# Let the starship user's services + dockerd run without an active login:
sudo loginctl enable-linger starship
```

**3a. Subordinate uid/gid ranges.** Rootless Docker maps container uids into a
delegated subid range. A user created with `adduser --system` (step 1) gets **no**
such range, and the setuptool then fails to create the unit. Confirm and, if empty,
add one:

```sh
grep -E '^starship:' /etc/subuid /etc/subgid      # both must return a line
# If empty, allocate a 65536-wide block that does not overlap existing ranges
# (check `cat /etc/subuid /etc/subgid` first — a regular user may already hold 100000):
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 starship
# usermod refuses for some system users; if so, append directly:
#   echo 'starship:100000:65536' | sudo tee -a /etc/subuid
#   echo 'starship:100000:65536' | sudo tee -a /etc/subgid
```

**3b. `starship` is a lingering user with no login session**, so every `sudo -iu
starship` docker/systemctl call needs `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS`
in its environment or you get `Failed to connect to bus: No medium found`. Define a
helper to keep the commands readable:

```sh
SUID=$(id -u starship)
asstarship() { sudo -iu starship XDG_RUNTIME_DIR=/run/user/$SUID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$SUID/bus \
  DOCKER_HOST=unix:///run/user/$SUID/docker.sock "$@"; }
```

**3c. Disable Docker networking BEFORE first start.** All bots run `--network none`
(see [arena sandbox](../starship_duel/arena/SANDBOX.md)), so Docker never needs its
bridge — and on many VPS kernels dockerd cannot program nftables even inside
RootlessKit's namespace, so bridge setup at startup kills the daemon with
`iptables ... nf_tables: Permission denied (you must be root)`. Turn it off in the
**rootless** config (`~/.config/docker/daemon.json`, *not* `/etc/docker/`):

```sh
sudo -iu starship mkdir -p /home/starship/.config/docker
sudo -iu starship tee /home/starship/.config/docker/daemon.json >/dev/null <<'EOF'
{ "iptables": false, "ip6tables": false, "bridge": "none" }
EOF
```

**3d. Install + start + smoke-test.** Order matters: the setuptool `install`
creates the user unit at `~/.config/systemd/user/docker.service` **and** starts the
daemon. `systemctl --user enable` never creates the unit — run it only *after*
install, and if you ever `uninstall` (which deletes the unit) you must re-run
install before enable, or you get `Unit file docker.service does not exist`.

```sh
asstarship dockerd-rootless-setuptool.sh install    # creates the unit + starts dockerd
asstarship systemctl --user enable --now docker      # enable at boot (needs linger, step 3)

# NOT `docker run hello-world`: with bridge:none a default-network container has no
# net, and the CLI needs DOCKER_HOST (the helper sets it) or it hits the rootful
# socket -> "permission denied ... /var/run/docker.sock". Test the way bots run:
asstarship docker run --rm --network none alpine true && echo "sandbox-style run OK"
```

Record the socket path — you need it in the env file (step 4):

```sh
id -u starship        # e.g. 1001  -> DOCKER_HOST=unix:///run/user/1001/docker.sock
```

> Why rootless: if a bot ever broke out of its container, it would be the
> unprivileged `starship` user, not root. With rootful Docker, docker-group access
> is root-equivalent and an escape owns the box.

**Troubleshooting the rootless install** (in order — each symptom blocks the next):

| Symptom | Cause | Fix |
|---|---|---|
| `Unit file docker.service does not exist` | `install` never ran, failed, or was undone by `uninstall` — `enable` alone never creates the unit | (re-)run step 3d `install` first, then `enable`; read `install`'s output for the real error |
| `No subuid ranges found` / install aborts creating the unit | system user has no subid range | step 3a |
| `Failed to connect to bus: No medium found` | missing `XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS` | use the `asstarship` helper (3b) |
| daemon dies with `iptables ... Permission denied (you must be root)` | kernel blocks nftables in the netns | step 3c, then re-run install |
| `permission denied ... /var/run/docker.sock` | CLI hitting rootful socket | set `DOCKER_HOST` (helper does) or `docker context use rootless` |
| container start fails: `Could not check if docker-default AppArmor profile was loaded: ... apparmor/profiles: permission denied` | rootless daemon can't load the AppArmor profile (Ubuntu 24.04) | already handled — the sandbox passes `--security-opt apparmor=unconfined`; seccomp + userns + cap-drop still apply |
| `docker build` fails, or any networked container errors `operation not permitted` / `iptables ... Permission denied` (yet `docker pull` and `--network none` both work) | this kernel forbids netfilter programming **and** `setns` from the rootless userns (common on hardened / Ubuntu 24.04) — so bridge *and* host networking are unavailable to rootless containers | you can't build on-host; build the image elsewhere and bring it in via `docker pull` or `docker load` — see **"The arena sandbox image"** below |

To retry a botched install cleanly, run it **as starship** (never as root):
`asstarship dockerd-rootless-setuptool.sh uninstall -f`.

**Reading the real dockerd error.** When the daemon fails to start, `systemctl
--user status` only says "control process exited", and `journalctl --user` under
`sudo -iu` often prints "No journal files were opened due to insufficient
permissions". Stop the crash-loop and run the daemon in the **foreground** — it
prints the true failure on the last line, then Ctrl-C:

```sh
asstarship systemctl --user stop docker
asstarship dockerd-rootless.sh 2>&1 | tail -40
```

Ignore the `AppArmor profile ... permission denied` and `Deleting nftables rules ...
exit status 1` lines — they're benign in rootless mode. The real cause is the last
`failed to start daemon: ...` line. Also `cat` the config to confirm a fix actually
landed before restarting: `asstarship cat ~starship/.config/docker/daemon.json`.

### The arena sandbox image

The daemon runs bots with `--security-opt apparmor=unconfined` (rootless can't load
`docker-default`; isolation is userns + `--cap-drop ALL` + `--network none` +
read-only fs + seccomp) — `sandbox.py` passes this automatically.

**You usually cannot `docker build` under the rootless daemon.** On hardened /
Ubuntu 24.04 kernels the rootless user namespace is denied both netfilter
programming (bridge NAT → `iptables ... nf_tables: Permission denied`) **and**
`setns` (host networking → `setns ... operation not permitted`), even with
`kernel.apparmor_restrict_unprivileged_userns=0` and all NAT modules loaded — only
`--network none` works. Since the Dockerfile's `RUN apt-get install g++` needs a
networked container, the *rootless* daemon can't build it. Quick check:

```sh
asstarship docker run --rm --network=host --security-opt apparmor=unconfined \
  python:3.12-slim echo ok        # 'operation not permitted' => rootless build won't work
```

**Fix: build with the rootful daemon, then hand the image to the rootless one.**
`root` in the host namespace has the caps the rootless userns lacks, so the build
just works. The rootful daemon runs only for this one trusted build (your own
Dockerfile, no untrusted code) and is stopped again afterwards, so the runtime
posture is unchanged.

```sh
sudo systemctl start docker.service                 # rootful daemon, temporarily
sudo docker build -t starship-arena-sandbox \
  -f /opt/starship-duel/starship_duel/arena/Dockerfile \
  /opt/starship-duel/starship_duel/arena
# move it from the rootful store into the rootless (starship) store:
sudo docker save starship-arena-sandbox:latest -o /tmp/arena.tar
sudo chmod 644 /tmp/arena.tar
asstarship docker load -i /tmp/arena.tar
rm -f /tmp/arena.tar
sudo systemctl stop docker.service                  # back off
sudo systemctl disable docker.service docker.socket # keep it off
asstarship python -m starship_duel.arena.sandbox status   # -> present=True
```

*Alternatives* if you'd rather not run the rootful daemon at all: build the image on
another machine and either `docker push` it to a registry then
`asstarship python -m starship_duel.arena.sandbox pull ghcr.io/<you>/starship-arena-sandbox:latest`
(pulls + tags locally — the daemon's own pull egresses via slirp4netns and works),
or `docker save … | gzip`, copy it over, and `asstarship docker load -i …`.

Once the image is present, `sandbox build --if-missing` (what `validate-sandbox.sh`
runs) is a no-op, so validation passes without a build.

> If you hit this, the box is fighting rootless Docker at the kernel level; a
> dedicated KVM VPS without the userns hardening is the intended long-term home.

## 4. Configuration + secrets

```sh
sudo cp /opt/starship-duel/deploy/starship.env.example /etc/starship/starship.env
sudo nano /etc/starship/starship.env      # set the uid, admin creds, tokens
#   openssl rand -hex 32     # for STARSHIP_ADMIN_TOKEN
sudo chown root:starship /etc/starship/starship.env
sudo chmod 640 /etc/starship/starship.env
```

Key settings (see the file for the rest):
- `STARSHIP_SANDBOX=docker` — **must** be `docker`, never `auto`/`none`.
- `XDG_RUNTIME_DIR` / `DOCKER_HOST` — match the uid from step 3.
- `STARSHIP_ADMIN_USER` / `STARSHIP_ADMIN_PASSWORD` — seed admin (comment the
  password out after the first successful start).
- Leave `STARSHIP_ACCESS_TOKEN` **unset** for a public tournament.

## 5. systemd services

```sh
sudo cp /opt/starship-duel/deploy/starship-web.service    /etc/systemd/system/
sudo cp /opt/starship-duel/deploy/starship-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now starship-web starship-worker
systemctl status starship-web --no-pager
curl -fsS http://127.0.0.1:8000/api/bots >/dev/null && echo "app up on loopback"
```

The web unit binds `127.0.0.1:8000` only and runs a **single** uvicorn process
(game state is in-memory — don't add `--workers`). Scale the *worker* unit's
`--workers` instead.

## 6. Caddy (TLS reverse proxy)

```sh
sudo apt-get install -y caddy
sudo cp /opt/starship-duel/deploy/Caddyfile /etc/caddy/Caddyfile
sudo chown -R caddy:caddy /var/log/caddy
sudo systemctl reload caddy
```

Caddy auto-issues a Let's Encrypt cert for the domain and proxies REST + WebSocket.
Once it's green, HTTPS is live at `https://starships.mopmacaque.com`.

## 7. Validate the sandbox — BEFORE opening the firewall

This project's docker path had only ever been exercised against a fake-docker
shim, so prove it for real on this host:

```sh
sudo -u starship env $(grep -v '^#' /etc/starship/starship.env | xargs) \
     /opt/starship-duel/deploy/validate-sandbox.sh
```

All five checks must pass (docker reachable, sandbox enabled, image builds,
fail-closed when docker is hidden, and a bot actually runs in a container). If
any fail, **stop** — do not expose the app.

## 8. Firewall + SSH hardening (open to the world last)

```sh
sudo /opt/starship-duel/deploy/firewall.sh      # allows 22 (limited), 80, 443 only
```

Harden SSH (`/etc/ssh/sshd_config`), then `sudo systemctl restart ssh`:
```
PasswordAuthentication no
PermitRootLogin no
```
Make sure your key works first. Optionally restrict 22 to your own IP (see the
note printed by `firewall.sh`).

## 9. Automatic security patches

The kernel is your last line of defense against a container escape, so keep it
patched:
```sh
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades      # enable
```
Reboot promptly when a kernel update lands (`/var/run/reboot-required`).

---

## Operating notes

- **Backups:** everything durable is under `/var/lib/starship` (SQLite DBs). Snapshot
  it. `submissions/` is a rebuildable cache (re-materialized from the DB).
- **Kick off a tournament** (admin token from the env file):
  ```sh
  curl -X POST https://starships.mopmacaque.com/api/tournament/schedule/full \
       -H "X-Admin-Token: $STARSHIP_ADMIN_TOKEN"
  ```
- **Watch logs:** `journalctl -u starship-web -f` / `-u starship-worker -f`.
- **Update the app:** `git pull` in `/opt/starship-duel` (as `starship`), then
  `sudo systemctl restart starship-web starship-worker`.

## Residual risk (know this)

Even fully hardened, you are executing attacker code. The strong container flags
stop escape-by-misconfiguration, but a **kernel 0-day** could still break out to
the `starship` user. To shrink that further: keep the kernel patched (step 9),
consider adding gVisor (`runsc`) as the container runtime later, and — ideally —
run this on a throwaway VPS that hosts nothing else you care about.
