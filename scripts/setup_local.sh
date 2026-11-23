#!/usr/bin/env bash
# Set up a local copy of the Hugging Face experiment and reproduce the full scan.
#
#   bash scripts/setup_local.sh                # full run: ~5 GB download, ~20 min
#   bash scripts/setup_local.sh --sample 10000 # quick check on a 10,000-card sample
#   SKIP_INSTALL=1 bash scripts/setup_local.sh # re-run without touching the venv/dependencies
#
# Run from the repository root (the folder `git clone` created). Needs Python 3.11+.
# macOS/Linux; on Windows use WSL or Git Bash. Safe to re-run: finished steps are skipped.
# Steps: 1 venv + dependencies, 2 download, 3 export + scan, 4 compare with the committed counts.
set -euo pipefail

SAMPLE=0
if [[ "${1:-}" == "--sample" ]]; then SAMPLE="${2:?usage: --sample N}"; fi
WORKERS="${WORKERS:-$(python3 -c 'import os; print(max(1, (os.cpu_count() or 2) - 1))')}"

[[ -f scanner/scan_hf.py ]] || { echo "run this from the repository root" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "Python 3.11+ is required (found $(python3 --version))" >&2; exit 1; }

echo "== 1/4 virtual environment and dependencies"
if [[ "${SKIP_INSTALL:-0}" == "1" ]]; then
  echo "SKIP_INSTALL=1: using the current Python environment"
else
  [[ -d .venv ]] || python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt anthropic
fi

echo "== 2/4 download the dataset (5.2 GB CSV, pinned revision)"
python data/huggingface/download.py fetch

if [[ "$SAMPLE" -gt 0 ]]; then
  CARDS="data/huggingface/cards.sample${SAMPLE}.jsonl.gz"
  OUT="results/hf_sample${SAMPLE}"
  EXPORT_ARGS=(--sample "$SAMPLE")
else
  CARDS="data/huggingface/cards.jsonl.gz"
  OUT="results/hf_full"
  EXPORT_ARGS=()
fi

echo "== 3/4 export and scan ($WORKERS workers)"
if [[ -f "$CARDS" ]]; then
  echo "$CARDS exists, skipping export"
else
  python data/huggingface/download.py export "${EXPORT_ARGS[@]}"
fi
python scanner/scan_hf.py "$CARDS" --out "$OUT" --yaml --workers "$WORKERS"

echo "== 4/4 compare with the committed counts"
if git ls-files --error-unmatch "$OUT/summary.json" >/dev/null 2>&1; then
  if git diff --quiet -- "$OUT/summary.json"; then
    echo "OK: $OUT/summary.json matches the committed results"
  else
    echo "MISMATCH: $OUT/summary.json differs from the committed results:" >&2
    git --no-pager diff -- "$OUT/summary.json" >&2
    exit 1
  fi
else
  echo "no committed $OUT/summary.json to compare with (new sample size?)"
fi
echo "Done. Next: classification, see classify/README.md"
