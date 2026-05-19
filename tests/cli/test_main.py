from __future__ import annotations
import sys
import pytest
from unittest.mock import MagicMock, patch


def test_no_command_returns_0() -> None:
    from archon.cli.main import main
    result = main([])
    assert result == 0


def test_start_dispatches() -> None:
    import archon.cli.service as svc
    with patch.object(svc, "run_start", return_value=0) as mock:
        from archon.cli.main import main
        result = main(["start"])
    assert result == 0
    assert mock.called


def test_stop_dispatches() -> None:
    import archon.cli.service as svc
    with patch.object(svc, "run_stop", return_value=0) as mock:
        from archon.cli.main import main
        result = main(["stop"])
    assert result == 0
    assert mock.called


def test_restart_dispatches() -> None:
    import archon.cli.service as svc
    with patch.object(svc, "run_restart", return_value=0) as mock:
        from archon.cli.main import main
        result = main(["restart"])
    assert result == 0
    assert mock.called


def test_status_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_status.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.status": mock_mod}):
        from archon.cli.main import main
        result = main(["status"])
    assert mock_mod.run_status.called
    assert result == 0


def test_logs_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_logs.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.logs": mock_mod}):
        from archon.cli.main import main
        result = main(["logs"])
    assert mock_mod.run_logs.called


def test_update_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_update.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.update": mock_mod}):
        from archon.cli.main import main
        result = main(["update"])
    assert mock_mod.run_update.called


def test_version_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_version.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.update": mock_mod}):
        from archon.cli.main import main
        result = main(["version"])
    assert mock_mod.run_version.called


def test_doctor_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_doctor.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.doctor": mock_mod}):
        from archon.cli.main import main
        result = main(["doctor"])
    assert mock_mod.run_doctor.called


def test_config_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_config.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.config_cmd": mock_mod}):
        from archon.cli.main import main
        result = main(["config", "show"])
    assert mock_mod.run_config.called


def test_logs_passes_lines_arg() -> None:
    mock_mod = MagicMock()
    mock_mod.run_logs.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.logs": mock_mod}):
        from archon.cli.main import main
        main(["logs", "--lines", "100"])
    call_args = mock_mod.run_logs.call_args[0][0]
    assert call_args.lines == 100


def test_update_passes_tag_arg() -> None:
    mock_mod = MagicMock()
    mock_mod.run_update.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.update": mock_mod}):
        from archon.cli.main import main
        main(["update", "--tag", "26.4.0"])
    call_args = mock_mod.run_update.call_args[0][0]
    assert call_args.tag == "26.4.0"


def test_uninstall_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_uninstall.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.update": mock_mod}):
        from archon.cli.main import main
        result = main(["uninstall"])
    assert mock_mod.run_uninstall.called
    assert result == 0


def test_help_returns_0(capsys: pytest.CaptureFixture[str]) -> None:
    from archon.cli.main import main
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "archon" in out


def test_dash_h_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    from archon.cli.main import main
    result = main(["-h"])
    assert result == 0
    out = capsys.readouterr().out
    assert "archon" in out


def test_dash_dash_help_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    from archon.cli.main import main
    result = main(["--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "archon" in out


def test_main_search_collection_add_parses_path() -> None:
    """main(['rag', 'collection', 'add', '/some/path']) sets args.path and args.collection_command='add'."""
    import argparse
    captured: dict = {}

    def fake_run_search(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    mock_mod = MagicMock()
    mock_mod.run_search.side_effect = fake_run_search

    with patch.dict(sys.modules, {"archon.cli.search_cmd": mock_mod}):
        from archon.cli.main import main
        result = main(["search", "collection", "add", "/some/path"])

    assert result == 0
    assert captured["args"].path == "/some/path"
    assert captured["args"].collection_command == "add"
    assert "search_parser" in captured["kwargs"]
    assert "collection_parser" in captured["kwargs"]
    assert isinstance(captured["kwargs"]["search_parser"], argparse.ArgumentParser)
    assert isinstance(captured["kwargs"]["collection_parser"], argparse.ArgumentParser)


def test_main_search_collection_remove_parses_path_and_force() -> None:
    """main(['rag', 'collection', 'remove', '/path', '--force']) sets args.force=True."""
    import argparse
    captured: dict = {}

    def fake_run_search(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    mock_mod = MagicMock()
    mock_mod.run_search.side_effect = fake_run_search

    with patch.dict(sys.modules, {"archon.cli.search_cmd": mock_mod}):
        from archon.cli.main import main
        result = main(["search", "collection", "remove", "/path", "--force"])

    assert result == 0
    assert captured["args"].force is True
    assert captured["args"].path == "/path"
    assert "search_parser" in captured["kwargs"]
    assert "collection_parser" in captured["kwargs"]
    assert isinstance(captured["kwargs"]["search_parser"], argparse.ArgumentParser)
    assert isinstance(captured["kwargs"]["collection_parser"], argparse.ArgumentParser)


def test_main_search_no_subcommand_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    """main(["search"]) shows rag help and returns 0."""
    from archon.cli.main import main
    result = main(["search"])
    assert result == 0
    out = capsys.readouterr().out
    assert "collection" in out or "usage" in out.lower()


def test_main_search_help_subcommand_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    """main(["search", "help"]) shows rag help and returns 0."""
    from archon.cli.main import main
    result = main(["search", "help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "collection" in out or "usage" in out.lower()


def test_main_search_collection_no_subcommand_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    """main(["search", "collection"]) shows collection help and returns 0."""
    from archon.cli.main import main
    result = main(["search", "collection"])
    assert result == 0
    out = capsys.readouterr().out
    assert "add" in out or "remove" in out or "usage" in out.lower()


# --- voice subparser tests ---

def test_voice_install_dispatches() -> None:
    """main(['voice', 'install']) dispatches to run_voice."""
    mock_mod = MagicMock()
    mock_mod.run_voice.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.voice_cmd": mock_mod}):
        from archon.cli.main import main
        result = main(["voice", "install"])
    assert mock_mod.run_voice.called
    assert result == 0


def test_voice_status_dispatches() -> None:
    """main(['voice', 'status']) dispatches to run_voice."""
    mock_mod = MagicMock()
    mock_mod.run_voice.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.voice_cmd": mock_mod}):
        from archon.cli.main import main
        result = main(["voice", "status"])
    assert mock_mod.run_voice.called
    assert result == 0


def test_voice_no_subcommand_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """main(['voice']) returns 0 (shows help)."""
    from archon.cli.main import main
    result = main(["voice"])
    assert result == 0


def test_search_dispatch_still_works_after_voice_added() -> None:
    """Regression: adding voice subparser must not break rag dispatch."""
    mock_rag = MagicMock()
    mock_rag.run_search.return_value = 0
    mock_voice = MagicMock()
    mock_voice.run_voice.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.search_cmd": mock_rag, "archon.cli.voice_cmd": mock_voice}):
        from archon.cli.main import main
        result = main(["search", "status"])
    assert mock_rag.run_search.called
    assert not mock_voice.run_voice.called
    assert result == 0
