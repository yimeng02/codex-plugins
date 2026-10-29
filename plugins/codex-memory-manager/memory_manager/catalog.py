"""Read plugin identifiers from this user's local Codex cache.

This module reads manifest metadata only. Cache presence does not establish that
a plugin is enabled, and no plugin entry points or Codex commands are executed.
"""

import json
import os
from pathlib import Path
import re
import stat


MAX_MANIFEST_BYTES = 256 * 1024
MAX_DIRECTORY_ENTRIES = 4096
MAX_CACHED_VERSIONS = 4096
PLUGIN_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
DISCOVERY_NOTE = "仅从当前 Codex 本地缓存读取插件标识；缓存中存在不代表插件已启用或可调用。"


def _native_absolute(value):
    if not isinstance(value, (str, os.PathLike)):
        return None
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    # A Windows drive/UNC spelling must not be interpreted as a POSIX path.
    if os.name != "nt" and "\\" in str(path):
        return None
    if not path.is_absolute() or ".." in path.parts:
        return None
    return path


def _label(value, maximum=256):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > maximum or any(ord(character) < 32 for character in value):
        return None
    return value


def _segment(value):
    return _label(value, 128) is not None and value not in (".", "..") and not any(character in value for character in "/\\")


def resolve_codex_home(plugin_root=None, environment=None, user_home=None):
    """Return a native Codex home without searching other users or runtimes.

    Explicit native absolute CODEX_HOME wins. Only an absent/empty environment
    setting permits inference from our exact installed cache layout. An invalid
    explicit setting falls back to this user's standard home instead.
    """
    environment = os.environ if environment is None else environment
    explicit = environment.get("CODEX_HOME")
    if explicit:
        configured = _native_absolute(explicit)
        if configured is not None:
            return configured
    else:
        root = Path(__file__).resolve().parent.parent if plugin_root is None else _native_absolute(plugin_root)
        if root is not None and len(root.parts) >= 6:
            parts = root.parts
            if (parts[-5] == "plugins" and parts[-4] == "cache"
                    and parts[-2] == "codex-memory-manager"
                    and _segment(parts[-3]) and _segment(parts[-1])):
                return root.parents[4]
    home = Path.home() if user_home is None else _native_absolute(user_home)
    if home is None:
        raise ValueError("user_home 必须是当前平台的绝对路径")
    return home / ".codex"


def _directory(path):
    try:
        return not path.is_symlink() and stat.S_ISDIR(path.stat().st_mode)
    except (OSError, ValueError):
        return False


def _directories(path):
    """Bound breadth as well as using the cache's fixed three-level depth."""
    if not _directory(path):
        return []
    result = []
    try:
        with os.scandir(path) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES:
                    break
                if _segment(entry.name) and not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                    result.append(Path(entry.path))
    except OSError:
        pass
    return sorted(result, key=lambda item: item.name)


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable portable file must not cause fallback to another name.
        return True


def _manifest(path):
    """Ignore links, non-files, oversized, unreadable, or malformed manifests."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_MANIFEST_BYTES:
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_MANIFEST_BYTES
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                return None
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            return None
        manifest = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(manifest, dict):
            return None
        name = manifest.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or not PLUGIN_NAME.fullmatch(name):
            return None
        if "version" in manifest and not isinstance(manifest["version"], str):
            return None
        return manifest
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def _interface_label(manifest):
    interface = manifest.get("interface")
    return _label(interface.get("displayName")) if isinstance(interface, dict) else None


def _metadata(root):
    portable_path = root / "plugin.json"
    overlay_directory = root / ".codex-plugin"
    overlay_path = overlay_directory / "plugin.json"
    overlay = _manifest(overlay_path) if _directory(overlay_directory) else None
    if _exists(portable_path):
        manifest = _manifest(portable_path)
        if manifest is None:
            return None
        name = manifest["name"]
        extensions = manifest.get("extensions")
        extension = extensions.get("com.openai") if isinstance(extensions, dict) else None
        display_name = _interface_label(extension) if isinstance(extension, dict) else None
        if display_name is None and overlay and overlay["name"] == name:
            display_name = _interface_label(overlay)
    else:
        manifest = overlay
        if manifest is None:
            return None
        name = manifest["name"]
        display_name = _interface_label(manifest)
    return name, display_name, _label(manifest.get("version"), 128)


def discover_plugins(codex_home=None):
    """Return canonical identifiers grouped across local cache markets/versions."""
    home = resolve_codex_home() if codex_home is None else _native_absolute(codex_home)
    result = {"plugins": [], "source": "local-cache", "note": DISCOVERY_NOTE}
    if home is None or not _directory(home) or not _directory(home / "plugins"):
        return result
    cache = home / "plugins" / "cache"
    records = {}
    examined = 0
    for market in _directories(cache):
        for plugin in _directories(market):
            for version_directory in _directories(plugin):
                examined += 1
                if examined > MAX_CACHED_VERSIONS:
                    break
                metadata = _metadata(version_directory)
                if metadata is None:
                    continue
                name, display_name, version = metadata
                record = records.setdefault(name, {"names": set(), "marketplaces": set(), "versions": set()})
                if display_name:
                    record["names"].add(display_name)
                record["marketplaces"].add(market.name)
                record["versions"].add(version or version_directory.name)
            if examined >= MAX_CACHED_VERSIONS:
                break
        if examined >= MAX_CACHED_VERSIONS:
            break
    result["plugins"] = [{
        "id": name,
        # Deterministic metadata, without guessing freshness from mtime/version.
        "display_name": sorted(record["names"])[0] if record["names"] else name,
        "marketplaces": sorted(record["marketplaces"]),
        "versions": sorted(record["versions"]),
    } for name, record in sorted(records.items())]
    return result
