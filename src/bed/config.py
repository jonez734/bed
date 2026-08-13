import json
import os
from pathlib import Path
from typing import Any, Dict

from bbsengine6 import io
from bbsengine6.common import safe_path

_PATH_KEY_SUFFIXES = ("_path", "_file", "_dir", "_socket", "_log")


def get_package_data_path(filename: str) -> Path:
    """Get path to a file in the bed package data directory."""
    return Path(__file__).parent / "data" / filename


def load_config(
    config_file: str,
    env_prefix: str = "BED_",
    **overrides: Any,
) -> Dict[str, Any]:
    """
    Load bed configuration from an explicit config file path (required).

    Priority order:
    1. Command line / overrides (highest)
    2. Environment variables
    3. Config file (lowest)

    Variable format: BED_<SECTION>_<KEY>=value or BED_KEY=value
    """
    io.echo(f"bed.json config path: {config_file}")
    with open(config_file) as f:
        config = json.load(f)
    config = _expand_paths(config)

    env_config = _load_from_env(env_prefix)
    env_config = _expand_paths(env_config)
    config = _merge_config(config, env_config)

    config = _expand_paths(config)
    config = _merge_config(config, overrides)

    return config


def _merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge override into base config."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def _load_from_env(prefix: str) -> Dict[str, Any]:
    """Load configuration from environment variables.

    Variable format: BED_<SECTION>_<KEY>=value or BED_KEY=value
    Example: BED_DEBUG=true, BED_BED_AUTORESTART=true
    """
    config = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue

        config_key = key[len(prefix):]

        if "_" in config_key:
            parts = config_key.split("_", 1)
            section = parts[0].lower()
            key_name = parts[1].lower()

            if section not in config:
                config[section] = {}

            if value.lower() in ("true", "false"):
                config[section][key_name] = value.lower() == "true"
            elif value.isdigit():
                config[section][key_name] = int(value)
            else:
                config[section][key_name] = value
        else:
            key_name = config_key.lower()
            if value.lower() in ("true", "false"):
                config[key_name] = value.lower() == "true"
            elif value.isdigit():
                config[key_name] = int(value)
            else:
                config[key_name] = value

    return config


def _expand_paths(value: Any, *, key: str | None = None) -> Any:
    """Recursively expand path-shaped values via bbsengine6.common.safe_path,
    which handles ``~`` and ``$VAR`` plus normalization/abspath, with symlinks
    NOT resolved so the textual form is preserved.

    Non-path keys (module names like ``bed.api.message``, hostnames like
    ``127.0.0.1``, mode strings like ``memory``) and all non-string values
    pass through unchanged.
    """
    if isinstance(value, str):
        if key is not None and any(key.endswith(s) for s in _PATH_KEY_SUFFIXES):
            return safe_path(value, resolve_symlinks=False)
        return value
    if isinstance(value, dict):
        return {k: _expand_paths(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_paths(v, key=key) for v in value]
    return value


