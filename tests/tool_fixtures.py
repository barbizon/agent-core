"""Module-level functions used as tools in tests only (process mode needs importable functions)."""

import os
import subprocess
import sys
import time

from agent_core.errors import PermanentToolError, TemporaryToolError

_calls = {"flaky": 0}


def echo(text: str) -> dict:
    return {"echo": text}


def noisy(text: str) -> dict:
    print("this goes to stdout and must not corrupt the response")
    return {"echo": text}


def sleep_forever(seconds: int) -> dict:
    time.sleep(seconds)
    return {"slept": seconds}


def spawn_grandchild_then_hang(pid_file: str) -> dict:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(pid_file, "w") as fh:
        fh.write(str(child.pid))
    time.sleep(60)
    return {}


def crash(code: int) -> dict:
    os._exit(code)


def fail_permanently(text: str) -> dict:
    raise PermanentToolError("nope")


def not_serializable(text: str) -> object:
    return object()


def flaky(text: str) -> dict:
    _calls["flaky"] += 1
    if _calls["flaky"] <= 2:
        raise TemporaryToolError("try again")
    return {"attempt": _calls["flaky"]}
