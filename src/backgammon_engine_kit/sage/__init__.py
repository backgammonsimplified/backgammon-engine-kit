"""Evidence-backed BGSage single-position adapter."""

from .adapter import SageAdapter
from .config import (
    SageRuntimeConfiguration,
    sage_configuration,
    sage_configuration_settings,
    verified_sage_configuration,
)

__all__ = (
    "SageAdapter",
    "SageRuntimeConfiguration",
    "sage_configuration",
    "sage_configuration_settings",
    "verified_sage_configuration",
)
