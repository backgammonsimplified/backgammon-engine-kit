"""Evidence-backed GNU Backgammon single-position integration."""

from .adapter import GnuAdapter
from .config import (
    GnuRuntimeConfiguration,
    gnu_configuration,
    gnu_configuration_settings,
    verified_gnu_configuration,
)

__all__ = (
    "GnuAdapter",
    "GnuRuntimeConfiguration",
    "gnu_configuration",
    "gnu_configuration_settings",
    "verified_gnu_configuration",
)
