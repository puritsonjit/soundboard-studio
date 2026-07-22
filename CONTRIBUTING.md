# Contributing

Soundboard Studio is licensed under GPLv3. By submitting a contribution, you
agree that it may be distributed under the same license.

## Development

Use Python 3.12 and the project-local environment described in
[Development](docs/DEVELOPMENT.md). Before opening a pull request, run:

```powershell
env\pyqt6\Scripts\python.exe -m unittest discover -s tests -v
env\pyqt6\Scripts\ruff.exe check src\python_qt6 tests tools
env\pyqt6\Scripts\python.exe -m compileall -q src\python_qt6 tests tools
```

## Pull Requests

- Keep changes focused and explain user-visible behavior.
- Add regression tests for bug fixes and new behavior when practical.
- Do not commit pad sounds, personal images, profiles, credentials, private
  signing keys, downloaded executables, or generated build output.
- External pull requests require maintainer review before merging.
- Changes to build, release, dependency, networking, or signing files receive
  additional supply-chain review.

Use GitHub's private vulnerability reporting instead of a pull request for an
unpatched security issue.
