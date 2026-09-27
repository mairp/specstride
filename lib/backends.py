#!/usr/bin/env python3
"""The single backend registry for Specstride's proposer and critic roles.

Every other backend list in the repository is rendered from this module or
checked against it by lib/test_backend_registry.py; no backend list may be
hand-copied anywhere else. This module owns backend NAMES and SPELLINGS only —
not defaults, not models, not any other runtime setting (those stay with the
code that uses them). Later items (SH-3/SH-4/SH-5) add optional per-entry
fields (stream, preflight, version) to these records without changing this
module's API or output.

Stdlib only; paths resolve from __file__, never from the CWD, and nothing here
reads .env or SPECSTRIDE_* variables.

Adding a backend: append its entry to REGISTRY here, add the matching arm to
that role's dispatch (run_agent() in proposer.sh and/or critic_call() in
lib/critic.py), update the "Proposer backends:" / "Critic backends:" lines in
.env.example, README.md, wiki/Configuration.md and — for a critic backend — the
lib/critic.py module docstring, then run lib/test_backend_registry.py: its
dispatch-parity and documentation-list checks name whatever is still missing.
"""
import argparse
import sys
from dataclasses import dataclass

ROLES = ("proposer", "critic")


@dataclass(frozen=True)
class Qualifier:
    """How a backend's optional/required qualifier is spelled for one role."""
    form: str  # "none" | "optional" | "required"
    label: str = None  # None iff form == "none"


@dataclass(frozen=True)
class Backend:
    """One backend: its name and, per role served, its qualifier spelling."""
    name: str
    roles: tuple  # ordered (role, Qualifier) pairs

    def qualifier(self, role):
        return dict(self.roles)[role]


# Registry order is display order; byte-stable output depends on it.
REGISTRY = (
    Backend("dsh", (("proposer", Qualifier("optional", "provider/model")),
                    ("critic", Qualifier("optional", "provider/model")))),
    Backend("claude", (("proposer", Qualifier("none")), ("critic", Qualifier("none")))),
    Backend("codex", (("proposer", Qualifier("none")), ("critic", Qualifier("none")))),
    Backend("bebop", (("proposer", Qualifier("optional", "name")),
                      ("critic", Qualifier("none")))),
    Backend("prime", (("proposer", Qualifier("optional", "variant")),
                      ("critic", Qualifier("optional", "variant")))),
)


def _check_role(role):
    if role not in ROLES:
        raise ValueError("unknown backend role: %r" % (role,))


def names(role):
    """Names of the backends serving `role`, in registry order."""
    _check_role(role)
    return tuple(b.name for b in REGISTRY if role in dict(b.roles))


def spelling(name, role):
    """The spelling of `name` for `role`; KeyError if it doesn't serve the role."""
    _check_role(role)
    for b in REGISTRY:
        if b.name == name:
            q = b.qualifier(role)
            if q.form == "none":
                return b.name
            if q.form == "optional":
                return "%s[:%s]" % (b.name, q.label)
            return "%s:%s" % (b.name, q.label)
    raise KeyError(name)


def display(role):
    """One line: the spellings serving `role`, joined with " | "."""
    _check_role(role)
    return " | ".join(spelling(n, role) for n in names(role))


def _main(argv):
    parser = argparse.ArgumentParser(prog="backends.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True, metavar="{names,display}")
    for mode in ("names", "display"):
        p = sub.add_parser(mode)
        p.add_argument("--role", required=True, choices=ROLES)
    args = parser.parse_args(argv)
    if args.mode == "names":
        for n in names(args.role):
            print(n)
    else:
        print(display(args.role))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
