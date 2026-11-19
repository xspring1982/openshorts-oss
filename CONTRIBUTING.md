# Contributing

Issues and pull requests are welcome. This is a fork of
[mutonby/openshorts](https://github.com/mutonby/openshorts). A fix that belongs
in OpenShorts itself (anything not specific to this fork's additions) is best
sent upstream first; we merge upstream regularly.

## Ground rules

- Never add upstream's `cloud/` directory or code derived from it. It is not
  MIT-licensed. CI fails if a `cloud/` path appears.
- Keep fork changes small and easy to merge with upstream: put new behaviour
  in new modules, mark edits to upstream files with a `Fork:` comment, and
  leave upstream's defaults unchanged unless there is a strong reason.
- Every fork change comes with tests in `tests/test_fork_*.py`. They must run
  without a GPU, network access or API keys.
- No secrets, personal data, server names or internal URLs in code, tests,
  docs or commit messages.

## Running the tests

```bash
pip install pytest pillow httpx pydantic sqlalchemy greenlet numpy opencv-python-headless \
  python-dotenv google-genai fastapi boto3 PyJWT email-validator python-multipart asyncpg
pytest tests/ -q
```

Tests that import `main.py` need the full CPU stack (`requirements-cpu.lock`
installed with `--no-deps`, plus `torch==2.11.0`); without it they are
skipped, as in CI.

## Syncing with upstream

```bash
scripts/sync-upstream.sh     # fetch upstream, drop cloud/, merge into this branch
pytest tests/ -q
```

The script rewrites upstream's history with git-filter-repo to remove
`cloud/`. The rewrite is deterministic, so each sync merges only the new
commits. On a conflict, keep the `Fork:` changes, adapt them to upstream's
new code, and run the tests. Note in `CHANGELOG.md` anything that changed for
users.

## Releases

Bump the version in `CHANGELOG.md`, tag `vX.Y.Z` on `main`, and publish the
GitHub release with the changelog section.
