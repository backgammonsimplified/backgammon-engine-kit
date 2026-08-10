#!/usr/bin/env python3
"""Print compact normative A/B Universal Position parity evidence."""

import json

from backgammon_engine_kit.position_contract import (
    decode_gnuid,
    decode_xgid,
    enrich_position,
    semantic_state_hash,
    source_record_hash,
)


PAIRS = (
    (
        "position-a",
        "XGID=-A-B--A---------------d---:0:0:1:00:0:0:0:0:10",
        "PAAAICMAAAAAAA:cAkAAAAAAAAE",
    ),
    (
        "position-b",
        "XGID=---D---------------a--b-a-:0:0:-1:00:0:0:0:0:8",
        "PAAAICMAAAAAAA:MAEAAAAAAAAE",
    ),
)


def main():
    rows = []
    for fixture_id, xgid, gnuid in PAIRS:
        x = decode_xgid(xgid)
        g = decode_gnuid(gnuid)
        x_context = {
            "cube": {"enabled": True},
            "rules": {"variation": "standard", "automatic_doubles": 0},
        }
        g_context = {
            "cube": {"enabled": True},
            "rules": {
                "variation": "standard",
                "jacoby": x.position.rules.jacoby,
                "beavers": x.position.rules.beavers,
                "automatic_doubles": 0,
                "maximum_cube": x.position.rules.maximum_cube,
            },
        }
        xp, xs = enrich_position(x.position, x.source, x_context)
        gp, gs = enrich_position(g.position, g.source, g_context)
        rows.append(
            {
                "fixture": fixture_id,
                "equal": xp == gp,
                "on_roll": xp.state.on_roll,
                "semantic_hash": semantic_state_hash(xp),
                "xgid_source_hash": source_record_hash(xs),
                "gnuid_source_hash": source_record_hash(gs),
            }
        )
    print(json.dumps({"status": "pass" if all(row["equal"] for row in rows) else "fail", "positions": rows}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
