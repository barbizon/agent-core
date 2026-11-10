import os
import time

import pytest

from agent_core.errors import ToolExecutionError
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.execution.thread_executor import run_in_thread
from tests import tool_fixtures as fx

REF = "tests.tool_fixtures:"
executor = ProcessExecutor(allowed_module_prefixes=("tests.",))


def error_type(fn, *args):
    with pytest.raises(ToolExecutionError) as exc:
        fn(*args)
    return exc.value.error_type


def test_thread_success_and_timeout():
    assert run_in_thread(fx.echo, {"text": "hi"}, 1, "echo") == {"echo": "hi"}
    start = time.monotonic()
    assert error_type(run_in_thread, fx.sleep_forever, {"seconds": 5}, 0.2, "s") == "timeout"
    assert time.monotonic() - start < 1


def test_thread_maps_tool_errors():
    assert error_type(run_in_thread, fx.fail_permanently, {"text": "x"}, 1, "f") == "permanent_failure"
    assert error_type(run_in_thread, fx.echo, {"wrong": "x"}, 1, "e") == "execution_error"


def test_process_success_and_stdout_isolation():
    assert executor.execute(REF + "echo", {"text": "hi"}, 5) == {"echo": "hi"}
    assert executor.execute(REF + "noisy", {"text": "hi"}, 5) == {"echo": "hi"}


def test_process_timeout_kills_process_group(tmp_path):
    pid_file = tmp_path / "pid"
    start = time.monotonic()
    assert error_type(executor.execute, REF + "spawn_grandchild_then_hang",
                      {"pid_file": str(pid_file)}, 1.5) == "timeout"
    assert time.monotonic() - start < 4
    grandchild = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)


@pytest.mark.parametrize("ref,args,expected", [
    ("agent_core.config:load_settings", {}, "permission_denied"),  # outside allowed prefix
    (REF + "does_not_exist", {}, "unknown_tool"),
    (REF + "crash", {"code": 3}, "worker_crashed"),
    (REF + "fail_permanently", {"text": "x"}, "permanent_failure"),
    (REF + "not_serializable", {"text": "x"}, "execution_error"),
])
def test_process_failures(ref, args, expected):
    assert error_type(executor.execute, ref, args, 5) == expected
