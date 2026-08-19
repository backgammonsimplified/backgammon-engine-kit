"""Deterministic, shell-free GNU Backgammon invocation construction."""

from dataclasses import dataclass

from .config import gnu_configuration_settings, verified_gnu_configuration


CHECKER_CANDIDATE_LIMIT = 8
MOVE_FILTER = (1, 0, 8, 0.160)

# Exact GNU Normal move filters used by the pinned executable and by the
# historical match runner when it changed plies without overriding filters.
_NORMAL_MOVE_FILTER_ROWS = (
    (1, 0, 0, 8, 0.160),
    (2, 0, 0, 8, 0.160),
    (2, 1, -1, 0, 0.0),
    (3, 0, 0, 8, 0.160),
    (3, 1, -1, 0, 0.0),
    (3, 2, 0, 2, 0.040),
    (4, 0, 0, 8, 0.160),
    (4, 1, -1, 0, 0.0),
    (4, 2, 0, 2, 0.040),
    (4, 3, -1, 0, 0.0),
)


@dataclass(frozen=True)
class GnuInvocation:
    argv: tuple
    stdin_text: str
    environment: dict

    def public_argv(self):
        values = list(self.argv)
        values[0] = "<GNU_EXECUTABLE>"
        values[values.index("-D") + 1] = "<GNU_DATA_DIR>"
        values[values.index("-P") + 1] = "<GNU_PACKAGE_DATA_DIR>"
        return values


def _normal_move_filter_commands():
    commands = []
    for target_ply, sub_ply, accept, extra, threshold in _NORMAL_MOVE_FILTER_ROWS:
        commands.append(
            "set evaluation movefilter {} {} {} {} {:.3f}".format(
                target_ply,
                sub_ply,
                accept,
                extra,
                threshold,
            )
        )
    return commands


def _common_commands(configuration=None):
    settings = gnu_configuration_settings(configuration or verified_gnu_configuration())
    checker_plies = settings["checker_plies"]
    cube_plies = settings["cube_plies"]
    threads = settings["threads"]
    commands = [
        "set confirm new off",
        "set confirm save off",
        "set player 0 name player0",
        "set player 1 name player1",
        "set variation standard",
        "set cube use on",
        "set jacoby off",
        "set beavers 0",
        "set threads {}".format(threads),
        "set output digits 6",
        "set output winpc off",
        "set output matchpc off",
        "set output mwc off",
        "set output rawboard off",
        "set evaluation chequerplay type evaluation",
        "set evaluation chequerplay evaluation plies {}".format(checker_plies),
        "set evaluation chequerplay evaluation cubeful on",
        "set evaluation chequerplay evaluation deterministic on",
        "set evaluation chequerplay evaluation noise 0",
        "set evaluation chequerplay evaluation prune off",
        "set evaluation cubedecision type evaluation",
        "set evaluation cubedecision evaluation plies {}".format(cube_plies),
        "set evaluation cubedecision evaluation cubeful on",
        "set evaluation cubedecision evaluation deterministic on",
        "set evaluation cubedecision evaluation noise 0",
        "set evaluation cubedecision evaluation prune off",
    ]
    if settings["legacy"]:
        # Preserve the exact v0.3.0 command stream for the legacy 1-ply profile.
        commands.append("set evaluation movefilter 1 0 0 8 0.160")
    else:
        commands.extend(_normal_move_filter_commands())
    return commands


def build_invocation(request, runtime):
    if request.position.format != "gnuid" or request.position.id is None:
        raise ValueError("GNU adapter requires a verified combined GNU ID")
    commands = _common_commands(request.configuration)
    commands.extend(
        [
            "set gnubgid " + request.position.id,
            "show board",
            "show dice",
            "show cube",
            "show score",
            "show turn",
            "show crawford",
            "show jacoby",
            "show beavers",
            "show evaluation",
            "show threads",
            "show output",
            "hint 8" if request.decision_type == "checker" else "hint",
            "quit",
        ]
    )
    argv = (
        str(runtime.executable),
        "-r",
        "-q",
        "-t",
        "-s",
        "/proc/self",
        "-D",
        str(runtime.data_dir),
        "-P",
        str(runtime.package_data_dir),
        "-c",
        "/dev/stdin",
    )
    return GnuInvocation(argv=argv, stdin_text="\n".join(commands) + "\n", environment=runtime.environment())


def verified_source_id(position, source, expected_semantic_hash=None, invocation_settings=None):
    """Adapter-boundary seam for guarded reuse of a preserved combined GNU ID."""
    from ..position_contract.gnu_bridge import verify_gnu_source_bridge

    return verify_gnu_source_bridge(
        position,
        source,
        expected_semantic_hash=expected_semantic_hash,
        invocation_settings=invocation_settings,
    )
