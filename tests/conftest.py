from __future__ import annotations

import os

import pytest


CORE_SKIP_FILES = {
    "tests/test_audit_v2_fixes.py": "full run/verifier hardening exercises the OS sandbox",
    "tests/test_demo.py": "demo run exercises verifier execution under the OS sandbox",
    "tests/test_egress_proxy.py": "binds localhost sockets",
    "tests/test_executor_backends.py": "framework executor tests require OS sandbox behavior",
    "tests/test_executor_network.py": "exercises sandboxed network/egress behavior",
    "tests/test_feedback.py": "binds a local HTTP server",
    "tests/test_gh.py": "binds a local fake GitHub HTTP server",
    "tests/test_merge_gate.py": "drives full merge/verifier loops under the OS sandbox",
    "tests/test_read_confine.py": "Seatbelt read-confinement integration tests",
    "tests/test_sandbox.py": "OS sandbox integration tests",
    "tests/test_sandbox_wiring.py": "OS sandbox wiring integration tests",
    "tests/test_serve.py": "binds local HTTP servers",
    "tests/test_verify_coverage.py": "drives full pytest-cov runs under the OS sandbox",
    "tests/test_verify_http.py": "binds a local HTTP server and drives full runs under the OS sandbox",
    "tests/test_verify_rubric.py": "drives full runs under the OS sandbox",
    "tests/test_soft_fail_closed.py": "sandbox fail-closed integration behavior",
    "tests/test_swarm_e2e.py": "end-to-end swarm execution",
}

CORE_SKIP_TESTS = {
    "tests/test_exit_codes.py::test_verified_done_exits_0": "full run executes commands under the OS sandbox",
    "tests/test_exit_codes.py::test_failing_verifier_exits_1": "full run executes commands under the OS sandbox",
    "tests/test_executor_sdk.py::test_adapter_drives_real_run_via_executor_name": "full run executes verifiers under the OS sandbox",
    "tests/test_module_sdk.py::test_module_verifier_grades_at_round_level": "executes hard verifiers under the OS sandbox",
    "tests/test_module_sdk.py::test_module_verifier_can_veto": "executes hard verifiers under the OS sandbox",
    "tests/test_module_sdk.py::test_module_verifier_never_runs_in_the_gate": "executes hard verifiers under the OS sandbox",
    "tests/test_run_json.py::test_verified_done_json_matches_schema": "full run executes commands under the OS sandbox",
    "tests/test_run_json.py::test_failing_run_json_matches_schema": "full run executes commands under the OS sandbox",
    "tests/test_run_json.py::test_json_file_written": "full run executes commands under the OS sandbox",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "sandbox: requires an OS sandbox such as bwrap or Seatbelt")
    config.addinivalue_line("markers", "network: binds localhost sockets or exercises network egress")
    config.addinivalue_line("markers", "integration: drives a full loophole run or external process boundary")


def pytest_collection_modifyitems(config, items):
    profile = os.environ.get("LOOPHOLE_TEST_PROFILE", "").strip().lower()
    if profile not in {"core", "ci-core"}:
        return

    for item in items:
        relpath = item.path.relative_to(config.rootpath).as_posix()
        nodeid = "{}::{}".format(relpath, item.name)
        reason = CORE_SKIP_TESTS.get(nodeid) or CORE_SKIP_FILES.get(relpath)
        if reason:
            item.add_marker(pytest.mark.skip(reason="skipped in LOOPHOLE_TEST_PROFILE=core: " + reason))
