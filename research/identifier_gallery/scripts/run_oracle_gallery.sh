#!/usr/bin/env bash
set -euo pipefail

RESEARCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_KIT_REPO="$(cd "$RESEARCH_DIR/../.." && pwd)"
ENGINE_SRC="$ENGINE_KIT_REPO/src"
CASES="$RESEARCH_DIR/fixtures/cases.csv"
OUTPUT="${ORACLE_GALLERY_OUTPUT:-$ENGINE_KIT_REPO/artifacts/oracle-identifier-comparison}"
BGLAB_LIBRARY="${BGLAB_R_LIBRARY:-$RESEARCH_DIR/.r-library}"
BOARD_LIBRARY="${BACKGAMMONBOARD_R_LIBRARY:-$RESEARCH_DIR/.renderer-library}"
BOARD_COMMIT="a4ab56f712c9ecb8e8ad83782cc82d5b32d94883"
BGLAB_REFRESH="${BGLAB_REFRESH:-0}"
BOARD_REFRESH="${BACKGAMMONBOARD_REFRESH:-0}"

if [[ -x "$ENGINE_KIT_REPO/.venv-native-codec/Scripts/python.exe" ]]; then PYTHON="$ENGINE_KIT_REPO/.venv-native-codec/Scripts/python.exe";
elif [[ -x "$ENGINE_KIT_REPO/.venv/Scripts/python.exe" ]]; then PYTHON="$ENGINE_KIT_REPO/.venv/Scripts/python.exe";
else PYTHON="$(command -v python)"; fi
RSCRIPT="${RSCRIPT:-$(command -v Rscript 2>/dev/null || command -v Rscript.exe 2>/dev/null || true)}"
[[ -n "$RSCRIPT" ]] || { echo "Rscript not found" >&2; exit 1; }
native_path(){ if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
BGLAB_LIBRARY_NATIVE="$(native_path "$BGLAB_LIBRARY")"; BOARD_LIBRARY_NATIVE="$(native_path "$BOARD_LIBRARY")"
mkdir -p "$BGLAB_LIBRARY" "$BOARD_LIBRARY"
export PYTHONPATH="$RESEARCH_DIR/src:$ENGINE_SRC${PYTHONPATH:+:$PYTHONPATH}"
export BGLAB_R_LIBRARY="$BGLAB_LIBRARY_NATIVE" BACKGAMMONBOARD_R_LIBRARY="$BOARD_LIBRARY_NATIVE"

printf '\n[1/6] Engine Kit / AnkiGammon preflight\n'
"$PYTHON" -c "import ankigammon, backgammon_engine_kit as bek; print('AnkiGammon: OK'); print('Engine Kit:', bek.__file__)"
printf '\n[2/6] Calculator 0.2.0 reference preflight\n'
"$RSCRIPT" --vanilla -e "if(!requireNamespace('backgammoncalculator',quietly=TRUE)) stop('backgammoncalculator is not installed'); d<-utils::packageDescription('backgammoncalculator'); v<-as.character(utils::packageVersion('backgammoncalculator')); if(v!='0.2.0') stop(paste('expected backgammoncalculator 0.2.0, found',v)); sha<-ifelse(is.null(d\$RemoteSha),'',d\$RemoteSha); if(sha!='a385a963ed01a6eac083dae7a1b246b1c150b3eb') stop(paste('unexpected Calculator RemoteSha',sha)); cat('backgammoncalculator: ',v,' RemoteSha=',sha,'\n',sep='')"
printf '\n[3/6] Exact current backgammonboard renderer preflight\n'
"$RSCRIPT" --vanilla "$RESEARCH_DIR/scripts/install_current_backgammonboard.R" "$BOARD_LIBRARY_NATIVE" "$BOARD_COMMIT" "$BOARD_REFRESH"
printf '\n[4/6] Current bglab diagnostic preflight\n'
"$RSCRIPT" --vanilla "$RESEARCH_DIR/scripts/install_current_bglab.R" "$BGLAB_LIBRARY_NATIVE" "$BGLAB_REFRESH"
"$PYTHON" - <<'PY'
from backgammon_research.gnu_cli import GnuBackgammonCli
c=GnuBackgammonCli();print('GNU CLI:',c.executable);print('GNU CLI command contract:',c.provenance['command_contract'])
PY
printf '\n[5/6] Research gallery tests\n'
"$PYTHON" -m unittest discover -s "$RESEARCH_DIR/tests" -p 'test_*.py' -v
printf '\n[6/6] Build full oracle-first reconciliation gallery\n'
rm -rf "$OUTPUT"; mkdir -p "$OUTPUT"
"$PYTHON" -m backgammon_research.oracle_gallery --cases "$CASES" --output "$OUTPUT" --r-library "$BGLAB_LIBRARY_NATIVE"
printf '\nFull oracle-first reconciliation gallery:\n%s\n' "$OUTPUT/oracle-gallery.html"
printf 'Machine-readable report:\n%s\n' "$OUTPUT/oracle-comparison-results.json"
printf 'Method comparisons:\n%s\n' "$OUTPUT/method-comparisons.csv"
printf 'Round trips:\n%s\n' "$OUTPUT/roundtrips.csv"
printf 'Rendered SVG directory:\n%s\n' "$OUTPUT/renders"
if command -v explorer.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then explorer.exe "$(cygpath -w "$OUTPUT/oracle-gallery.html")" >/dev/null 2>&1 || true; fi
