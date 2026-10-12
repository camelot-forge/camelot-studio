#!/usr/bin/env sh
# bootstrap.sh — creates the .venv for Camelot Studio core and patches pyffi
# for Python >= 3.12.
# Usage: ./bootstrap.sh   (needs network on first run: pypi.org)
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
    echo ">> creating venv (without pip)…"
    "$PY" -m venv --without-pip .venv
fi

if [ ! -x .venv/bin/pip ]; then
    echo ">> installing pip (bootstrap.pypa.io)…"
    curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    .venv/bin/python get-pip.py --no-cache-dir
    rm -f get-pip.py
fi

echo ">> installing dependencies (pyffi, setuptools, numpy, pytest, xdist, ruff, server)…"
.venv/bin/python -m pip install --no-cache-dir -q pyffi setuptools numpy pytest pytest-xdist ruff \
    fastapi "uvicorn[standard]" watchfiles httpx

SP=.venv/lib/python3.*/site-packages/pyffi
if grep -q "time.clock()" $SP/object_models/xml/__init__.py; then
    echo ">> patching pyffi: time.clock -> time.perf_counter (Python >= 3.12)…"
    # perl -pi instead of sed -i: works on both GNU (Linux) and BSD (macOS)
    perl -pi -e 's/time\.clock\(\)/time.perf_counter()/g' \
        $SP/object_models/xml/__init__.py \
        $SP/object_models/xsd/__init__.py
fi

echo ">> installing the core package (editable mode)…"
.venv/bin/python -m pip install --no-cache-dir -q -e ./core

echo ">> ready. Use: .venv/bin/camelot --help"