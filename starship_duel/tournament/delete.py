"""Delete tournament participants.

Removes the account (with its sessions + submissions), drops the competitor from
the ladder (and its not-yet-played matches; finished games/replays are kept for
history), and deletes the materialized bot file.

Examples::

    python -m starship_duel.tournament.delete alice bob        # by login
    python -m starship_duel.tournament.delete --all --yes      # everyone (admins kept)
    python -m starship_duel.tournament.delete --all --include-admins --yes

``--all`` requires ``--yes`` and, by default, spares admin accounts so you don't
lock yourself out (pass ``--include-admins`` to remove them too).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from .accounts import AccountStore, default_submissions_dir
from .store import TournamentStore


def delete_participants(logins: List[str], *, accounts: AccountStore,
                        tourney: Optional[TournamentStore] = None,
                        submissions_dir: Optional[str] = None) -> List[str]:
    """Delete each login end-to-end. Returns the logins actually removed."""
    subs_dir = Path(submissions_dir or default_submissions_dir())
    removed: List[str] = []
    for login in logins:
        if not accounts.delete_user(login):
            continue
        if tourney is not None:
            tourney.remove_competitor(login)
        bot_file = subs_dir / f"{login}.py"
        try:
            bot_file.unlink()
        except OSError:
            pass
        removed.append(login)
    return removed


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Delete tournament participants")
    ap.add_argument("logins", nargs="*", help="usernames to delete")
    ap.add_argument("--all", action="store_true", help="delete every participant")
    ap.add_argument("--include-admins", action="store_true",
                    help="with --all, also delete admin accounts (default: keep them)")
    ap.add_argument("--yes", action="store_true", help="required confirmation for --all")
    ap.add_argument("--db", default=None, help="accounts sqlite path ($STARSHIP_ACCOUNTS_DB)")
    ap.add_argument("--tourney-db", default=None, help="tournament sqlite path ($STARSHIP_TOURNEY_DB)")
    ap.add_argument("--submissions-dir", default=None, help="where submission bots are materialized")
    args = ap.parse_args(argv)

    accounts = AccountStore(args.db)
    tourney = TournamentStore(args.tourney_db)

    if args.all:
        if not args.yes:
            ap.error("--all is destructive; re-run with --yes to confirm")
        logins = [u["username"] for u in accounts.list_users()
                  if args.include_admins or not u["is_admin"]]
    elif args.logins:
        logins = args.logins
    else:
        ap.error("give usernames to delete, or --all")

    removed = delete_participants(logins, accounts=accounts, tourney=tourney,
                                  submissions_dir=args.submissions_dir)
    print(f"deleted {len(removed)} participant(s): {', '.join(removed) or '(none)'}")
    not_found = sorted(set(logins) - set(removed))
    if not_found:
        print(f"not found: {', '.join(not_found)}")


if __name__ == "__main__":
    main()
