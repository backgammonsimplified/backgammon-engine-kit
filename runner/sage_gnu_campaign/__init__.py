"""Resumable Sage 4/3 versus GNU 3/2 mirrored-pair campaign."""

from .config import CampaignConfig, load_campaign_config
from .identity import PairIdentity, pair_identity

__all__ = ("CampaignConfig", "PairIdentity", "load_campaign_config", "pair_identity")
