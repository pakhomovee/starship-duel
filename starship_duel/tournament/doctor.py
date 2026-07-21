"""Explain why competitors aren't competing.

The failure this exists for: a competitor row is permanent, but a bot is only
*launchable* while its active submission still builds here.  When the two drift
apart, every match for that competitor dies with ``no launch spec for bot
competitor ...`` -- and the real reason (a compile error, usually) was only ever
raised inside :func:`~starship_duel.tournament.accounts.materialize_active`,
which used to swallow it.  This rebuilds every active submission and prints the
error it hits::

    python -m starship_duel.tournament.doctor

Add ``--purge-unrunnable`` to drop the queued matches of competitors that cannot
run, so they stop accumulating errors while their authors fix the build.
"""

from __future__ import annotations

import argparse
from typing import Optional

from .accounts import AccountStore, BuildError, build_command, default_submissions_dir
from .registry import BASELINES, BotRegistry
from .store import TournamentStore


def diagnose(accounts: AccountStore, tourney: TournamentStore, *,
             submissions_dir: Optional[str] = None) -> dict:
    """Return ``{bot_id: None | reason}`` for every active bot competitor."""
    from pathlib import Path

    from ..arena.sandbox import SandboxSpec

    out = Path(submissions_dir or default_submissions_dir()).resolve()
    sandbox = SandboxSpec.from_env()
    subs = {s["username"]: s for s in accounts.active_submissions()}

    report = {}
    for c in tourney.list_competitors(kind="bot", active_only=True):
        cid = c["id"]
        sub = subs.get(cid)
        if sub is None:
            report[cid] = "no active validated submission (deleted, or every upload rejected)"
            continue
        try:
            build_command(sub["code"], sub["filename"], out / cid, cid, sandbox=sandbox)
            report[cid] = None
        except BuildError as e:
            report[cid] = f"will not build: {e}"
        except Exception as e:  # docker down, permissions, ...
            report[cid] = f"{type(e).__name__}: {e}"
    return report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Diagnose non-competing competitors")
    ap.add_argument("--db", default=None, help="tournament sqlite path ($STARSHIP_TOURNEY_DB)")
    ap.add_argument("--accounts-db", default=None, help="accounts sqlite path ($STARSHIP_ACCOUNTS_DB)")
    ap.add_argument("--submissions-dir", default=None)
    ap.add_argument("--purge-unrunnable", action="store_true",
                    help="delete queued matches for competitors that cannot run")
    args = ap.parse_args(argv)

    accounts = AccountStore(args.accounts_db)
    tourney = TournamentStore(args.db)
    report = diagnose(accounts, tourney, submissions_dir=args.submissions_dir)

    ok = sorted(c for c, why in report.items() if why is None)
    bad = sorted((c, why) for c, why in report.items() if why is not None)
    stats = tourney.competitor_match_stats()

    print(f"runnable ({len(ok)}): {', '.join(ok) or '(none)'}")
    print(f"NOT runnable ({len(bad)}):")
    for cid, why in bad:
        st = stats.get(cid, {})
        print(f"  {cid}: {why}")
        print(f"      matches: {st.get('error', 0)} errored, {st.get('pending', 0)} queued, "
              f"{st.get('done', 0)} played")
    if not bad:
        print("  (none — every active competitor can be launched)")

    if args.purge_unrunnable and bad:
        n = sum(tourney.purge_matches(cid, statuses=("pending",)) for cid, _ in bad)
        print(f"\npurged {n} queued match(es) for {len(bad)} unrunnable competitor(s)")


if __name__ == "__main__":
    main()
