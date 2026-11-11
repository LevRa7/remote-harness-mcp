"""Registry hardening (REV-SEC-15, first slice): atomic writes, 0600, fail closed.

The old writer truncated ``registry.json`` in place. A crash during the write, or
two writers at once, left a half-file which ``load_registry`` then treated as an
empty registry - every node vanished and their names became registrable again.
"""

import json
import os
import stat

import pytest

import gateway


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))
    return path


def test_save_is_atomic_and_round_trips(registry):
    gateway.save_registry({"node-one": {"token_sha256": "a" * 64, "link_key": "lk_" + "b" * 32}})
    data = gateway.load_registry()
    assert data["node-one"]["token_sha256"] == "a" * 64
    assert not list(registry.parent.glob(".registry-*.tmp")), "temp files must be cleaned up"


def test_registry_file_is_user_only(registry):
    gateway.save_registry({"node-one": {"token_sha256": "a" * 64}})
    if os.name != "nt":
        mode = stat.S_IMODE(registry.stat().st_mode)
        assert mode & 0o077 == 0, "registry must not be group/world readable"


def test_corrupt_registry_is_not_empty_and_blocks_control_plane(registry):
    registry.write_text("{not-json", encoding="utf-8")
    assert gateway.load_registry() == {}
    assert gateway.registry_corrupt() is True

    # A successful save repairs the state and re-enables registration.
    gateway.save_registry({"node-one": {"token_sha256": "a" * 64}})
    assert gateway.registry_corrupt() is False
    assert "node-one" in gateway.load_registry()


def test_json_array_root_is_treated_as_corrupt(registry):
    registry.write_text("[]", encoding="utf-8")
    gateway.load_registry()
    assert gateway.registry_corrupt() is True


def test_missing_registry_is_not_corrupt(registry):
    assert gateway.load_registry() == {}
    assert gateway.registry_corrupt() is False
