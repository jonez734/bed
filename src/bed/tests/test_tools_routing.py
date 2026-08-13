"""Tests for :mod:`bed.tools._routing`.

Covers:

- :func:`build_client_args` registers every ``--bed-*`` flag + the
  ``--direct`` opt-out with the expected defaults and CLI overrides.
- :func:`select_backend` returns ``"direct"`` immediately when
  ``--direct`` is set (the probe is skipped).
- :func:`select_backend` returns ``"bed"`` when the probe says the
  daemon is reachable.
- :func:`select_backend` raises :class:`BedNotReachable` with a
  one-line operator-facing message when the probe says unreachable
  and ``--direct`` was not requested.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from bed.tools import _routing


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    _routing.build_client_args(parser)
    return parser.parse_args(argv)


# ---------------------------------------------------------------------
# build_client_args


class TestBuildClientArgs:
    def test_defaults_match_empyre(self):
        ns = _parse_args([])
        assert ns.bed_host == "localhost"
        assert ns.bed_port == 8765
        assert ns.bed_path == "/"
        assert ns.bed_call_timeout == 5.0
        assert ns.bed_probe_timeout == 0.25
        assert ns.direct is False

    def test_cli_overrides(self):
        ns = _parse_args(
            [
                "--bed-host",
                "bed.internal",
                "--bed-port",
                "9999",
                "--bed-path",
                "/api",
                "--bed-call-timeout",
                "30",
                "--bed-probe-timeout",
                "1.5",
            ]
        )
        assert ns.bed_host == "bed.internal"
        assert ns.bed_port == 9999
        assert ns.bed_path == "/api"
        assert ns.bed_call_timeout == 30.0
        assert ns.bed_probe_timeout == 1.5

    def test_direct_flag_sets_true(self):
        ns = _parse_args(["--direct"])
        assert ns.direct is True


# ---------------------------------------------------------------------
# select_backend


class TestSelectBackendDirect:
    def test_direct_skips_probe(self):
        """``--direct`` must short-circuit without touching the probe."""
        args = argparse.Namespace(direct=True, bed_host="x", bed_port=1)
        with patch(
            "bed.tools._routing.probe_bed"
        ) as probe:
            assert _routing.select_backend(args) == "direct"
        probe.assert_not_called()

    def test_direct_default_false_uses_probe(self):
        args = argparse.Namespace(direct=False, bed_host="x", bed_port=1)
        with patch(
            "bed.tools._routing.probe_bed", return_value=True
        ) as probe:
            assert _routing.select_backend(args) == "bed"
        probe.assert_called_once_with(args)


class TestSelectBackendReachable:
    def test_returns_bed(self):
        args = argparse.Namespace(
            direct=False, bed_host="localhost", bed_port=8765
        )
        with patch("bed.tools._routing.probe_bed", return_value=True):
            assert _routing.select_backend(args) == "bed"


class TestSelectBackendUnreachable:
    def test_raises_bed_not_reachable(self):
        args = argparse.Namespace(
            direct=False, bed_host="nope.example", bed_port=9999
        )
        with patch("bed.tools._routing.probe_bed", return_value=False):
            with pytest.raises(_routing.BedNotReachable) as ei:
                _routing.select_backend(args)
        assert ei.value.host == "nope.example"
        assert ei.value.port == 9999
        assert "bed unreachable at nope.example:9999" in str(ei.value)
        assert "rerun with --direct" in str(ei.value)

    def test_message_includes_host_and_port(self):
        args = argparse.Namespace(
            direct=False, bed_host="10.0.0.1", bed_port=1234
        )
        with patch("bed.tools._routing.probe_bed", return_value=False):
            with pytest.raises(_routing.BedNotReachable):
                _routing.select_backend(args)


class TestBedNotReachableMessage:
    def test_str_contains_endpoint_and_direct_hint(self):
        exc = _routing.BedNotReachable("h", 9)
        s = str(exc)
        assert "h:9" in s
        assert "--direct" in s
