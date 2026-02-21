"""Smoke test — verifies the package is importable and the module structure is intact."""
import importlib


def test_package_importable():
    archon = importlib.import_module("archon")
    assert archon is not None


def test_submodules_importable():
    for submodule in ("archon.config", "archon.ai", "archon.chat", "archon.gateway"):
        mod = importlib.import_module(submodule)
        assert mod is not None


def test_gateway_start_not_implemented():
    import pytest
    from archon.gateway import Gateway

    with pytest.raises(NotImplementedError):
        Gateway.start()
