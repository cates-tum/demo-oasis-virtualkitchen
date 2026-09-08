#!/usr/bin/env python3
"""Operator CLI for e-kitchen. No HTTP surface: run it from a shell, or
`docker compose run --rm web python manage.py ...`. Nexus must be reachable
(OASIS_URL, default http://localhost:8000).

    python manage.py generate --count 10            # 10 per wired schema type
    python manage.py generate --count 5 --schema grill_red_meat
    python manage.py generate --count 10 --dry-run  # print, POST nothing

    python manage.py clean --yes                    # DELETE every Nexus entry
    python manage.py clean --schema fermentation_beer --yes
"""
import argparse
import random
import sys

import client
from engine import generator

WIRED = list(generator.OUTCOME_FORMULAS)
NICK_POOL = ["alex", "sam", "jordan", "kim", "mara", "vik", None]


def cmd_generate(args):
    targets = [args.schema] if args.schema else WIRED
    for st in targets:
        if st not in generator.OUTCOME_FORMULAS:
            sys.exit(f"not a wired schema_type: {st!r} (wired: {', '.join(WIRED)})")

    made = 0
    for st in targets:
        fields = client.get_schema_fields(st)
        for _ in range(args.count):
            nick = random.choice(NICK_POOL)
            form = generator.random_form(st, fields)
            entry = generator.build_entry(st, fields, form, nick or "")
            if args.dry_run:
                print(f"[dry-run] {st}: {entry['title']} -> {entry['data'].get('outcome')}")
            else:
                created = client.post_entry(**entry)
                print(f"created {created['id']}  {created['title']}")
            made += 1
    verb = "would generate" if args.dry_run else "generated"
    print(f"{verb} {made} entries across {len(targets)} schema type(s)")


def cmd_clean(args):
    first = client.list_entries(args.schema)
    if not first:
        print("nothing to delete")
        return
    scope = f"schema_type={args.schema}" if args.schema else "all schema types"
    if not args.yes:
        more = "+" if len(first) == 500 else ""
        reply = input(f"delete {len(first)}{more} entries from Nexus ({scope})? [y/N] ")
        if reply.strip().lower() != "y":
            print("aborted")
            return
    # Nexus caps a page at 500 and has no bulk delete; page until empty.
    n = 0
    batch = first
    while batch:
        for e in batch:
            client.delete_entry(e["id"])
            n += 1
        batch = client.list_entries(args.schema)
    print(f"deleted {n} entries")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="bulk-generate experiments into Nexus")
    g.add_argument("--count", type=int, default=10, help="entries per schema type (default 10)")
    g.add_argument("--schema", help="one wired schema_type; default is all")
    g.add_argument("--dry-run", action="store_true", help="print payloads, POST nothing")
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("clean", help="delete Nexus entries")
    c.add_argument("--schema", help="one schema_type; default is all")
    c.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    c.set_defaults(func=cmd_clean)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except client.OasisUnavailable as e:
        sys.exit(f"Nexus unavailable: {e}")


if __name__ == "__main__":
    main()
