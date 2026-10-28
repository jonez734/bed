"""bed --config path resolution.

The ``--config`` CLI flag is optional. When the operator omits it,
:func:`resolve_config_path` walks this precedence (highest wins):

1. The ``--config <path>`` value (if argparse set it).
2. The ``$BED_CONFIG`` environment variable.
3. The packaged default ``bed/data/bed.json`` shipped inside the wheel
   (via :func:`bed.config.get_package_data_path`).

Nothing is read from ``/etc``. The packaged default ships with the module, so a daemon
needs no file outside its venv.
"""

from __future__ import annotations

import os
from typing import Optional

from .config import get_package_data_path

CONFIG_ENV = "BED_CONFIG"


def resolve_config_path(explicit: Optional[str] = None) -> str:
    """Resolve the ``bed.json`` path using the precedence above.

    ``explicit`` is the value argparse produced for ``--config``. When
    non-empty, it always wins. Otherwise the resolver walks env ->
    packaged default. The returned path is guaranteed to exist (the
    packaged default ships inside the wheel and is always present
    after ``pip install bed``).
    """
    if explicit:
        return explicit
    env_override = os.environ.get(CONFIG_ENV)
    if env_override:
        return env_override
    return str(get_package_data_path("bed.json"))
