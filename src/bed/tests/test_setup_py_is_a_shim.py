"""setup.py is a metadata-free shim: metadata lives in pyproject.toml (matches letteredolive)."""

import pathlib

import pytest

SETUP = pathlib.Path(__file__).resolve().parents[3] / "setup.py"


@pytest.mark.unit
def test_setup_py_exists_as_a_shim():
    assert SETUP.exists(), "bed/setup.py must exist as a shim"


@pytest.mark.unit
def test_setup_py_carries_no_metadata():
    text = SETUP.read_text()
    assert "name=" not in text and "classifiers" not in text and "license" not in text
    assert "setup()" in text
