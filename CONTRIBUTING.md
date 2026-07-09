# Contributing

Thanks for considering a contribution. This is a solo-maintained project, so
please keep changes focused and open an issue first for anything non-trivial
(new skills, hook behavior changes, breaking changes to `install.sh`/
`doctor.sh`) before investing time in a PR.

## Development setup

```bash
git clone <this-repo>
cd claude-core
python3 -m pip install pytest ruff
```

No package manager lock-in — this repo ships no installable Python package
(no build target), just skills, a hook, and shell installers. `pyproject.toml`
exists only to configure `python-semantic-release`.

Run the test suite:

```bash
python3 -m pytest tests/ -v
```

Lint:

```bash
ruff check lib tests
```

Both must be clean before a PR is reviewed. CI (`portability.yml`) runs the
same checks on a matrix of ubuntu/macos × Python 3.11/3.13.

## Commit messages: Conventional Commits are required

This project's version, `CHANGELOG.md`, and GitHub Releases are generated
automatically from commit history via
[semantic-release](https://python-semantic-release.readthedocs.io/), so every
commit on `main` must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

Common types: `feat` (new feature → minor version bump), `fix` (bug fix →
patch bump), `docs`, `refactor`, `test`, `chore`. A breaking change is
signalled with `!` after the type/scope (`feat!:`) or a `BREAKING CHANGE:`
footer, and triggers a major version bump.

PRs are typically squash-merged, so the **squash-merge commit message** is
what matters — make sure it follows the convention even if your in-branch
commits don't.

## Developer Certificate of Origin (DCO)

By contributing, you certify that you wrote the contribution yourself (or have
the right to submit it) under the project's [MIT license](LICENSE) — the
standard [Developer Certificate of Origin](https://developercertificate.org/).

Sign off your commits with `git commit -s` (adds a `Signed-off-by:` trailer).
PRs with unsigned commits will be asked to amend before merge.

## Reporting bugs / requesting features

- **Bugs:** open an issue using the Bug Report template — please note which
  surface is affected (the `cost-discipline.py` hook, `install.sh`/
  `doctor.sh`/`bootstrap.sh`, or a specific skill under `skills/`).
- **Ideas / feature requests:** start a [Discussion](../../discussions)
  rather than an issue, so we can talk through scope before committing to it.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
