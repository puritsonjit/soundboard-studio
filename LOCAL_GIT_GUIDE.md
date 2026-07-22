# Local Git Guide

This project uses Git entirely on this computer. GitHub, an account, and an
internet connection are not required for normal source history.

## What is protected

Git tracks the Qt6 application source, web remote, tests, documentation, brand
assets, and development scripts. Generated EXEs, build folders, the local
Python environment, downloaded installer packages, runtime sounds/images,
personal configuration, screenshots, and manual backups remain on disk but are
ignored by Git.

## Save a development snapshot

From the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File tools\git-snapshot.ps1 -Message "Describe the completed change"
```

The command creates a commit and updates the `local-backup` mirror. Use
`-NoBackup` only when you intentionally do not want to update that mirror.

## Inspect history

```powershell
git status
git log --oneline --decorate -20
git diff
```

## Restore source safely

Restore one tracked file from the latest commit:

```powershell
git restore path\to\file.py
```

Inspect an older file without changing the current copy:

```powershell
git show COMMIT_ID:path/to/file.py
```

Create an isolated branch before a risky experiment:

```powershell
git switch -c experiment-name
```

Return to the stable branch:

```powershell
git switch main
```

## Local recovery mirror

The ignored bare mirror is stored at:

```text
Backup\local-git\Soundboard.git
```

It is configured as the `local-backup` remote. To rebuild a clean working copy,
run this from the parent directory and choose a different destination folder:

```powershell
git clone "Soundboard\Backup\local-git\Soundboard.git" "Soundboard-Recovered"
```

Copy the entire project folder, including the hidden `.git` directory and the
`Backup` directory, when moving the self-contained project to another drive or
computer.

## Recreate the ignored Python environment

On a computer with Python 3.12 and internet access:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup-python-env.ps1
```

Then run tests or create the EXE using the existing project scripts.
