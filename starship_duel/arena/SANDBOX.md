# Arena sandbox (untrusted-bot isolation)

Submitted tournament bots are arbitrary Python/C++ from strangers. The real
security boundary is a locked-down **docker** container that both *compiles* and
*runs* every submission. Implemented in [`sandbox.py`](sandbox.py); image in
[`Dockerfile`](Dockerfile).

## Build the image (once, on each worker/web host)

```sh
python -m starship_duel.arena.sandbox build       # -> image "starship-arena-sandbox"
python -m starship_duel.arena.sandbox status      # show mode + docker availability
```

## Turn it on

Isolation is selected by `STARSHIP_SANDBOX`:

| value             | behaviour                                                            |
|-------------------|---------------------------------------------------------------------|
| `docker`          | **always** sandbox; **fail closed** if docker is missing (use in prod) |
| `auto` (default)  | sandbox if the `docker` CLI is present, else bare subprocess + warning |
| `none`            | never sandbox (only for a fully trusted bot set on a trusted host)  |

Run the web server and the match workers with `STARSHIP_SANDBOX=docker` in
production so a misconfigured host refuses to run untrusted code raw:

```sh
STARSHIP_SANDBOX=docker uvicorn starship_duel.web.server:app ...
STARSHIP_SANDBOX=docker python -m starship_duel.tournament.worker --workers 4
```

## What the container enforces (per `docker run`)

- `--network none` — no internet, no host-network access.
- `--read-only` root fs + a small `--tmpfs /tmp` (`noexec,nosuid,nodev`) — nothing
  else is writable; the bot's own dir is mounted **read-only** at `/bot`.
- `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user 65534` (nobody).
- `--security-opt apparmor=unconfined` — rootless Docker on AppArmor hosts (Ubuntu
  24.04) can't load the `docker-default` profile and would refuse to start the
  container; the default **seccomp** profile still applies, and isolation rests on
  the user namespace + cap-drop + no-new-privileges + `--network none` + read-only fs.
- `--memory` / `--cpus` / `--pids-limit` — a fork bomb or memory hog is contained.
- Each bot mounts **only its own** directory, so one competitor can't read another's
  source.

The container speaks the same one-JSON-line-per-turn protocol over stdin/stdout,
and is started **once per game** (per-turn latency excludes container start-up),
reaped by `--rm` at match end.

### Tunables (env)

`STARSHIP_SANDBOX_IMAGE`, `STARSHIP_SANDBOX_MEMORY_MB` (256), `STARSHIP_SANDBOX_CPUS`
(1.0), `STARSHIP_SANDBOX_PIDS` (128), `STARSHIP_SANDBOX_TMPFS_MB` (64),
`STARSHIP_SANDBOX_BUILD_TIMEOUT` (180s), `STARSHIP_SANDBOX_COMPILE_MEMORY_MB` (1024).

> The C++ *compile* container has a separate, larger memory ceiling
> (`STARSHIP_SANDBOX_COMPILE_MEMORY_MB`, default 1024) than the per-bot *run* cap:
> `cc1plus` compiling the vendored `nlohmann/json` is OOM-killed at 256 MB, which
> would otherwise fail every C++ submission. Keep it at ~768 MB or higher.

> Note: the first move of a match pays container start-up (~0.5–1s). `SubprocessBot`
> treats a slow move as a *strike + safe default*, never a loss, so a cold start
> costs at most one strike on turn one.
