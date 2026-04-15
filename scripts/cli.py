import argparse
import sys

from scripts import doctor, ingest, lint, query, sync
from scripts.version import read_version


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Unified CLI for the LLM wiki starter.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {read_version()}")
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", help="Sync source files into raw/inbox.")
    sync_parser.add_argument("sync_args", nargs=argparse.REMAINDER)

    ingest_parser = subparsers.add_parser("ingest", help="Build wiki artifacts from raw sources.")
    ingest_parser.add_argument("ingest_args", nargs=argparse.REMAINDER)

    query_parser = subparsers.add_parser("query", help="Ask a question against the local wiki.")
    query_parser.add_argument("query_args", nargs=argparse.REMAINDER)
    subparsers.add_parser("lint", help="Lint the generated wiki.")
    subparsers.add_parser("doctor", help="Validate local config, versioning, and demo artifact readiness.")

    refresh_parser = subparsers.add_parser("refresh", help="Run sync with prune and then ingest.")
    refresh_parser.add_argument("--dry-run", action="store_true", help="Dry run sync before ingesting.")
    refresh_parser.add_argument("--reconcile", action="store_true", help="Rebuild derived wiki artifacts before ingest.")

    fast_parser = subparsers.add_parser("refresh-fast", help="Run sync without prune and then ingest.")
    fast_parser.add_argument("--dry-run", action="store_true", help="Dry run sync before ingesting.")

    args = parser.parse_args(argv)

    if args.command == "sync":
        sys.argv = ["sync.py", *args.sync_args]
        sync.main()
        return
    if args.command == "ingest":
        sys.argv = ["ingest.py", *args.ingest_args]
        ingest.main()
        return
    if args.command == "query":
        sys.argv = ["query.py", *args.query_args]
        query.main()
        return
    if args.command == "lint":
        raise SystemExit(lint.main())
    if args.command == "doctor":
        raise SystemExit(doctor.main())
    if args.command == "refresh":
        sync_argv = ["sync.py", "--prune"]
        if args.dry_run:
            sync_argv.append("--dry-run")
        sys.argv = sync_argv
        sync.main()
        if not args.dry_run:
            ingest_argv = ["ingest.py"]
            if args.reconcile:
                ingest_argv.append("--reconcile")
            sys.argv = ingest_argv
            ingest.main()
        return
    if args.command == "refresh-fast":
        sync_argv = ["sync.py"]
        if args.dry_run:
            sync_argv.append("--dry-run")
        sys.argv = sync_argv
        sync.main()
        if not args.dry_run:
            sys.argv = ["ingest.py"]
            ingest.main()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
