"""The `tally` command line."""
import argparse
import sys

from tally import report, store


def build_parser():
    parser = argparse.ArgumentParser(prog="tally", description="Named counters.")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add to a counter (creating it at 0)")
    add.add_argument("name")
    add.add_argument("amount", nargs="?", type=int, default=1)
    sub.add_parser("show", help="print every counter and the total")
    reset = sub.add_parser("reset", help="remove a counter")
    reset.add_argument("name")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    counters = store.load()
    if args.command == "add":
        if args.amount < 1:
            print("tally: amount must be at least 1", file=sys.stderr)
            return 2
        counters[args.name] = counters.get(args.name, 0) + args.amount
        store.save(counters)
        print("%s: %d" % (args.name, counters[args.name]))
    elif args.command == "show":
        print(report.render(counters))
    elif args.command == "reset":
        if args.name not in counters:
            print("tally: no counter named %s" % args.name, file=sys.stderr)
            return 1
        del counters[args.name]
        store.save(counters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
