"""Validated, read-only configuration sources for registered Odoo instances."""
from __future__ import annotations

from configparser import ConfigParser, Error as ConfigError
from threading import RLock
import os
import tempfile
from pathlib import Path
from typing import Protocol


class ConfigurationError(Exception):
    """Raised when an instance configuration cannot be read safely.

    The one-argument form is used inside the configuration layer; the
    three-argument form carries the HTTP mapping used by the handlers.
    """

    def __init__(self, *args: object):
        if len(args) == 1:
            self.status_code = 500
            self.code = "CONFIGURATION_ERROR"
            self.message = str(args[0])
        elif len(args) == 3:
            if not isinstance(args[0], int):
                raise TypeError("ConfigurationError status must be an integer")
            self.status_code = args[0]
            self.code = str(args[1])
            self.message = str(args[2])
        else:
            raise TypeError("ConfigurationError expects a message or status, code, and message")
        super().__init__(self.message)


class ConfigurationSource(Protocol):
    def get_admin_password(self) -> object:
        """Return the configured admin password value without mutating config."""

    def read_text(self) -> str:
        """Return the complete configuration text."""

    def write_text(self, text: str) -> None:
        """Persist configuration text atomically."""


class IniConfiguration:
    """Read-only adapter for an Odoo INI configuration file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get_admin_password(self) -> object:
        parser = ConfigParser(interpolation=None)
        try:
            with self.path.open(encoding="utf-8") as stream:
                parser.read_file(stream)
            return parser.get("options", "admin_passwd", fallback=None)
        except (OSError, ConfigError, UnicodeError):
            raise ConfigurationError("configuration is unavailable") from None

    def read_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ConfigurationError("configuration is unavailable") from None

    def write_text(self, text: str) -> None:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent, text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            raise ConfigurationError("configuration could not be written") from None


_configs: dict[str, object] = {}
_config_lock = RLock()


def register_config(client: str, source: object) -> None:
    if not isinstance(client, str) or not client:
        raise ValueError("client must be a non-empty string")
    with _config_lock:
        _configs[client] = source


def clear_configs() -> None:
    with _config_lock:
        _configs.clear()


def _read_source(source: object) -> str:
    reader = getattr(source, "read_text", None) or getattr(source, "read", None)
    if reader is None:
        raise ConfigurationError("configuration source cannot be read")
    try:
        return reader()
    except (OSError, UnicodeError):
        raise ConfigurationError("configuration is unavailable") from None


def _write_source(source: object, text: str) -> None:
    writer = getattr(source, "write_text", None) or getattr(source, "write", None)
    if writer is None:
        raise ConfigurationError("configuration source cannot be written")
    try:
        writer(text)
    except (OSError, UnicodeError):
        raise ConfigurationError("configuration could not be written") from None


def read_mode(client: str) -> str:
    with _config_lock:
        parser = _parse_mode_config(_read_source(get_config(client)))
        return _mode(parser)


def change_mode(client: str, mode: str) -> str:
    if mode not in {"client", "database-manager"}:
        raise ConfigurationError("unsupported startup mode")
    with _config_lock:
        source = get_config(client)
        parser = _parse_mode_config(_read_source(source))
        _mode(parser)
        parser.set("options", "mode", mode)
        from io import StringIO
        output = StringIO()
        parser.write(output)
        _write_source(source, output.getvalue())
        return _mode(_parse_mode_config(_read_source(source)))


def _parse_mode_config(text: str) -> ConfigParser:
    parser = ConfigParser()
    try:
        parser.read_string(text)
        if not parser.has_section("options"):
            raise ConfigError("missing options")
    except (ConfigError, TypeError, UnicodeError):
        raise ConfigurationError("configuration is malformed") from None
    return parser


def _mode(parser: ConfigParser) -> str:
    value = parser.get("options", "mode", fallback="").strip().lower()
    if value not in {"client", "database-manager"}:
        raise ConfigurationError("unsupported startup mode")
    return value


def get_config(client: str) -> object:
    try:
        return _configs[client]
    except KeyError:
        raise ConfigurationError("configuration is unavailable") from None
