"""Evidence-backed GNU Backgammon single-position integration."""

from .adapter import GnuAdapter
from .config import GnuRuntimeConfiguration, verified_gnu_configuration

__all__ = ("GnuAdapter", "GnuRuntimeConfiguration", "verified_gnu_configuration")
