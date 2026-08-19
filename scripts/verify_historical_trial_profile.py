#!/usr/bin/env python3
"""Run a bounded real-runtime smoke for the retained Sage-vs-GNU trial profile."""

import argparse
import json

from backgammon_engine_kit.gnu import GnuAdapter, GnuRuntimeConfiguration, gnu_configuration
from backgammon_engine_kit.gnu.config import gnu_configuration_settings
from backgammon_engine_kit.models import AnalysisRequest, Position
from backgammon_engine_kit.sage import SageAdapter, SageRuntimeConfiguration, sage_configuration


CHECKER_GNUID = "4PPgASTgc/ABMA:cAnqAAAAAAAE"
CUBE_GNUID = "bD3BAQyYd2cEAA:cAngAAAAAAAE"


def request(engine, decision_type, setting, configuration):
    return AnalysisRequest(
        position=Position(
            id=CHECKER_GNUID if decision_type == "checker" else CUBE_GNUID,
            format="gnuid",
        ),
        engine=engine,
        analysis_setting=setting,
        decision_type=decision_type,
        dice=(4, 2) if decision_type == "checker" else None,
        configuration=configuration,
    )


def result_record(engine, decision_type, result, configuration):
    decision = result.checker_decision if decision_type == "checker" else result.cube_decision
    record = {
        "engine": engine,
        "decision_type": decision_type,
        "requested_setting": result.analysis_setting,
        "actual_ply": decision.actual_ply,
        "configuration_hash": configuration.configuration_hash,
        "profile": configuration.profile,
        "status": result.status,
    }
    if engine == "gnu":
        settings = gnu_configuration_settings(configuration)
        record.update(
            {
                "configured_checker_plies": settings["checker_plies"],
                "configured_cube_plies": settings["cube_plies"],
                "checker_move_filter_profile": settings["move_filter_profile"],
            }
        )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sage-timeout", type=float, default=900.0)
    parser.add_argument("--gnu-timeout", type=float, default=180.0)
    parser.add_argument("--sage-threads", type=int, default=1)
    parser.add_argument("--gnu-threads", type=int, default=1)
    parser.add_argument("--skip-sage", action="store_true")
    parser.add_argument("--skip-gnu", action="store_true")
    args = parser.parse_args()

    records = []
    if not args.skip_sage:
        sage_config = sage_configuration(
            checker_setting="4ply",
            cube_setting="3ply",
            parallel_threads=args.sage_threads,
        )
        sage = SageAdapter(SageRuntimeConfiguration.discover())
        sage_checker = sage.analyze(
            request("sage", "checker", "4ply", sage_config),
            timeout_seconds=args.sage_timeout,
        )
        sage_cube = sage.analyze(
            request("sage", "cube", "3ply", sage_config),
            timeout_seconds=args.sage_timeout,
        )
        records.extend(
            (
                result_record("sage", "checker", sage_checker, sage_config),
                result_record("sage", "cube", sage_cube, sage_config),
            )
        )

    if not args.skip_gnu:
        gnu_config = gnu_configuration(
            checker_plies=3,
            cube_plies=2,
            threads=args.gnu_threads,
        )
        gnu = GnuAdapter(GnuRuntimeConfiguration.discover())
        gnu_checker = gnu.analyze(
            request("gnu", "checker", "3ply", gnu_config),
            timeout_seconds=args.gnu_timeout,
        )
        gnu_cube = gnu.analyze(
            request("gnu", "cube", "2ply", gnu_config),
            timeout_seconds=args.gnu_timeout,
        )
        records.extend(
            (
                result_record("gnu", "checker", gnu_checker, gnu_config),
                result_record("gnu", "cube", gnu_cube, gnu_config),
            )
        )

    for record in records:
        key = (record["engine"], record["decision_type"])
        if key == ("sage", "checker"):
            if record["requested_setting"] != "4ply" or record["actual_ply"] != 4:
                raise SystemExit("Sage checker profile did not execute at 4-ply")
        elif key == ("sage", "cube"):
            if record["requested_setting"] != "3ply" or record["actual_ply"] != 3:
                raise SystemExit("Sage cube profile did not execute at 3-ply")
        elif key == ("gnu", "checker"):
            if record["requested_setting"] != "3ply" or record["configured_checker_plies"] != 3:
                raise SystemExit("GNU checker was not configured for 3-ply")
            if record["configured_cube_plies"] != 2:
                raise SystemExit("GNU checker run did not preserve the pinned 2-ply cube configuration")
            if not isinstance(record["actual_ply"], int) or not 0 <= record["actual_ply"] <= 3:
                raise SystemExit("GNU checker recommendation reports an impossible actual ply")
            if "normal-v1" not in record["checker_move_filter_profile"]:
                raise SystemExit("GNU checker did not use the pinned Normal move-filter profile")
        elif key == ("gnu", "cube"):
            if record["requested_setting"] != "2ply" or record["configured_cube_plies"] != 2:
                raise SystemExit("GNU cube was not configured for 2-ply")
            if record["configured_checker_plies"] != 3:
                raise SystemExit("GNU cube run did not preserve the pinned 3-ply checker configuration")
            if record["actual_ply"] != 2:
                raise SystemExit("GNU cube result did not execute at 2-ply")
        else:
            raise SystemExit("unexpected smoke result {}".format(key))

    print(json.dumps({"profile": "sage4/3-gnu3/2", "results": records}, indent=2, sort_keys=True))
    print("HISTORICAL_TRIAL_PROFILE_SMOKE=PASS")


if __name__ == "__main__":
    main()
