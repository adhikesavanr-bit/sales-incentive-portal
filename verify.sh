#!/usr/bin/env bash
# Offline verification. Needs Python 3.10+ and nothing else — no cloud account,
# no credentials, no network beyond installing packages.
#
#   ./verify.sh /path/to/folder/holding/the/workbooks
#
# On macOS you can drag the folder onto the Terminal window to fill in the path.

set -euo pipefail
cd "$(dirname "$0")/backend"

SOURCE="${1:-}"

# --- find a usable python -------------------------------------------------
# Prefer a version that scientific packages ship wheels for. A very new
# interpreter works, but pip may have to compile pandas from source, which is
# slow and often fails.
PY=""
for candidate in python3.12 python3.11 python3.13 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "No Python 3.10 or newer found."
  echo "On macOS:  brew install python@3.12    (or install Xcode command line tools)"
  exit 1
fi
echo "Using $($PY --version) at $(command -v $PY)"

# --- isolated environment -------------------------------------------------
# A venv keeps this off your system Python, which recent macOS and Homebrew
# builds refuse to install into anyway.
if [ ! -d .venv ]; then
  echo "Creating a virtual environment in backend/.venv …"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if python -c 'import sys; sys.exit(0 if sys.version_info >= (3,13) else 1)'; then
  echo
  echo "Note: Python $(python -c 'import sys;print(".".join(map(str,sys.version_info[:2])))') is newer than most"
  echo "scientific packages publish wheels for. If the install below fails or"
  echo "stalls compiling pandas, install 3.12 and re-run:"
  echo "    brew install python@3.12 && rm -rf backend/.venv && bash verify.sh \"$SOURCE\""
  echo
fi

echo "Installing dependencies (first run takes a minute) …"
pip install --quiet --upgrade pip
if ! pip install --quiet -r requirements-verify.txt; then
  echo
  echo "Dependency install failed. If the error above names a package, it is"
  echo "usually a Python version problem — install 3.12 and retry. If it names"
  echo "a line in requirements-verify.txt, that file is malformed."
  echo "    brew install python@3.12"
  echo "    rm -rf backend/.venv"
  echo "    bash verify.sh \"$SOURCE\""
  exit 1
fi

# --- checks ---------------------------------------------------------------
echo
echo "=== Unit tests ==============================================="
python -m pytest tests/ -q --ignore=tests/reconcile_august.py

if [ -z "$SOURCE" ]; then
  echo
  echo "Unit tests passed."
  echo
  echo "To also reconcile against your workbooks, put them in one folder and"
  echo "pass it in. Spaces or underscores in the filenames are both fine:"
  echo "    the monthly Coupon Working workbook"
  echo "    the Field Incentive Report workbook"
  echo "    the Coupon Agent Details file"
  echo "    the employee email list"
  echo
  echo "    ./verify.sh ~/Downloads/workbooks"
  exit 0
fi

if [ ! -d "$SOURCE" ]; then
  echo "Not a folder: $SOURCE"
  exit 1
fi

# The Python tools locate the workbooks themselves and report clearly if one
# is absent, so there is no filename check here. Spaces or underscores both
# work.

echo
echo "=== Reconciliation against the August workbook ==============="
python -m tests.reconcile_august "$SOURCE"

echo
echo "=== Bootstrap dry run (writes nothing) ======================="
python -m scripts.bootstrap --source "$SOURCE" --dry-run
