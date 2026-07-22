from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path


class RuntimeAssetStore:
    def __init__(self, sounds_dir: Path, images_dir: Path) -> None:
        self.sounds_dir = sounds_dir
        self.images_dir = images_dir

    def copy_sound(self, path_text: str, pad_id: str) -> Path:
        return self._copy_asset(path_text, self.sounds_dir, pad_id)

    def copy_image(self, path_text: str, pad_id: str) -> Path:
        return self._copy_asset(path_text, self.images_dir, pad_id)

    def copy_sound_pack(
        self,
        source_dir_text: str,
        pad_id: str,
        source_files: Iterable[Path],
    ) -> Path:
        source_dir = Path(source_dir_text).expanduser()
        if not source_dir.is_dir():
            raise FileNotFoundError(source_dir_text)
        source_dir = source_dir.resolve()
        safe_pad_id = self._safe_component(pad_id, "pad")
        managed_pad_dir = self.sounds_dir.resolve(strict=False) / safe_pad_id

        entries: list[tuple[Path, str]] = []
        for source_file in source_files:
            resolved = Path(source_file).resolve()
            if not resolved.is_file():
                raise FileNotFoundError(str(source_file))
            try:
                logical_name = resolved.relative_to(source_dir).as_posix()
            except ValueError:
                logical_name = resolved.name
            entries.append((resolved, logical_name))
        if source_dir.parent == managed_pad_dir and source_dir.name.startswith("pack-"):
            requested_files = {source for source, _logical_name in entries}
            existing_entries = list(source_dir.iterdir())
            if all(path.is_file() and not path.is_symlink() for path in existing_entries):
                existing_files = {path.resolve() for path in existing_entries}
                if requested_files == existing_files:
                    return source_dir
        return self._copy_sound_pack_entries(entries, pad_id)

    def copy_sound_pack_files(self, source_files: Iterable[Path], pad_id: str) -> Path:
        entries = []
        for source_file in source_files:
            resolved = Path(source_file).resolve()
            if not resolved.is_file():
                raise FileNotFoundError(str(source_file))
            entries.append((resolved, resolved.name))
        return self._copy_sound_pack_entries(entries, pad_id)

    def is_managed_sound(self, path_text: str) -> bool:
        return self._is_inside(Path(path_text), self.sounds_dir)

    def is_managed_image(self, path_text: str) -> bool:
        return self._is_inside(Path(path_text), self.images_dir)

    def is_managed_asset(self, path_text: str) -> bool:
        if not path_text:
            return False
        path = Path(path_text)
        return self._is_inside(path, self.sounds_dir) or self._is_inside(path, self.images_dir)

    def managed_asset_files(self) -> list[Path]:
        files: list[Path] = []
        for directory in (self.sounds_dir, self.images_dir):
            if not directory.is_dir():
                continue
            files.extend(path for path in directory.rglob("*") if path.is_file())
        return files

    def remove_if_managed(self, path_text: str) -> bool:
        if not path_text or not self.is_managed_asset(path_text):
            return False

        path = Path(path_text).resolve(strict=False)
        roots = {self.sounds_dir.resolve(strict=False), self.images_dir.resolve(strict=False)}
        if path in roots or not path.exists():
            return False

        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        self._prune_empty_parents(path.parent, roots)
        return True

    def _copy_sound_pack_entries(self, entries: list[tuple[Path, str]], pad_id: str) -> Path:
        if not entries:
            raise ValueError("A sound pack must contain at least one sound file.")

        normalized_entries = sorted(
            entries,
            key=lambda entry: (entry[1].casefold(), str(entry[0]).casefold()),
        )
        prepared_entries: list[tuple[Path, str, str]] = []
        used_names: set[str] = set()
        pack_digest = hashlib.sha256()
        for source, logical_name in normalized_entries:
            content_digest = self._content_digest(source)
            destination_name = self._unique_pack_filename(
                source,
                content_digest,
                used_names,
            )
            used_names.add(destination_name.casefold())
            prepared_entries.append((source, destination_name, content_digest))
            pack_digest.update(logical_name.casefold().encode("utf-8", errors="surrogatepass"))
            pack_digest.update(b"\0")
            pack_digest.update(content_digest.encode("ascii"))
            pack_digest.update(b"\0")

        safe_pad_id = self._safe_component(pad_id, "pad")
        pad_dir = self.sounds_dir / safe_pad_id
        destination = pad_dir / f"pack-{pack_digest.hexdigest()[:12]}"
        if self._pack_matches(destination, prepared_entries):
            return destination.resolve()

        pad_dir.mkdir(parents=True, exist_ok=True)
        temporary = pad_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        backup = pad_dir / f".{destination.name}.{uuid.uuid4().hex}.backup"
        committed = False
        try:
            temporary.mkdir()
            for source, destination_name, _digest in prepared_entries:
                shutil.copy2(source, temporary / destination_name)

            if destination.exists():
                os.replace(destination, backup)
            try:
                os.replace(temporary, destination)
            except Exception:
                if backup.exists() and not destination.exists():
                    os.replace(backup, destination)
                raise
            committed = True
        finally:
            if temporary.exists():
                self._remove_path(temporary)
            if committed and (backup.exists() or backup.is_symlink()):
                self._remove_path(backup)
        return destination.resolve()

    @classmethod
    def _unique_pack_filename(
        cls,
        source: Path,
        content_digest: str,
        used_names: set[str],
    ) -> str:
        safe_stem = cls._safe_component(source.stem, "sound")[:64]
        suffix = source.suffix.lower()
        base = f"{safe_stem}-{content_digest}"
        candidate = f"{base}{suffix}"
        counter = 2
        while candidate.casefold() in used_names:
            candidate = f"{base}-{counter}{suffix}"
            counter += 1
        return candidate

    @classmethod
    def _pack_matches(
        cls,
        destination: Path,
        entries: list[tuple[Path, str, str]],
    ) -> bool:
        if not destination.is_dir() or destination.is_symlink():
            return False
        expected = {name.casefold(): digest for _source, name, digest in entries}
        contents = list(destination.iterdir())
        if any(not path.is_file() or path.is_symlink() for path in contents):
            return False
        actual = contents
        if len(actual) != len(expected):
            return False
        try:
            return all(
                path.name.casefold() in expected
                and cls._content_digest(path) == expected[path.name.casefold()]
                for path in actual
            )
        except OSError:
            return False

    @staticmethod
    def _prune_empty_parents(directory: Path, roots: set[Path]) -> None:
        current = directory.resolve(strict=False)
        while current not in roots and any(RuntimeAssetStore._is_inside(current, root) for root in roots):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def _copy_asset(self, path_text: str, target_dir: Path, pad_id: str) -> Path:
        source = Path(path_text).expanduser()
        if not source.is_file():
            raise FileNotFoundError(path_text)

        source = source.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_pad_id = self._safe_component(pad_id, "pad")
        target_root = target_dir.resolve(strict=False)
        source_is_owned_by_pad = (
            source.parent == target_root
            and source.name.casefold().startswith(f"{safe_pad_id}-".casefold())
        )
        if source_is_owned_by_pad:
            return source

        source_digest = self._content_digest(source)
        destination = target_dir / self._managed_filename(source, pad_id, source_digest)
        if destination.exists():
            try:
                if (
                    destination.is_file()
                    and destination.stat().st_size == source.stat().st_size
                    and self._content_digest(destination) == source_digest
                ):
                    return destination.resolve()
            except OSError:
                pass

        temporary = target_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination.resolve()

    @staticmethod
    def _managed_filename(source: Path, pad_id: str, digest: str | None = None) -> str:
        safe_stem = RuntimeAssetStore._safe_component(source.stem, "asset")
        safe_stem = safe_stem[:64]
        safe_pad_id = RuntimeAssetStore._safe_component(pad_id, "pad")
        content_digest = digest or RuntimeAssetStore._content_digest(source)
        return f"{safe_pad_id}-{safe_stem}-{content_digest}{source.suffix.lower()}"

    @staticmethod
    def _safe_component(value: str, fallback: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("._-") or fallback

    @staticmethod
    def _content_digest(source: Path) -> str:
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:12]

    @staticmethod
    def _is_inside(path: Path, directory: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(directory.resolve(strict=False))
            return True
        except ValueError:
            return False
