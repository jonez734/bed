"""bed --config path resolution.

The ``--config`` CLI flag is optional. When the operator omits it,
:func:`resolve_config_path` walks this precedence (highest wins):

1. The ``--config <path>`` value (if argparse set it).
2. The ``$BED_CONFIG`` environment variable.
3. ``/etc/bed/bed.json`` if present (the FHS-installed config,
   populated by ``make install-etc`` from the wheel-shipped factory
   default).
4. The packaged default ``bed/data/bed.json`` shipped inside the wheel
   (via :func:`bed.config.get_package_data_path`).

The packaged default is byte-identical to the FHS factory
(``bed/usr/share/factory/etc/bed/bed.json``), so the fallback is
semantically equivalent to "operator has not customised the config".
The systemd unit ``bed/src/bed/daemon/bed.service`` keeps passing
``--config /etc/bed/bed.json`` so FHS hosts use the operator-edit
surface; the fallback only fires for non-prod invocations
(``bed --foreground``, ``deploy-venv``, ``test_*``) and for hosts
that simply have not run ``make install-etc`` yet.
"""

from __future__ import annotations

import os
from typing import Optional

from .config import get_package_data_path

CONFIG_ENV = "BED_CONFIG"
FHS_CONFIG = "/etc/bed/bed.json"


def resolve_config_path(explicit: Optional[str] = None) -> str:
    """Resolve the ``bed.json`` path using the precedence above.

    ``explicit`` is the value argparse produced for ``--config``. When
    non-empty, it always wins. Otherwise the resolver walks env -> FHS
    -> packaged default. The returned path is guaranteed to exist (the
    packaged default ships inside the wheel and is always present
    after ``pip install bed``).
    """
    if explicit:
        return explicit
    env_override = os.environ.get(CONFIG_ENV)
    if env_override:
        return env_override
    if os.path.isfile(FHS_CONFIG):
        return FHS_CONFIG
    return str(get_package_data_path("bed.json"))
