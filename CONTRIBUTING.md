# Contributing

## Commits and releases

Use [Conventional Commits](https://www.conventionalcommits.org/) for commits that
land on `main`. The semantic release workflow evaluates every commit since the
latest `v*` tag after a merge:

- `fix:` and `perf:` create a patch release.
- `feat:` creates a minor release.
- `!` after the type or a `BREAKING CHANGE:` footer creates a breaking release.
- `docs:`, `test:`, `ci:`, `build:`, `chore:`, and `style:` do not release by themselves.

Examples:

```text
fix(ec): preserve existing project configuration
feat(ec): add Linear issue synchronization
feat(cli)!: remove the deprecated command alias
```

The workflow creates the version tag, publishes `spareparts-cli` to PyPI,
creates the GitHub release, and updates the Homebrew tap. Package versions are
derived from Git tags; do not edit a version string in source files.
