"""Load and save the counter file."""
import json
import os

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".tally.json")


def store_path():
    """The counter file: $TALLY_FILE when set, else ~/.tally.json."""
    return os.environ.get("TALLY_FILE") or DEFAULT_PATH


def load(path=None):
    """Return the counters as a dict of name -> int. A missing file is empty."""
    path = path or store_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("counter file must hold a JSON object: %s" % path)
    return {str(name): int(value) for name, value in data.items()}


def save(counters, path=None):
    """Write the counters atomically: a temp file, then a rename."""
    path = path or store_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(counters, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)
