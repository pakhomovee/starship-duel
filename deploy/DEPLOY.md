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
sudo apt-get install -y docker.io uidmap dbus-user-session fuse-overlayfs
sudo systemctl disable --now docker.service docker.socket || true   # no rootful daemon

# Let the starship user's services + dockerd run without an active login:
sudo loginctl enable-linger starship

# Install the rootless daemon AS the starship user:
sudo -iu starship dockerd-rootless-setuptool.sh install
sudo -iu starship systemctl --user enable --now docker
sudo -iu starship docker run --rm hello-world      # must succeed
```

Find the uid and record the socket path — you need it in the env file:

```sh
id -u starship        # e.g. 1001  -> DOCKER_HOST=unix:///run/user/1001/docker.sock
```

> Why rootless: if a bot ever broke out of its container, it would be the
> unprivileged `starship` user, not root. With rootful Docker, docker-group access
> is root-equivalent and an escape owns the box.

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
