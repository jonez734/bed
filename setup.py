#!/usr/bin/env python3
# Metadata lives in pyproject.toml. This shim exists only so older tooling that runs `setup.py` keeps
# working; `python -m build` and `pip` read pyproject.toml directly.
from setuptools import setup

setup()
