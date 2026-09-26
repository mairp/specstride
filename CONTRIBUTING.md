# Contributing to Specstride

Thanks for your interest. Issues, discussions and pull requests are all welcome.

## Questions and ideas

Ask in [Discussions](https://github.com/mairp/specstride/discussions) first. Use Q&A for "how do I…",
Ideas for proposals. Open an issue when something is broken or a change is agreed.

## Reporting a bug

Open a [bug report](https://github.com/mairp/specstride/issues/new?template=bug_report.md) with:

- the `specstride` command you ran and the exit code (see **Exit codes** in the README);
- the proposer and critic backends (`claude`, `codex`, `bebop`, `dsh`, …);
- the relevant lines of the run's `events.jsonl`, with keys and hostnames removed.

## Pull requests

1. Fork, branch from `main`, keep the change focused.
2. Specstride is stdlib-only Python plus Bash: please don't add dependencies.
3. Run the checks CI runs:
   ```bash
   python3 -m pytest lib -q
   shellcheck -S error $(git ls-files "*.sh") specstride
   ```
4. Sign off your commits (`git commit -s`, [DCO](https://developercertificate.org/)).
5. Open the PR against `main`. CI must pass (`lint`, `test`); green PRs are merged automatically,
   label a PR `do-not-merge` to hold it.

By contributing you agree your work is licensed under the [Apache-2.0](LICENSE) license.
