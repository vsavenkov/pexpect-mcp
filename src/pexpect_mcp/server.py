import sys
import re
import time
import traceback
import threading
from io import StringIO
from typing import Any, Dict, Optional, Union, List
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# Platform-specific imports
if sys.platform != "win32":
    import signal
    HAS_SIGALRM = True
else:
    HAS_SIGALRM = False

# Import pexpect or create pywinpty wrapper based on platform
if sys.platform == "win32":
    from winpty import PtyProcess

    class EOF:
        """Sentinel for end-of-file."""
        pass

    class TIMEOUT:
        """Sentinel for timeout."""
        pass

    class WinPtySpawn:
        """Pexpect-like wrapper around pywinpty for Windows."""

        EOF = EOF
        TIMEOUT = TIMEOUT

        def __init__(self, command: str):
            """Spawn a process using pywinpty."""
            self.proc = PtyProcess.spawn(command)
            self.buffer = ""
            self.before = ""
            self.after = ""
            self.match = None
            self.timeout = 30  # default timeout
            self._lock = threading.Lock()
            self._reader_thread = None
            self._stop_reader = False
            self._start_reader_thread()

        def _start_reader_thread(self):
            """Start a background thread to continuously read from the process."""
            def reader():
                while not self._stop_reader and self.proc.isalive():
                    try:
                        # Read in small chunks
                        data = self.proc.read(1)
                        if data:
                            with self._lock:
                                self.buffer += data
                    except:
                        break
                    time.sleep(0.001)  # Small sleep to prevent CPU spin

            self._reader_thread = threading.Thread(target=reader, daemon=True)
            self._reader_thread.start()

        def expect(self, pattern: Union[str, type, List], timeout: Optional[int] = None) -> int:
            """Wait for pattern to appear in output.

            Args:
                pattern: String pattern, EOF, TIMEOUT, or list of patterns
                timeout: Timeout in seconds (uses self.timeout if None)

            Returns:
                Index of matched pattern (0 if single pattern)
            """
            if timeout is None:
                timeout = self.timeout

            # Handle list of patterns
            if isinstance(pattern, list):
                patterns = pattern
            else:
                patterns = [pattern]

            start_time = time.time()

            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    with self._lock:
                        buf_snapshot = self.buffer[:500]
                    raise TimeoutError(f"Timeout waiting for pattern after {timeout}s. Buffer: {repr(buf_snapshot)}")

                # Check for EOF
                if not self.proc.isalive():
                    # Give reader thread time to finish
                    time.sleep(0.1)

                    # Check if any pattern is EOF
                    for i, p in enumerate(patterns):
                        if p is EOF or p == EOF:
                            with self._lock:
                                self.before = self.buffer
                                self.after = ""
                                self.buffer = ""
                            return i

                    with self._lock:
                        buf_snapshot = self.buffer
                    raise EOFError(f"Process ended without matching pattern. Buffer: {repr(buf_snapshot)}")

                # Check patterns against buffer (thread-safe)
                with self._lock:
                    for i, p in enumerate(patterns):
                        if p is EOF or p == EOF:
                            continue  # EOF checked above
                        if p is TIMEOUT or p == TIMEOUT:
                            continue  # TIMEOUT handled by timeout logic

                        # String pattern matching
                        if isinstance(p, str):
                            match = re.search(p, self.buffer)
                            if match:
                                self.before = self.buffer[:match.start()]
                                self.after = match.group()
                                self.match = match
                                self.buffer = self.buffer[match.end():]
                                return i

                # Small sleep to avoid busy waiting
                time.sleep(0.01)

        def sendline(self, text: str = "") -> None:
            """Send text followed by newline."""
            self.proc.write(text + "\r\n")

        def send(self, text: str) -> None:
            """Send text without newline."""
            self.proc.write(text)

        def read(self, size: int = -1) -> str:
            """Read from process output."""
            if size == -1:
                return self.proc.read()
            else:
                return self.proc.read(size)

        def isalive(self) -> bool:
            """Check if process is still running."""
            return self.proc.isalive()

        def close(self) -> None:
            """Close the process."""
            self._stop_reader = True
            if self._reader_thread:
                self._reader_thread.join(timeout=1)
            try:
                self.proc.close()
            except:
                pass

        def __del__(self):
            """Cleanup on deletion."""
            self.close()

    # Create pexpect-like module interface
    class PexpectModule:
        """Module-like object providing pexpect interface using pywinpty."""
        EOF = EOF
        TIMEOUT = TIMEOUT
        spawn = WinPtySpawn

    pexpect = PexpectModule()
else:
    import pexpect

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("pexpect-mcp")

# Global timeout setting (can be overridden by tool caller)
TIMEOUT = 12
# Default pexpect timeout (2 seconds less than hard timeout to avoid signal timeout)
DEFAULT_PEXPECT_TIMEOUT = TIMEOUT - 2

pexpect_session: Optional[pexpect.spawn] = None
session_globals: Dict[str, Any] = {}


class TimeoutError(Exception):
    """Raised when pexpect operation times out."""

    pass


if HAS_SIGALRM:
    def timeout_handler(signum, frame):
        """Signal handler for timeout."""
        raise TimeoutError("Operation timed out after {} seconds".format(TIMEOUT))


def safe_str(obj: Any) -> str:
    """Safely convert object to string, handling bytes and other types."""
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except:
            return repr(obj)
    return str(obj)


@mcp.tool()
def pexpect_tool(code: str, timeout: Optional[int] = None) -> str:
    """Execute Python code in a pexpect session. Can spawn processes and interact with them.

    Args:
        code: Python code to execute. Use 'child' variable to interact with the spawned process.
        The pexpect library is already imported.  Use `pexpect.spawn(...)` to spawn something.
        timeout: Optional timeout in seconds. If not provided, uses global TIMEOUT (default 10s).
        DONT WRITE COMMENTS

    Example:
        child = pexpect.spawn('lldb ./mytool')
        child.expect("(lldb)")

    Platform Support:
        - Unix: Uses native pexpect with PTY support
        - Windows: Uses pywinpty wrapper with ConPTY (Windows 10 1809+)

    Returns:
        The result of the code execution or an error message.

    When asked to dump out a pexpect transcript, make sure to dump with uv --script dependency info
    so the user can run it with "uv run session.py"
    """
    if not code:
        return "No code provided"

    global pexpect_session, session_globals, TIMEOUT

    # Use provided timeout or global default
    actual_timeout = timeout if timeout is not None else TIMEOUT
    # Use shorter timeout for pexpect operations
    pexpect_timeout = (
        min(actual_timeout - 2, DEFAULT_PEXPECT_TIMEOUT) if actual_timeout > 2 else 1
    )

    # Set up print capture
    captured_output = StringIO()
    original_print = print

    def captured_print(*args, **kwargs):
        # Print to our capture buffer
        kwargs_copy = kwargs.copy()
        kwargs_copy["file"] = captured_output
        original_print(*args, **kwargs_copy)
        # Also print to stdout for debugging if needed
        original_print(*args, **kwargs)

    # Set up the execution environment
    local_vars = session_globals.copy()
    local_vars["pexpect"] = pexpect
    local_vars["print"] = captured_print  # Inject our custom print function

    # If we have an active session, make it available as 'child'
    if pexpect_session is not None:
        local_vars["child"] = pexpect_session
        # Set default timeout for pexpect operations
        pexpect_session.timeout = pexpect_timeout

    if HAS_SIGALRM:
        # Unix: Use signal-based timeout
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(actual_timeout)

        try:
            # Try to execute as an expression first
            result = eval(code, {"__builtins__": __builtins__}, local_vars)
            _update_globals(local_vars, pexpect_timeout)

            # Format the response
            return _format_response(result, captured_output.getvalue())

        except SyntaxError:
            # If it's not an expression, try executing as a statement
            try:
                exec(code, {"__builtins__": __builtins__}, local_vars)
                _update_globals(local_vars, pexpect_timeout)
                return _format_response(
                    "Code executed successfully", captured_output.getvalue()
                )

            except Exception as exec_error:
                return _format_response(f"Error: {exec_error}", captured_output.getvalue())

        except TimeoutError as timeout_error:
            # Format timeout error with traceback
            tb = traceback.format_exc()
            error_msg = f"Timeout Error: {timeout_error}\n\nTraceback:\n{tb}"
            return _format_response(error_msg, captured_output.getvalue())

        except Exception as eval_error:
            return _format_response(f"Error: {eval_error}", captured_output.getvalue())

        finally:
            # Always clean up the alarm and restore old handler
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows: Use threading-based timeout
        def execute_code():
            try:
                # Try to execute as an expression first
                result = eval(code, {"__builtins__": __builtins__}, local_vars)
                _update_globals(local_vars, pexpect_timeout)
                return _format_response(result, captured_output.getvalue())
            except SyntaxError:
                # If it's not an expression, try executing as a statement
                exec(code, {"__builtins__": __builtins__}, local_vars)
                _update_globals(local_vars, pexpect_timeout)
                return _format_response(
                    "Code executed successfully", captured_output.getvalue()
                )

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(execute_code)
                result = future.result(timeout=actual_timeout)
                return result

        except FuturesTimeoutError:
            error_msg = f"Timeout Error: Operation timed out after {actual_timeout} seconds"
            return _format_response(error_msg, captured_output.getvalue())

        except Exception as error:
            return _format_response(f"Error: {error}", captured_output.getvalue())


def _format_response(result, log_output):
    """Format the response with RESULT, LOG, and BUFFER sections."""
    response_parts = [
        safe_str(result) if result is not None else "",
        "---",
        log_output.strip() if log_output else "(no output)",
    ]

    return "\n".join(response_parts)


def _update_globals(local_vars, pexpect_timeout):
    global pexpect_session
    for key, value in local_vars.items():
        if key not in [
            "__builtins__",
            "pexpect",
            "print",
        ]:  # Don't persist the custom print function
            session_globals[key] = value
            # If a 'child' variable was created/modified, update our session
            if key == "child" and isinstance(value, pexpect.spawn):
                pexpect_session = value
                # Set default timeout for the new session
                pexpect_session.timeout = pexpect_timeout


def main():
    """Main entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
