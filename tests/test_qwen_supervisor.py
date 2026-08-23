import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "qwen_supervisor", Path(__file__).parents[1] / "qwen_worker" / "supervisor.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
restart_decision = _MODULE.restart_decision


def test_nonzero_qwen_exit_is_restarted_with_bounded_backoff():
    assert restart_decision(1, 0) == (True, 1.0)
    assert restart_decision(1, 5) == (True, 30.0)


def test_clean_qwen_exit_is_not_restarted():
    assert restart_decision(0, 0) == (False, 0.0)
