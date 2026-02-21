"""S5.8 — Live unit tests: PtySession with real /bin/bash process, no mocks."""
import asyncio
import pytest

from archon.ai.pty_session import PtySession

pytestmark = pytest.mark.live

_CAT_CMD = ["/bin/bash", "-c", "cat"]


async def test_live_pty_start_sets_is_alive() -> None:
    session = PtySession(command=_CAT_CMD)
    session.start()
    try:
        assert session.is_alive
    finally:
        await session.stop()


async def test_live_pty_send_and_receive_via_read_stream() -> None:
    """send() text → read_stream() yields a chunk containing that text within 5s."""
    session = PtySession(command=_CAT_CMD)
    session.start()

    collected: list[bytes] = []

    async def collect() -> None:
        async for chunk in session.read_stream():
            collected.append(chunk)

    task = asyncio.create_task(collect())
    session.send("hello\n")

    # Poll until we see "hello" in the output or 5 s pass
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if b"hello" in b"".join(collected):
            break
        await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await session.stop()

    assert b"hello" in b"".join(collected)


async def test_live_pty_stop_terminates_process() -> None:
    session = PtySession(command=_CAT_CMD)
    session.start()
    assert session.is_alive
    await session.stop()
    assert not session.is_alive


async def test_live_pty_double_stop_is_noop() -> None:
    session = PtySession(command=_CAT_CMD)
    session.start()
    await session.stop()
    await session.stop()  # must not raise


async def test_live_pty_stop_without_start_is_noop() -> None:
    session = PtySession(command=_CAT_CMD)
    await session.stop()  # must not raise


async def test_live_pty_read_stream_ends_when_process_exits_naturally() -> None:
    """read_stream() terminates by itself when the command exits."""
    session = PtySession(command=["/bin/bash", "-c", "echo done"])
    session.start()

    chunks: list[bytes] = []
    async with asyncio.timeout(5.0):
        async for chunk in session.read_stream():
            chunks.append(chunk)

    assert b"done" in b"".join(chunks)
    assert not session.is_alive
