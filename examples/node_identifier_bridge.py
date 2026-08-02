"""Node-consumption sketch: prepare checker/cube requests without execution.

This file lives in Engine Kit.  A future Node integration can import the same
public functions; it does not need to own identifier codecs or perspective
normalization.
"""

from backgammon_engine_kit import to_gnu_request, to_sage_request


CHECKER_GNUID = "4PPgASTgc/ABMA:cAnqAAAAAAAE"
CUBE_XGID = "XGID=---bB-DCC-B-cA---a-dabb---:2:1:1:00:4:2:1:7:10"


# Prepare only.  These calls do not start engines, parse results, cache data,
# publish output, or manage any worker lifecycle.
checker_preparation = to_gnu_request(CHECKER_GNUID, "checker")
cube_preparation = to_sage_request(CUBE_XGID, "cube")

if not checker_preparation.ready or not cube_preparation.ready:
    raise RuntimeError("identifier state is unavailable or unsupported")

checker_request = checker_preparation.request
cube_request = cube_preparation.request

# Node can now hand these AnalysisRequest values to an explicitly owned future
# Engine Kit service boundary.  This example intentionally stops before that.
assert checker_request.decision_type == "checker"
assert cube_request.decision_type == "cube"
