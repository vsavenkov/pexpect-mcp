#!/usr/bin/env python
"""
Test pexpect-mcp server with interactive tools on Unix and Windows.

This test communicates with the MCP server using JSON-RPC and tests
spawning interactive processes with pexpect (Unix) or wexpect (Windows).
"""
import subprocess
import json
import sys


def send_jsonrpc(proc, obj):
    """Send a JSON-RPC message to the MCP server."""
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()


def read_response(proc):
    """Read a JSON-RPC response from the MCP server."""
    line = proc.stdout.readline()
    if line:
        return json.loads(line.strip())
    return None


def test_server():
    """Test the pexpect-mcp server with platform-specific interactive tools."""
    print(f"Running on platform: {sys.platform}")

    # Start the MCP server
    proc = subprocess.Popen(
        ["pexpect-mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0
    )

    try:
        # Test 1: Initialize server
        print("Test 1: Initialize MCP server...")
        send_jsonrpc(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        })
        init_response = read_response(proc)
        assert init_response is not None, "Failed to get init response"
        assert "result" in init_response, f"Init failed: {init_response}"
        assert init_response["result"]["serverInfo"]["name"] == "pexpect-mcp"
        print("  PASS: Server initialized")

        # Send initialized notification
        send_jsonrpc(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })

        # Test 2: Simple code execution
        print("Test 2: Execute simple Python code...")
        send_jsonrpc(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "pexpect_tool",
                "arguments": {
                    "code": "result = 2 + 2\nprint(f'Result: {result}')\nresult"
                }
            }
        })
        response = read_response(proc)
        assert response is not None, "Failed to get response"
        assert "result" in response, f"Tool call failed: {response}"
        content = response["result"]["content"][0]["text"]
        assert "4" in content, f"Expected 4 in result: {content}"
        assert "Result: 4" in content, f"Expected 'Result: 4' in output: {content}"
        print("  PASS: Simple code execution works")

        # Test 3: Spawn interactive process (platform-specific)
        print("Test 3: Spawn interactive process...")

        if sys.platform == "win32":
            # Windows: Use pywinpty wrapper for Python REPL
            code = '''
child = pexpect.spawn('python -i')
child.expect('>>>', timeout=10)
child.sendline('print(3 * 7)')
child.expect('21', timeout=5)
child.sendline('exit()')
child.expect(pexpect.EOF, timeout=5)
print('Interactive test passed')
'''
        else:
            # Unix: Use Python REPL
            code = '''
child = pexpect.spawn('python3 -i')
child.expect('>>>', timeout=10)
child.sendline('print(3 * 7)')
child.expect('21', timeout=5)
child.sendline('exit()')
child.expect(pexpect.EOF, timeout=5)
print('Interactive test passed')
'''

        send_jsonrpc(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "pexpect_tool",
                "arguments": {
                    "code": code,
                    "timeout": 30
                }
            }
        })
        response = read_response(proc)
        assert response is not None, "Failed to get response"
        assert "result" in response, f"Tool call failed: {response}"
        content = response["result"]["content"][0]["text"]
        assert "Interactive test passed" in content, f"Interactive test failed: {content}"
        print("  PASS: Interactive process spawning works")

        # Test 4: Test subprocess execution (useful for Windows)
        print("Test 4: Execute subprocess...")

        # Use sys.executable to ensure we get the correct Python
        if sys.platform == "win32":
            code = '''
import subprocess
import sys
# On Windows, redirect stdin to DEVNULL and use CREATE_NO_WINDOW
result = subprocess.run(
    [sys.executable, '-c', 'print(5 * 5)'],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=10,
    creationflags=subprocess.CREATE_NO_WINDOW
)
print(f'Output: {result.stdout.strip()}')
print(f'Return code: {result.returncode}')
'''
        else:
            code = '''
import subprocess
import sys
result = subprocess.run([sys.executable, '-c', 'print(5 * 5)'], capture_output=True, text=True, timeout=10)
print(f'Output: {result.stdout.strip()}')
print(f'Return code: {result.returncode}')
'''

        send_jsonrpc(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "pexpect_tool",
                "arguments": {
                    "code": code,
                    "timeout": 30
                }
            }
        })
        response = read_response(proc)
        assert response is not None, "Failed to get response"
        assert "result" in response, f"Tool call failed: {response}"
        content = response["result"]["content"][0]["text"]
        assert "25" in content, f"Expected 25 in result: {content}"
        assert "Return code: 0" in content, f"Expected successful return: {content}"
        print("  PASS: Subprocess execution works")

        # Test 5: Test timeout handling
        print("Test 5: Test timeout handling...")
        send_jsonrpc(proc, {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "pexpect_tool",
                "arguments": {
                    "code": "import time; time.sleep(10)",
                    "timeout": 2
                }
            }
        })
        response = read_response(proc)
        assert response is not None, "Failed to get response"
        assert "result" in response, f"Tool call failed: {response}"
        content = response["result"]["content"][0]["text"]
        assert "Timeout" in content or "timed out" in content, f"Expected timeout message: {content}"
        print("  PASS: Timeout handling works")

        print("\nAll tests passed!")
        return 0

    except AssertionError as e:
        print(f"\nTest FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    sys.exit(test_server())
