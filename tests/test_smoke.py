"""Smoke test — verifies the package is importable and the module structure is intact."""
import importlib


def test_package_importable():
    archon = importlib.import_module("archon")
    assert archon is not None


def test_submodules_importable():
    for submodule in ("archon.config", "archon.ai", "archon.chat", "archon.gateway"):
        mod = importlib.import_module(submodule)
        assert mod is not None


def test_gateway_is_importable() -> None:
    from archon.gateway import Gateway

    assert callable(Gateway.start)


def test_main_function_is_importable_and_callable() -> None:
    """main.py must be importable and expose a callable main() function."""
    import main as main_module

    assert callable(main_module.main)


def test_main_function_calls_gateway_start() -> None:
    """main() must delegate to Gateway.start() exactly once."""
    from unittest.mock import patch
    import main as main_module

    with patch("main.Gateway.start") as mock_start:
        main_module.main()

    mock_start.assert_called_once()
