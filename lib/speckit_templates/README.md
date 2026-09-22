# Spec Kit templates (vendored)

The five templates `lib/reverse.py lint` derives its required headings from when
the linted source has no `.specify/templates/` of its own. Copied unchanged from
[github/spec-kit](https://github.com/github/spec-kit) `templates/` at commit
`4a7341a9` (2026-09-04), under its MIT license (`LICENSE`, beside this file).

The linter reads them at run time; nothing in the code lists their headings. To
follow a newer Spec Kit, copy the new files over these and run
`python3 -m pytest lib/test_reverse.py`.
