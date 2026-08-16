"""Consumer example: prepare checker and cube requests from identifiers.

The example uses only public Engine Kit APIs. It demonstrates the integration
boundary a downstream application can use before handing an ``AnalysisRequest``
to an explicitly owned analysis service.

The preparation helpers validate and normalize identifier state but do not
start GNU Backgammon or BGSage, parse analysis results, write cache entries, or
manage worker/process lifecycle.
"""

from backgammon_engine_kit import to_gnu_request, to_sage_request


CHECKER_GNUID = "4PPgASTgc/ABMA:cAnqAAAAAAAE"
CUBE_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:1:00:0:0:0:0:8"


checker_preparation = to_gnu_request(CHECKER_GNUID, "checker")
cube_preparation = to_sage_request(CUBE_XGID, "cube")

if not checker_preparation.ready or not cube_preparation.ready:
    raise RuntimeError("identifier state is unavailable or unsupported")

checker_request = checker_preparation.request
cube_request = cube_preparation.request

# A consumer can now pass these validated AnalysisRequest values to the
# application-owned analysis boundary. This example intentionally stops before
# engine execution.
assert checker_request.decision_type == "checker"
assert cube_request.decision_type == "cube"
