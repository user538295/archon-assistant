"""PTY session — spawns a process in a PTY and provides send/receive/stop."""
import asyncio
import logging

from ptyprocess import PtyProcess

logger = logging.getLogger("archon")

_GRACEFUL_POLL_INTERVAL = 0.1
_GRACEFUL_TIMEOUT = 5.0


class PtySession:
    """Manages a single process running inside a PTY."""

    DEFAULT_COMMAND = ["claude", "--dangerously-skip-permissions"]

    def __init__(self, command: list[str] | None = None) -> None:
        self._command: list[str] = command or self.DEFAULT_COMMAND
        self._proc: PtyProcess | None = None

    def start(self) -> None:
        """Spawn the process in a PTY."""
        self._proc = PtyProcess.spawn(self._command)
        logger.info("PTY session started: %s", self._command[0])

    def send(self, text: str) -> None:
        """Write text to the PTY stdin."""
        if self._proc is None:
            raise RuntimeError("Session not started")
        self._proc.write(text.encode())

    async def read_stream(self):  # type: ignore[return]
        """Async generator yielding raw output chunks until the process exits."""
        if self._proc is None:
            raise RuntimeError("Session not started")
        loop = asyncio.get_running_loop()
        while True:
            try:
                chunk: bytes = await loop.run_in_executor(None, self._proc.read, 4096)
                yield chunk
            except EOFError:
                break

    async def stop(self) -> None:
        """Terminate the process: SIGTERM first, SIGKILL after timeout."""
        if self._proc is None:
            return
        if not self._proc.isalive():
            self._proc = None
            return

        self._proc.terminate(force=False)

        elapsed = 0.0
        while elapsed < _GRACEFUL_TIMEOUT:
            if not self._proc.isalive():
                break
            await asyncio.sleep(_GRACEFUL_POLL_INTERVAL)
            elapsed += _GRACEFUL_POLL_INTERVAL

        if self._proc.isalive():
            self._proc.close(force=True)
            logger.warning("PTY session killed (SIGKILL): %s", self._command[0])
        else:
            logger.info("PTY session stopped: %s", self._command[0])

        self._proc = None

    @property
    def is_alive(self) -> bool:
        """True if the process is running."""
        return self._proc is not None and self._proc.isalive()
