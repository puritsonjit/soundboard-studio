from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import shutil


NOTICE_NAMES = ("license", "licence", "copying", "notice", "copyright", "authors")


def safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "unknown"


def is_notice_file(relative_path: Path) -> bool:
    name = relative_path.name.casefold()
    return any(name.startswith(prefix) for prefix in NOTICE_NAMES) or any(
        part.casefold() in {"license", "licenses"} for part in relative_path.parts
    )


def collect_notices(output_dir: Path, project_license: Path) -> int:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    shutil.copy2(project_license, output_dir / "Soundboard-Studio-GPL-3.0.txt")

    copied = 1
    for distribution in sorted(
        metadata.distributions(), key=lambda item: (item.metadata.get("Name") or "").casefold()
    ):
        name = distribution.metadata.get("Name") or "unknown"
        package_dir = output_dir / safe_component(f"{name}-{distribution.version}")
        for entry in distribution.files or ():
            relative = Path(str(entry))
            if not is_notice_file(relative):
                continue
            source = Path(distribution.locate_file(entry)).resolve()
            if not source.is_file():
                continue
            destination_parts = [
                safe_component(part) for part in relative.parts if part not in {".", ".."}
            ]
            destination = package_dir.joinpath(*destination_parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
    return copied


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Collect dependency license and notice files.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-license", type=Path, default=repo_root / "LICENSE")
    args = parser.parse_args()
    copied = collect_notices(args.output, args.project_license)
    print(f"Collected {copied} project and dependency license files in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
