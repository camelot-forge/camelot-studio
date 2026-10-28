#!/usr/bin/env sh
# bootstrap.sh — the development environment for the Python core.
#
# Creates .venv, installs the core package with its dev, server and MySQL
# extras, and patches pyffi for Python >= 3.12. Network access is needed on the
# first run (PyPI).
set -eu
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "error: Python 3.11 or newer is required (set PYTHON=python3.11 to choose an interpreter)" >&2
    exit 1
}

if [ ! -x .venv/bin/python ]; then
    echo ">> creating .venv"
    "$PY" -m venv .venv
fi

echo ">> installing the core package and its extras"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e './core[dev,server,mysql]'

SP=$(.venv/bin/python -c 'import pathlib, pyffi; print(pathlib.Path(pyffi.__file__).parent)')
if grep -q "time.clock()" "$SP/object_models/xml/__init__.py"; then
    echo ">> patching pyffi: time.clock -> time.perf_counter (Python >= 3.12)"
    # perl -pi works on both GNU (Linux) and BSD (macOS).
    perl -pi -e 's/time\.clock\(\)/time.perf_counter()/g' \
        "$SP/object_models/xml/__init__.py" \
        "$SP/object_models/xsd/__init__.py"
fi

echo ">> ready. Use: .venv/bin/camelot --help"
