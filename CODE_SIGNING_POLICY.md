# Code Signing Policy

## Current Status

The project has been accepted into the SignPath Foundation open-source program.
The GitHub and SignPath signing integration is being completed. Existing
v1.0.0 preview artifacts remain unsigned and must not claim a SignPath signature.
An artifact is described as signed only after its Authenticode signature and
timestamp have been verified successfully.

Free code signing provided by [SignPath.io](https://signpath.io/), certificate
by [SignPath Foundation](https://signpath.org/).

## Roles

- Authors and committers: [puritsonjit](https://github.com/puritsonjit) and
  collaborators granted write access to the
  [Soundboard Studio repository](https://github.com/puritsonjit/soundboard-studio).
- Reviewers: [puritsonjit](https://github.com/puritsonjit) and repository
  collaborators granted review access.
- Signing approver: [puritsonjit](https://github.com/puritsonjit).
- External contributions: must be reviewed by a committer before merging.

All maintainers with write, review, release, or signing access must use
multi-factor authentication for both the source repository and SignPath.

## Build And Signing Rules

- Official binaries must be produced by `.github/workflows/windows-build.yml`
  from the public repository.
- Release versions must come from `VERSION` and match the Windows executable
  metadata.
- The SignPath signing request must reference the CI-produced artifact and the
  exact source commit.
- Every signing request requires manual approval by the signing approver.
- The signing key remains in SignPath's hardware security module and is never
  copied into this repository or onto a maintainer computer.
- Unsigned previews must be labeled clearly and must not replace a signed asset.
- A signed release is promoted only after Authenticode verification, malware
  scanning, and SHA-256 checksum generation succeed.

## Privacy Statement

Soundboard Studio transfers no information to the project maintainers. Network
connections occur only for functions requested by the user and are documented
in [PRIVACY.md](PRIVACY.md).
