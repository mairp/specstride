"""Summaries of the counters."""


def total(counters):
    """The sum of every counter."""
    return sum(counters.values())


def render(counters):
    """One `name: value` line per counter, sorted by name, then the total."""
    lines = ["%s: %d" % (name, counters[name]) for name in sorted(counters)]
    lines.append("total: %d" % total(counters))
    return "\n".join(lines)
