#!/bin/bash
# Release script: bump pyproject.toml version, update CHANGELOG, tag, push.
#
# Usage:
#   .pi/skills/release/scripts/release.sh <version>
#
# Example:
#   .pi/skills/release/scripts/release.sh 1.2.3
#
# Steps:
#   1. Summarize recent commits into a changelog block.
#   2. Move [Unreleased] to a dated version header.
#   3. Bump pyproject.toml [project].version.
#   4. Regenerate uv.lock (uv lock).
#   5. Lint + test.
#   6. Amend commit with changelog + version bump.
#   7. Tag v<X.Y.Z> and push (main + tag).

set -euo pipefail

VERSION="$1"
TAG="v${VERSION}"

# 1. Summarize recent commits into a changelog block
echo "Generating changelog from recent commits..."
COMMIT_LOG=$(git log --oneline --since="1 week ago" 2>/dev/null || git log --oneline -20)

# 2. Move [Unreleased] to a dated version header
echo "Updating CHANGELOG.md..."
sed -i.bak "s/## \[Unreleased\]/## [${VERSION}] - $(date +%Y-%m-%d)/" CHANGELOG.md
rm CHANGELOG.md.bak

# 3. Bump pyproject.toml [project].version
echo "Bumping pyproject.toml version to ${VERSION}..."
sed -i.bak "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
rm pyproject.toml.bak

# 4. Regenerate uv.lock
echo "Regenerating uv.lock..."
uv lock

# 5. Lint + test
echo "Running lint + test..."
make lint
make test

# 6. Amend commit
echo "Amending commit..."
git add CHANGELOG.md pyproject.toml uv.lock
git commit --amend -m "release: v${VERSION}" --no-edit

# 7. Tag and push
echo "Creating tag ${TAG}..."
git tag -a "${TAG}" -m "Release v${VERSION}"
echo "Pushing to origin..."
git push origin main
git push origin "${TAG}"

echo "Release v${VERSION} complete!"
