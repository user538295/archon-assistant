"""Tests for PtySession — S1.1."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.pty_session import PtySession


@pytest.fixture
def mock_proc() -> MagicMock:
    proc = MagicMock()
    proc.isalive.return_value = True
    return proc


@pytest.fixture
def session() -> PtySession:
    return PtySession(command=["echo", "test"])


# --- start / is_alive ---

def test_is_alive_false_before_start(session: PtySession) -> None:
    assert not session.is_alive


def test_start_spawns_process(session: PtySession, mock_proc: MagicMock) -> None:
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        session.start()
        MockPty.spawn.assert_called_once_with(["echo", "test"])
        assert session.is_alive


def test_is_alive_false_after_process_exits(session: PtySession, mock_proc: MagicMock) -> None:
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        session.start()
        mock_proc.isalive.return_value = False
        assert not session.is_alive


# --- send ---

def test_send_writes_encoded_text(session: PtySession, mock_proc: MagicMock) -> None:
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        session.start()
        session.send("hello\n")
        mock_proc.write.assert_called_once_with(b"hello\n")


# --- read_stream ---

async def test_read_stream_yields_chunks_until_eof(
    session: PtySession, mock_proc: MagicMock
) -> None:
    mock_proc.read.side_effect = [b"chunk1", b"chunk2", EOFError()]
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        session.start()
        chunks = []
        async for chunk in session.read_stream():
            chunks.append(chunk)
    assert chunks == [b"chunk1", b"chunk2"]


# --- stop ---

async def test_stop_terminates_process_gracefully(
    session: PtySession, mock_proc: MagicMock
) -> None:
    # initial check → alive; loop poll → dead; final check → dead
    mock_proc.isalive.side_effect = [True, False, False]
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        session.start()
        await session.stop()
    mock_proc.terminate.assert_called_once_with(force=False)
    mock_proc.close.assert_not_called()
    assert not session.is_alive


async def test_stop_force_kills_if_process_does_not_exit(
    session: PtySession, mock_proc: MagicMock
) -> None:
    mock_proc.isalive.return_value = True  # never exits gracefully
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        with patch("asyncio.sleep", new_callable=AsyncMock):
            session.start()
            await session.stop()
    mock_proc.close.assert_called_once_with(force=True)
    assert not session.is_alive


async def test_stop_noop_when_not_started(session: PtySession) -> None:
    await session.stop()  # must not raise


async def test_stop_noop_when_process_already_dead(
    session: PtySession, mock_proc: MagicMock
) -> None:
    mock_proc.isalive.return_value = False
    with patch("archon.ai.pty_session.PtyProcess") as MockPty:
        MockPty.spawn.return_value = mock_proc
        session.start()
        await session.stop()
    mock_proc.terminate.assert_not_called()
