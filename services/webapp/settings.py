"""Authenticated ROS bounds edits preserve the shared physical configuration."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

from services.spacecraft_config import (get_sensor_limits, load_settings,
    resolve_settings_path, settings_sha256, validate_settings)


class SettingsError(ValueError):
    pass


_WRITER_LOCK = threading.RLock()


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.explicit = path is not None or bool(os.environ.get("PADO_SPACECRAFT_CONFIG"))
        self.path = resolve_settings_path(path, writable=True)

    def _read(self) -> dict:
        try:
            # Native development can read the template before its first edit.
            source = self.path if self.explicit or self.path.exists() else None
            return load_settings(source)
        except (OSError, ValueError) as error:
            raise SettingsError("settings_unavailable") from error

    @staticmethod
    def _view(document: dict) -> dict:
        limits = get_sensor_limits(document)["sensors"]
        return {"schema": "pado.settings-view.v1", "sha256": settings_sha256(document),
                "file_name": "spacecraft.json", "sensors": [
                    {"key": key, **rule} for key, rule in limits.items()],
                "power": copy.deepcopy(document["power"])}

    def view(self) -> dict:
        return self._view(self._read())

    def limits(self) -> dict:
        return get_sensor_limits(self._read())

    @contextmanager
    def _locked(self):
        with _WRITER_LOCK:
            parent = self.path.parent
            if parent.is_symlink() or self.path.is_symlink():
                raise SettingsError("settings_unavailable")
            parent.mkdir(parents=True, exist_ok=True)
            lock_path = parent / ".spacecraft-settings.lock"
            if lock_path.is_symlink():
                raise SettingsError("settings_unavailable")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o664)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise SettingsError("settings_unavailable")
                if os.name == "posix" and info.st_uid == os.geteuid():
                    os.fchmod(descriptor, 0o664)
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                os.close(descriptor)

    def save_limits(self, expected_sha256: object, changes: object) -> dict:
        if (not isinstance(expected_sha256, str) or
                not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or
                not isinstance(changes, dict) or not 1 <= len(changes) <= 64):
            raise SettingsError("invalid_settings")
        try:
            with self._locked():
                document = self._read()
                if settings_sha256(document) != expected_sha256:
                    raise SettingsError("settings_conflict")
                updated = copy.deepcopy(document)
                configured = updated["sensor_limits"]
                for key, values in changes.items():
                    if (key not in configured or not isinstance(values, dict) or
                            set(values) != {"lower", "upper"} or
                            any(type(values[name]) not in (int, float) or
                                not math.isfinite(values[name]) for name in ("lower", "upper")) or
                            values["lower"] >= values["upper"]):
                        raise SettingsError("invalid_settings")
                    configured[key].update(lower=values["lower"], upper=values["upper"])
                updated = validate_settings(updated)
                import json
                body = (json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".spacecraft-", suffix=".tmp", delete=False) as target:
                        temporary = Path(target.name)
                        target.write(body)
                        target.flush()
                        os.fsync(target.fileno())
                    os.chmod(temporary, 0o664)
                    os.replace(temporary, self.path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                return self._view(updated)
        except SettingsError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SettingsError("settings_unavailable") from error
