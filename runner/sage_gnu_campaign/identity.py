"""Stable campaign pair and seed identities."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .config import CampaignConfig, canonical_json_bytes


@dataclass(frozen=True)
class PairIdentity:
    campaign_id: str
    pair_index: int
    pair_id: str
    base_seed: str

    def to_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "pair_index": self.pair_index,
            "pair_id": self.pair_id,
            "base_seed": self.base_seed,
        }


def pair_identity(config: CampaignConfig, pair_index: int) -> PairIdentity:
    origin = int(config.data["identity"]["pair_sequence_origin"])
    if not origin <= pair_index < origin + config.pair_count:
        raise ValueError(f"pair index {pair_index} is outside the committed campaign bound")
    pair_authority = {
        "domain": config.data["identity"]["pair_id_domain"],
        "campaign": config.semantics_identity(),
        "pair_index": pair_index,
    }
    pair_digest = hashlib.sha256(canonical_json_bytes(pair_authority)).hexdigest()
    pair_id = f"pair-{pair_index:06d}-{pair_digest[:16]}"
    seed_material = (
        f"{config.data['identity']['base_seed_domain']}\0{config.campaign_id}\0"
        f"{pair_index}\0{pair_id}"
    ).encode("utf-8")
    base_seed = "sha256:" + hashlib.sha256(seed_material).hexdigest()
    return PairIdentity(config.campaign_id, pair_index, pair_id, base_seed)


def all_pair_identities(config: CampaignConfig) -> tuple[PairIdentity, ...]:
    origin = int(config.data["identity"]["pair_sequence_origin"])
    return tuple(pair_identity(config, index) for index in range(origin, origin + config.pair_count))
