"""Bulk-register tournament participants.

Examples::

    # explicit login:password pairs
    python -m starship_duel.tournament.register alice:s3cret bob:hunter2

    # from a file (one per line: "login password", "login,password", or "login:password";
    # blank lines and #comments ignored)
    python -m starship_duel.tournament.register --from participants.txt

    # just usernames -> generate + print strong passwords for each
    python -m starship_duel.tournament.register --generate alice bob carol

    # make them admins
    python -m starship_duel.tournament.register --admin alice:pw

Existing usernames are skipped (reported), so the script is safe to re-run.
"""

from __future__ import annotations

import argparse
import secrets
from typing import List, Optional, Tuple

from .accounts import AccountStore

Pair = Tuple[str, str]  # (username, password)


def _split_pair(token: str) -> Pair:
    for sep in (":", ",", "\t", " "):
        if sep in token:
            u, p = token.split(sep, 1)
            return u.strip(), p.strip()
    raise ValueError(f"cannot parse {token!r} as login<sep>password")


def _pairs_from_file(path: str) -> List[Pair]:
    pairs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pairs.append(_split_pair(line))
    return pairs


def register_participants(store: AccountStore, pairs: List[Pair], *,
                          is_admin: bool = False) -> Tuple[List[Pair], List[str]]:
    """Create each (username, password). Returns (created, skipped_existing)."""
    created: List[Pair] = []
    skipped: List[str] = []
    for username, password in pairs:
        if not username or not password:
            skipped.append(username or "<blank>")
            continue
        if store.get_user_by_name(username) is not None:
            skipped.append(username)
            continue
        store.create_user(username, password, is_admin=is_admin)
        created.append((username, password))
    return created, skipped


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Bulk-register tournament participants")
    ap.add_argument("pairs", nargs="*", help="login:password tokens (or usernames with --generate)")
    ap.add_argument("--from", dest="from_file", help="read login/password lines from a file")
    ap.add_argument("--generate", action="store_true",
                    help="treat positional args as usernames and generate passwords")
    ap.add_argument("--admin", action="store_true", help="create the users as admins")
    ap.add_argument("--db", default=None, help="accounts sqlite path ($STARSHIP_ACCOUNTS_DB)")
    args = ap.parse_args(argv)

    if args.generate:
        pairs = [(u, secrets.token_urlsafe(9)) for u in args.pairs]
    else:
        pairs = [_split_pair(t) for t in args.pairs]
    if args.from_file:
        pairs += _pairs_from_file(args.from_file)
    if not pairs:
        ap.error("no participants given (pass login:password tokens, --from FILE, or --generate)")

    store = AccountStore(args.db)
    created, skipped = register_participants(store, pairs, is_admin=args.admin)

    role = "admin" if args.admin else "user"
    print(f"created {len(created)} {role}(s):")
    for u, p in created:
        # Show the password only when we generated it; otherwise the caller knows it.
        print(f"  {u}" + (f"  password: {p}" if args.generate else ""))
    if skipped:
        print(f"skipped {len(skipped)} existing/invalid: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
