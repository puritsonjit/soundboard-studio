# Git Workflow

## Branches

The public `main` branch contains release-ready source. Create a focused branch
for each change and open a pull request when collaboration or review is needed.

```powershell
git switch -c feature/short-description
```

Do not rewrite published release tags or force-push shared branches.

## Before Committing

Run the project verification commands:

```powershell
env\pyqt6\Scripts\python.exe -m unittest discover -s tests -v
env\pyqt6\Scripts\ruff.exe check src\python_qt6 tests tools
env\pyqt6\Scripts\python.exe -m compileall -q src\python_qt6 tests tools
```

Review `git status` and `git diff` before staging. Do not commit generated EXEs,
runtime assets, profiles, downloaded helpers, driver packages, credentials,
private keys, screenshots, or local environments.

## Commit And Review

Use a short commit subject that explains the purpose of the change:

```powershell
git add path\to\changed-file
git commit -m "docs: clarify release verification"
```

External pull requests require maintainer review. Build, dependency, network,
release, and signing changes receive additional supply-chain review.

## Recovery

Inspect a previous file without changing the working tree:

```powershell
git show COMMIT_ID:path/to/file.py
```

Restore one tracked file from the current commit only after confirming that its
local edits are no longer needed:

```powershell
git restore path\to\file.py
```

Independent local or remote Git mirrors may be used for recovery, but generated
runtime data must be backed up separately because it is intentionally untracked.
