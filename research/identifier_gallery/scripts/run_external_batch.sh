#!/usr/bin/env bash
set -euo pipefail

RESEARCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_KIT_REPO="$(cd "$RESEARCH_DIR/../.." && pwd)"
ENGINE_SRC="$ENGINE_KIT_REPO/src"
CALCULATOR_LIBRARY="${BACKGAMMONCALCULATOR_R_LIBRARY:-$RESEARCH_DIR/.r-library}"
CALCULATOR_REF="v0.2.0"
CALCULATOR_COMMIT="a385a963ed01a6eac083dae7a1b246b1c150b3eb"
CALCULATOR_REFRESH="${BACKGAMMONCALCULATOR_REFRESH:-0}"
PROGRESS_INTERVAL="${EXTERNAL_BATCH_PROGRESS_INTERVAL:-1000}"

if [[ $# -ne 1 ]]; then
  echo "usage: bash research/identifier_gallery/scripts/run_external_batch.sh /path/to/input.csv" >&2
  exit 2
fi
INPUT="$1"
[[ -f "$INPUT" ]] || { echo "input CSV not found: $INPUT" >&2; exit 2; }

if [[ -x "$ENGINE_KIT_REPO/.venv-native-codec/Scripts/python.exe" ]]; then
  PYTHON="$ENGINE_KIT_REPO/.venv-native-codec/Scripts/python.exe"
elif [[ -x "$ENGINE_KIT_REPO/.venv/Scripts/python.exe" ]]; then
  PYTHON="$ENGINE_KIT_REPO/.venv/Scripts/python.exe"
else
  PYTHON="$(command -v python)"
fi
RSCRIPT="${RSCRIPT:-$(command -v Rscript 2>/dev/null || command -v Rscript.exe 2>/dev/null || true)}"
[[ -n "$RSCRIPT" ]] || { echo "Rscript not found" >&2; exit 1; }

native_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUTPUT="${EXTERNAL_BATCH_OUTPUT:-$ENGINE_KIT_REPO/artifacts/external-identifier-batch-$TIMESTAMP}"
SCRATCH="$HOME/Documents/scratch"
ZIP="$SCRATCH/$(basename "$OUTPUT").zip"
mkdir -p "$CALCULATOR_LIBRARY" "$OUTPUT" "$SCRATCH"

INPUT_NATIVE="$(native_path "$INPUT")"
OUTPUT_NATIVE="$(native_path "$OUTPUT")"
CALCULATOR_LIBRARY_NATIVE="$(native_path "$CALCULATOR_LIBRARY")"
ZIP_NATIVE="$(native_path "$ZIP")"
SCRATCH_NATIVE="$(native_path "$SCRATCH")"

export PYTHONPATH="$RESEARCH_DIR/src:$ENGINE_SRC${PYTHONPATH:+:$PYTHONPATH}"
export BACKGAMMONCALCULATOR_R_LIBRARY="$CALCULATOR_LIBRARY_NATIVE"

printf '\n[1/3] Verify released Calculator %s at %s\n' "$CALCULATOR_REF" "$CALCULATOR_COMMIT"
"$RSCRIPT" --vanilla "$RESEARCH_DIR/scripts/install_released_backgammoncalculator.R" \
  "$CALCULATOR_LIBRARY_NATIVE" "$CALCULATOR_REF" "$CALCULATOR_COMMIT" "$CALCULATOR_REFRESH"

printf '\n[2/3] Run external identifier batch\n'
set +e
"$PYTHON" -m backgammon_research.external_batch "$INPUT_NATIVE" \
  --output "$OUTPUT_NATIVE" \
  --r-library "$CALCULATOR_LIBRARY_NATIVE" \
  --rscript "$(native_path "$RSCRIPT")" \
  --progress-interval "$PROGRESS_INTERVAL"
BATCH_STATUS=$?
set -e

printf '\n[3/3] Hash and package complete evidence\n'
"$PYTHON" -m backgammon_research.external_batch --rehash-evidence "$OUTPUT_NATIVE"
export BATCH_EVIDENCE_NATIVE="$OUTPUT_NATIVE"
export BATCH_ZIP_NATIVE="$ZIP_NATIVE"
powershell.exe -NoProfile -Command \
  'Compress-Archive -LiteralPath $env:BATCH_EVIDENCE_NATIVE -DestinationPath $env:BATCH_ZIP_NATIVE -CompressionLevel Optimal -Force'
HASH_OUTPUT="$("$PYTHON" -m backgammon_research.external_batch --hash-file "$ZIP_NATIVE")"
ZIP_SHA256="${HASH_OUTPUT%% *}"

printf '\nEvidence directory:\n%s\n' "$OUTPUT"
printf 'ZIP path:\n%s\n' "$ZIP"
printf 'ZIP SHA-256:\n%s\n' "$ZIP_SHA256"
explorer.exe "$SCRATCH_NATIVE" >/dev/null 2>&1 || true
exit "$BATCH_STATUS"
