#!/usr/bin/env python3
# bed/tests/test_bind_config.py
# bed.json "bind" resolves to the same listeners the command line would give.

import argparse
import importlib
import json
import os

import pytest

bm = importlib.import_module("bed.main")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BED_JSON = os.path.join(_HERE, "..", "data", "bed.json")


def _packaged():
    with open(_BED_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def _args(**kw):
    base = dict(bind=None, host="127.0.0.1", port=8765)
    base.update(kw)
    return argparse.Namespace(**base)


def test_packaged_bind_lists_ipv4_and_ipv6():
    args = _args()
    bm._apply_bind_list_config(args, _packaged())
    assert bm._resolve_binds(args) == [("127.0.0.1", 8765), ("::1", 8765)]


def test_cli_bind_overrides_config():
    args = _args(bind=[("127.0.0.1", 9000)])
    bm._apply_bind_list_config(args, _packaged())
    assert bm._resolve_binds(args) == [("127.0.0.1", 9000)]


def test_bad_entry_is_skipped_and_good_entries_kept():
    cfg = {"bind": [
        {"host": "127.0.0.1"},            # missing port
        {"host": "::1", "port": 8765},
        {"host": "", "port": 8765},       # empty host
    ]}
    args = _args()
    bm._apply_bind_list_config(args, cfg)
    assert bm._resolve_binds(args) == [("::1", 8765)]


@pytest.mark.parametrize("port", [0, 70000])
def test_out_of_range_port_is_skipped(port):
    cfg = {"bind": [{"host": "127.0.0.1", "port": port}, {"host": "::1", "port": 8765}]}
    args = _args()
    bm._apply_bind_list_config(args, cfg)
    assert bm._resolve_binds(args) == [("::1", 8765)]
