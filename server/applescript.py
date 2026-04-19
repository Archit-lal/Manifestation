"""
Send an iMessage via AppleScript / osascript.

Requires:
  - macOS (obviously)
  - Messages.app open (we activate it automatically)
  - Automation permission: System Settings → Privacy → Automation → Terminal → Messages ✓
"""

import subprocess
import time


def _escape(text: str) -> str:
    """Escape double quotes and backslashes for AppleScript string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send_imessage(handle: str, body: str, timeout: int = 8) -> None:
    """
    Send `body` to `handle` (phone number or email) via iMessage.
    Raises RuntimeError on failure.
    """
    escaped_handle = _escape(handle)
    escaped_body = _escape(body)

    script = f"""
tell application "Messages"
    activate
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy "{escaped_handle}" of targetService
    send "{escaped_body}" to targetBuddy
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "AppleScript timed out sending iMessage. "
            "Make sure Messages.app is open and your account is signed in."
        )
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        if "Not authorized" in err or "not allowed" in err.lower():
            raise RuntimeError(
                "macOS blocked Messages automation. "
                "Go to System Settings → Privacy & Security → Automation → "
                "enable Messages for your terminal app."
            )
        raise RuntimeError(f"osascript error: {err or result.stdout}")


def activate_messages() -> None:
    """Open Messages.app in the background so osascript works reliably."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Messages" to activate'],
            capture_output=True,
            timeout=5,
        )
        time.sleep(0.5)  # let it launch
    except Exception:
        pass
