#!/usr/bin/env sh
# Bring upstream's new commits into this fork.
#
# Upstream's history is rewritten with git-filter-repo to drop cloud/ (not MIT)
# before it is merged. The rewrite is deterministic, so the same upstream
# commit always becomes the same rewritten commit, and each sync merges only
# what is new since the last one.
#
#   scripts/sync-upstream.sh            # fetch, rewrite, merge into the current branch
#
# Needs git-filter-repo (pip install git-filter-repo, or: uvx git-filter-repo).
set -eu
UPSTREAM_URL=${UPSTREAM_URL:-https://github.com/mutonby/openshorts.git}
BRANCH=${UPSTREAM_BRANCH:-main}
work=$(mktemp -d "${TMPDIR:-/tmp}/openshorts-upstream.XXXXXX")
trap 'rm -rf "$work"' EXIT

filter_repo() {
  if command -v git-filter-repo >/dev/null 2>&1; then git-filter-repo "$@"; else uvx git-filter-repo "$@"; fi
}

git clone --quiet --single-branch --branch "$BRANCH" "$UPSTREAM_URL" "$work/upstream"
( cd "$work/upstream" && filter_repo --path cloud/ --path docker-compose.cloud.yml --invert-paths --force )

git fetch --quiet "$work/upstream" "$BRANCH:refs/remotes/upstream-filtered/$BRANCH"
echo "Upstream (without cloud/) is at refs/remotes/upstream-filtered/$BRANCH."
echo "New upstream commits since the last sync:"
git log --oneline "HEAD..refs/remotes/upstream-filtered/$BRANCH" | head -50
git merge --no-ff --no-edit "refs/remotes/upstream-filtered/$BRANCH" || {
  echo "Merge conflicts: resolve them (keep the 'Fork:' changes), then run the tests." >&2
  exit 1
}
if git ls-files | grep -q '^cloud/'; then
  echo "cloud/ came back in this merge; it must not be committed." >&2
  exit 1
fi
echo "Merged. Now run: pytest tests/ -q"
