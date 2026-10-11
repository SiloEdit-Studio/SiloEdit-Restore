"""Manage persistent isolated AI runners for one scan."""
from __future__ import annotations

from collections import deque
import json
import queue
import subprocess
import threading


class PersistentModelRunner:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.process: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[str | None] = queue.Queue()
        self.stderr_lines: deque[str] = deque(maxlen=40)
        self.stderr_thread: threading.Thread | None = None
        self.stdout_thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.responses.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self.responses.put(None)

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            for line in self.process.stderr:
                self.stderr_lines.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def _start(self, timeout: float) -> None:
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        response = self._receive(timeout, "model startup")
        if not response.get("ready"):
            detail = response.get("error") or self._stderr_detail()
            self.close()
            raise RuntimeError(detail or "Persistent AI runner did not initialize.")

    def _receive(self, timeout: float, operation: str) -> dict:
        try:
            line = self.responses.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError(f"AI runner timed out during {operation}.") from exc
        if line is None:
            return_code = self.process.poll() if self.process else None
            detail = self._stderr_detail()
            self.close()
            raise RuntimeError(
                f"AI runner exited during {operation} (exit code {return_code})."
                + (f" {detail}" if detail else "")
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            self.close()
            raise RuntimeError(
                f"AI runner returned invalid protocol data during {operation}: {line[:300]!r}"
            ) from exc
        if not isinstance(response, dict):
            self.close()
            raise RuntimeError(f"AI runner returned an invalid response during {operation}.")
        return response

    def _stderr_detail(self) -> str:
        return "\n".join(self.stderr_lines)[-1200:]

    def run(
        self,
        source: str,
        destination: str,
        startup_timeout: float = 600,
        request_timeout: float = 600,
    ) -> str:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                self.close()
                self._start(startup_timeout)
            assert self.process is not None and self.process.stdin is not None
            request = json.dumps({"source": source, "destination": destination})
            try:
                self.process.stdin.write(request + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                detail = self._stderr_detail()
                self.close()
                raise RuntimeError(
                    "Could not send work to the AI runner."
                    + (f" {detail}" if detail else "")
                ) from exc
            response = self._receive(request_timeout, "inference")
            if not response.get("ok"):
                raise RuntimeError(response.get("error") or "AI inference failed.")
            return str(response.get("message", ""))

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self.stdout_thread and self.stdout_thread is not threading.current_thread():
            self.stdout_thread.join(timeout=1)
        if self.stderr_thread and self.stderr_thread is not threading.current_thread():
            self.stderr_thread.join(timeout=1)
        self.stdout_thread = None
        self.stderr_thread = None
        self.responses = queue.Queue()


class AIInferenceSession:
    def __init__(self) -> None:
        self.runners: dict[tuple[str, ...], PersistentModelRunner] = {}
        self.lock = threading.Lock()

    def run(
        self,
        key: tuple[str, ...],
        command: list[str],
        source: str,
        destination: str,
    ) -> str:
        with self.lock:
            runner = self.runners.get(key)
            if runner is None:
                runner = PersistentModelRunner(command)
                self.runners[key] = runner
        return runner.run(source, destination)

    def close(self) -> None:
        for runner in self.runners.values():
            runner.close()
        self.runners.clear()


_SESSION_LOCAL = threading.local()


def start_ai_inference_session() -> None:
    _SESSION_LOCAL.session = AIInferenceSession()


def run_persistent_inference(
    key: tuple[str, ...],
    command: list[str],
    source: str,
    destination: str,
) -> str | None:
    session = getattr(_SESSION_LOCAL, "session", None)
    if session is None:
        return None
    return session.run(key, command, source, destination)


def close_ai_inference_session() -> None:
    session = getattr(_SESSION_LOCAL, "session", None)
    if session is not None:
        try:
            session.close()
        finally:
            del _SESSION_LOCAL.session
