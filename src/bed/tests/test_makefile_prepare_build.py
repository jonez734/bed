"""PREPARE_BUILD moves a build/ tree aside when the current user cannot clear it.

A build/ left by an earlier run is often owned by another account, and with the sticky bit set
its files cannot be removed by the deploy user. The deploy then fails on the stale files. The
check must move the directory aside whenever it is not writable by the current user, not only
when it is owned by someone else. A rename needs write access on the parent, which the deploy
user has.
"""

import re
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[3] / "Makefile"


def _prepare_build_block() -> str:
    text = MAKEFILE.read_text()
    m = re.search(r"^PREPARE_BUILD = \\\n(.*?)\n\n", text, re.MULTILINE | re.DOTALL)
    assert m, "PREPARE_BUILD not found"
    return m.group(1)


@pytest.mark.unit
def test_stale_build_is_moved_aside_when_not_writable_by_the_deploy_user():
    block = _prepare_build_block()
    assert "-w $(1)/build" in block or "! -w" in block, block


@pytest.mark.unit
def test_stale_build_is_still_moved_when_owned_by_someone_else():
    block = _prepare_build_block()
    assert "-O" in block or "! -w" in block, block


@pytest.mark.unit
def test_build_directory_is_recreated_after_the_move():
    block = _prepare_build_block()
    assert "mkdir -p $(1)/build" in block, block


@pytest.mark.unit
def test_clean_moves_build_aside_instead_of_deleting_it():
    """deploy-venv depends on `clean`. `rm -rf build` fails on a tree the deploy user cannot
    remove, which stops the deploy before PREPARE_BUILD runs. clean must rename build/ aside."""
    text = MAKEFILE.read_text()
    m = re.search(r"^clean:.*?(?=^\S)", text, re.MULTILINE | re.DOTALL)
    assert m, "clean target not found"
    body = m.group(0)
    assert "rm -rf build " not in body and "rm -rf build\n" not in body, body
    assert "build.stale" in body, body


@pytest.mark.unit
def test_clean_does_not_delete_the_tree_it_just_moved_aside():
    """clean moves build/ to build.stale.<pid>. A later `rm -rf build.stale.*` in the same target
    would delete that tree, so the removal must not cover build.stale."""
    text = MAKEFILE.read_text()
    m = re.search(r"^clean:.*?(?=^\S)", text, re.MULTILINE | re.DOTALL)
    body = m.group(0)
    removals = [l for l in body.splitlines() if "rm -rf" in l]
    assert not any("build.stale" in l for l in removals), removals
