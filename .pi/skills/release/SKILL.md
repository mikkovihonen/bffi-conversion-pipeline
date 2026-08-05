# Release Skill

**Purpose:** Drive the release process for the BFFI conversion pipeline.

**When to use:** When the operator wants to create a new release (e.g., after a set of bug fixes, feature additions, or mapping doc updates).

## How to use

1. **Summarize recent changes.** Read the recent commit log and identify what changed since the last release.
2. **Update CHANGELOG.md.** Move `[Unreleased]` to a dated version header. Add entries for the new version.
3. **Bump version.** Update `pyproject.toml` `[project].version` to the new semantic version (e.g., `1.2.3`).
4. **Regenerate uv.lock.** Run `uv lock` to update the lockfile.
5. **Validate.** Run `make lint && make test` to ensure everything still passes.
6. **Commit.** Amend the commit with the changelog + version bump.
7. **Tag.** Create an annotated tag `v<X.Y.Z>` with a descriptive message.
8. **Push.** Push `main` and the tag to origin.

## Steps

```bash
# 1. Check recent commits
git log --oneline -20

# 2. Edit CHANGELOG.md manually or via the release script
#    Move [Unreleased] to the new version, add entries

# 3. Bump version in pyproject.toml
sed -i 's/^version = ".*/version = "1.2.3"/' pyproject.toml

# 4. Regenerate uv.lock
uv lock

# 5. Validate
make lint
make test

# 6. Amend commit
git add CHANGELOG.md pyproject.toml uv.lock
git commit --amend -m "release: v1.2.3" --no-edit

# 7. Tag
git tag -a v1.2.3 -m "Release v1.2.3"

# 8. Push
git push origin main
git push origin v1.2.3
```

## Automation

For a fully automated release, use the `release.sh` script:

```bash
.pi/skills/release/scripts/release.sh 1.2.3
```

This runs all steps in sequence, including validation.

## Versioning convention

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html):

- **MAJOR** (1.0.0 → 2.0.0): Breaking changes
- **MINOR** (1.1.0 → 1.2.0): Backwards-compatible feature additions
- **PATCH** (1.1.1 → 1.1.2): Bug fixes, no new features

0.x.y is pre-release. 1.x.y is stable.

## Sync points

Two places stay in sync:
- Git tag (`v<X.Y.Z>`)
- `pyproject.toml` `[project].version`

No `schema_version` to sync (no seeded config template).

## Out of scope

- Docker image publishing (no images to build)
- PyPI publishing (this is a pro-bono tool, distributed via `uv sync` from git)
