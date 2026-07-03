"""Tests for the startup health checker."""

from unittest.mock import patch

from core.health import (
    CheckResult,
    HealthReport,
    _check_import,
    check_anthropic,
    check_chromadb,
    check_network_ports,
    run_health_check,
)


class TestCheckResult:
    def test_ok_result(self):
        r = CheckResult(name="foo", ok=True, message="1.0")
        assert r.ok
        assert r.critical  # default

    def test_failed_critical(self):
        r = CheckResult(name="bar", ok=False, message="missing", critical=True)
        assert not r.ok
        assert r.critical


class TestHealthReport:
    def test_all_ok(self):
        report = HealthReport(
            results=[
                CheckResult("a", ok=True, message="ok"),
                CheckResult("b", ok=True, message="ok"),
            ]
        )
        assert report.all_critical_ok
        assert report.failures == []

    def test_critical_failure_blocks(self):
        report = HealthReport(
            results=[
                CheckResult("a", ok=True, message="ok"),
                CheckResult("b", ok=False, message="err", critical=True),
            ]
        )
        assert not report.all_critical_ok
        assert len(report.failures) == 1

    def test_optional_failure_does_not_block(self):
        report = HealthReport(
            results=[
                CheckResult("a", ok=True, message="ok"),
                CheckResult("b", ok=False, message="optional missing", critical=False),
            ]
        )
        assert report.all_critical_ok
        assert len(report.failures) == 1


class TestIndividualChecks:
    def test_check_import_present(self):
        r = _check_import("json", "json")
        assert r.ok

    def test_check_import_missing(self):
        r = _check_import("nonexistent_package_xyz_123", "nonexistent")
        assert not r.ok
        assert "pip install" in r.fix_hint

    def test_check_network_ports_free(self):
        # Use high ports unlikely to be in use
        r = check_network_ports(ws_port=19988, api_port=19989)
        assert r.ok

    def test_check_network_ports_busy(self):
        import socket

        # Bind a socket to simulate a busy port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            r = check_network_ports(ws_port=port, api_port=19990)
            assert not r.ok
            assert str(port) in r.message
        finally:
            s.close()

    def test_check_anthropic_not_installed(self):
        with patch.dict("sys.modules", {"anthropic": None}):
            r = check_anthropic()
            # anthropic is optional -- failure is non-critical
            assert not r.critical

    def test_check_chromadb_not_installed(self):
        with patch.dict("sys.modules", {"chromadb": None}):
            r = check_chromadb()
            assert not r.critical


class TestRunHealthCheck:
    def test_runs_without_crash(self):
        report = run_health_check(ws_port=19988, api_port=19989, verbose=False)
        assert isinstance(report, HealthReport)
        assert len(report.results) > 0

    def test_print_report_no_crash(self, capsys):
        report = run_health_check(ws_port=19988, api_port=19989, verbose=False)
        report.print_report()
        out = capsys.readouterr().out
        assert "AgentMax" in out
