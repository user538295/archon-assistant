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


def test_uninstall_flag_dispatches() -> None:
    mock_mod = MagicMock()
    mock_mod.run_uninstall.return_value = 0
    with patch.dict(sys.modules, {"archon.cli.update": mock_mod}):
        from archon.cli.main import main
        result = main(["--uninstall"])
    assert mock_mod.run_uninstall.called
    assert result == 0


