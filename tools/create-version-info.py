from __future__ import annotations

import argparse
from pathlib import Path
import re


VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def create_version_info(version_file: Path, output_file: Path) -> None:
    version = version_file.read_text(encoding="ascii").strip()
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise ValueError("VERSION must contain exactly three numeric components, such as 1.0.0.")

    components = tuple(int(value) for value in match.groups())
    if any(value > 65535 for value in components):
        raise ValueError("Every Windows version component must be between 0 and 65535.")

    file_version = (*components, 0)
    dotted_file_version = ".".join(str(value) for value in file_version)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_version!r},
    prodvers={file_version!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Soundboard Studio contributors'),
          StringStruct('FileDescription', 'Soundboard Studio'),
          StringStruct('FileVersion', '{dotted_file_version}'),
          StringStruct('InternalName', 'SoundboardQt6'),
          StringStruct('LegalCopyright', 'Copyright (C) 2026 Soundboard Studio contributors'),
          StringStruct('OriginalFilename', 'SoundboardQt6.exe'),
          StringStruct('ProductName', 'Soundboard Studio'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Create PyInstaller Windows version metadata.")
    parser.add_argument("--version-file", type=Path, default=repo_root / "VERSION")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_version_info(args.version_file, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
