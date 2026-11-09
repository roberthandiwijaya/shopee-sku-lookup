"""Release-only pytest policy: skips/xfails/collection errors cannot look green."""
from pathlib import Path

import pytest


_rejected = []


def pytest_sessionstart(session):
    Path("/results").mkdir(exist_ok=True)


def pytest_runtest_logreport(report):
    if report.skipped or hasattr(report, "wasxfail"):
        _rejected.append(report.nodeid)


def pytest_collectreport(report):
    if report.skipped:
        _rejected.append(report.nodeid)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if _rejected:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_sep("=", "Release gate rejects skipped/xfail tests")
            for nodeid in sorted(set(_rejected)):
                reporter.write_line(nodeid)
