# Sandbox image for untrusted Starship Duel arena bots.
#
# It only needs to (a) run a single-file Python 3 bot that speaks the stdin/stdout
# JSON protocol -- the SDK is pure stdlib, so no pip packages are required -- and
# (b) compile a single-file C++17 bot against the vendored nlohmann/json header.
#
# Security posture is enforced at *run* time by `docker run` flags (see
# arena/sandbox.py): --network none, --read-only, --cap-drop ALL,
# --security-opt no-new-privileges, --user 65534, and memory/cpu/pids limits.
# The image itself is deliberately minimal.
#
# Build:  python -m starship_duel.arena.sandbox build
#    (or) docker build -t starship-arena-sandbox -f starship_duel/arena/Dockerfile starship_duel/arena
FROM python:3.12-slim

# g++ for compiling C++ submissions in-container. No network at build-run time,
# so everything a bot needs must already be here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ libstdc++-12-dev \
    && rm -rf /var/lib/apt/lists/*

# Bots run as an unprivileged, home-less user (uid 65534 = nobody) and may only
# write to the tmpfs mounted at /tmp. /bot is a read-only mount of the bot's dir.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /bot

# No ENTRYPOINT: the exact command (python3 bot.py / ./bot / g++ ...) is supplied
# by `docker run` so the same image serves both compile and run.
