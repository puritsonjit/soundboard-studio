from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import soundfile as sf


MAX_SOUND_DURATION_SECONDS = 15.0
SUPPORTED_AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".ogg", ".flac"})


class SoundValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RejectedSoundPackFile:
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class SoundPackScanResult:
    directory: Path
    accepted: tuple[Path, ...]
    rejected: tuple[RejectedSoundPackFile, ...]


def validate_sound_duration(path_text: str | Path, *, max_seconds: float = MAX_SOUND_DURATION_SECONDS) -> float:
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(str(path))

    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise SoundValidationError(
            f"Could not read this audio file, so it was not added: {path.name}"
        ) from exc

    return validate_sound_frames(
        int(info.frames or 0),
        int(info.samplerate or 0),
        path.name,
        max_seconds=max_seconds,
    )


def sound_pack_files(path_text: str | Path) -> list[Path]:
    if isinstance(path_text, str) and not path_text.strip():
        return []
    directory = Path(path_text).expanduser()
    if not directory.is_dir():
        return []
    return sorted(
        (
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )


def validate_sound_pack(path_text: str | Path) -> list[Path]:
    result = scan_sound_pack(path_text)
    if result.rejected:
        details = "; ".join(
            f"{rejected.path.name}: {rejected.reason}" for rejected in result.rejected[:3]
        )
        if len(result.rejected) > 3:
            details += f"; and {len(result.rejected) - 3} more"
        raise SoundValidationError(
            "Sound pack not added. Every sound must be readable and 15 seconds or shorter. "
            + details
        )
    return list(result.accepted)


def scan_sound_pack(path_text: str | Path) -> SoundPackScanResult:
    if isinstance(path_text, str) and not path_text.strip():
        raise SoundValidationError("Choose a folder for Sound pack mode.")
    directory = Path(path_text).expanduser()
    if not directory.exists():
        raise FileNotFoundError(str(directory))
    if not directory.is_dir():
        raise SoundValidationError("Choose a folder for Sound pack mode.")

    files = sound_pack_files(directory)
    if not files:
        supported = ", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS))
        raise SoundValidationError(
            f"No supported audio files were found in {directory.name}. Supported types: {supported}."
        )

    accepted: list[Path] = []
    rejected: list[RejectedSoundPackFile] = []
    for path in files:
        try:
            validate_sound_duration(path)
        except (FileNotFoundError, SoundValidationError) as exc:
            rejected.append(RejectedSoundPackFile(path=path, reason=str(exc)))
        else:
            accepted.append(path)
    return SoundPackScanResult(
        directory=directory.resolve(),
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )


def validate_sound_frames(
    frame_count: int,
    sample_rate: int,
    file_name: str,
    *,
    max_seconds: float = MAX_SOUND_DURATION_SECONDS,
) -> float:
    frames = int(frame_count)
    sample_rate = int(sample_rate)
    if sample_rate <= 0 or frames <= 0:
        raise SoundValidationError(
            f"This audio file is empty or unreadable, so it was not added: {file_name}"
        )

    duration = frames / sample_rate
    if duration > max_seconds:
        raise SoundValidationError(
            f"Sound files must be {max_seconds:g} seconds or shorter. "
            f"{file_name} is {_format_seconds(duration)} seconds, so it was not added."
        )
    return duration


def _format_seconds(seconds: float) -> str:
    rounded = f"{seconds:.1f}"
    return rounded[:-2] if rounded.endswith(".0") else rounded
