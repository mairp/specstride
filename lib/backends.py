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
    stream: str | None = None  # canonical stream format (SH-3); absent = raw text
    # Optional throwaway config-home declaration, read only by
    # lib/backend_overlay.py: {"vars": (...), "seeds": (...)} or None. No
    # shipped entry sets it; that module defines what each field means.
    overlay: dict = None

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


def find(name):
    """The entry named `name`, or None (SH-3: bare names only, no qualifier)."""
    for b in REGISTRY:
        if b.name == name:
            return b
    return None


def stream_value(name):
    """The entry's `stream` value; KeyError for an unknown name (SH-3)."""
    entry = find(name)
    if entry is None:
        raise KeyError(name)
    return entry.stream


def invocation_model_value(spec, model):
    """The research R5 invocation model for SPEC (NAME[:QUALIFIER]) and --model.

    A qualifier counts as a model only when SH-1 labels the proposer qualifier
    `provider/model` (today only dsh); `variant`/`name` labels are not model
    ids. An explicit --model beats the qualifier unless both are given, in
    which case picking either would be a guess (XI) — so neither is reported.
    Returns None when there is no model to report; KeyError for an unknown name.
    """
    bare, _, qualifier = spec.partition(":")
    entry = find(bare)
    if entry is None:
        raise KeyError(bare)
    if model:
        return None if qualifier else model
    q = entry.qualifier("proposer") if "proposer" in dict(entry.roles) else None
    if (qualifier and q is not None and q.form != "none"
            and q.label == "provider/model"):
        return qualifier
    return None


def _main(argv):
    parser = argparse.ArgumentParser(prog="backends.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True, metavar="{names,display,stream,invocation-model}")
    for mode in ("names", "display"):
        p = sub.add_parser(mode)
        p.add_argument("--role", required=True, choices=ROLES)
    # SH-3 modes: read the per-entry stream format / invocation model. They take
    # their own --backend (and --model); --role does not apply to them.
    p = sub.add_parser("stream")
    p.add_argument("--backend", required=True)
    p = sub.add_parser("invocation-model")
    p.add_argument("--backend", required=True)
    p.add_argument("--model", default="")
    args = parser.parse_args(argv)
    if args.mode == "names":
        for n in names(args.role):
            print(n)
    elif args.mode == "display":
        print(display(args.role))
    elif args.mode == "stream":
        try:
            value = stream_value(args.backend)
        except KeyError:
            return 1
        if value is not None:
            print(value)
        return 0
    else:
        try:
            value = invocation_model_value(args.backend, args.model)
        except KeyError:
            return 1
        if value is not None:
            print(value)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
