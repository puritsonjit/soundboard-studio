from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppPaths:
    app_dir: Path
    data_dir: Path
    runtime_dir: Path
    sounds_dir: Path
    images_dir: Path
    config_path: Path
    resources_dir: Path

    @classmethod
    def build(cls) -> "AppPaths":
        if getattr(sys, "frozen", False):
            app_dir = Path(sys.executable).resolve().parent
            resources_dir = Path(getattr(sys, "_MEIPASS")).resolve() / "soundboard_qt6"
        else:
            app_dir = Path(__file__).resolve().parents[3]
            resources_dir = Path(__file__).resolve().parent

        data_dir = app_dir / "data"
        runtime_dir = app_dir / "runtime"
        sounds_dir = runtime_dir / "sounds"
        images_dir = runtime_dir / "images"
        config_path = data_dir / "soundboard_config.json"

        paths = cls(
            app_dir=app_dir,
            data_dir=data_dir,
            runtime_dir=runtime_dir,
            sounds_dir=sounds_dir,
            images_dir=images_dir,
            config_path=config_path,
            resources_dir=resources_dir,
        )
        paths.ensure()
        return paths

    @property
    def web_dir(self) -> Path:
        return self.resources_dir / "web"

    def ensure(self) -> None:
        for directory in (self.data_dir, self.runtime_dir, self.sounds_dir, self.images_dir):
            directory.mkdir(parents=True, exist_ok=True)
