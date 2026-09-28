"""Static and argument-parsing checks for scripts/cos-install.sh.

The install script itself needs root, apt, systemd and PostgreSQL to run
for real, so it cannot be exercised end-to-end here. These tests cover
what can be verified without any of that: bash syntax, regression guards
against reintroducing destructive behavior, and the argument-parsing /
validation code path, which runs (and can fail via `die()`) before the
root check - so it is reachable without privileges.

See INSTALL.md's troubleshooting section and the final task summary for
the full list of behaviors that still require manual validation on a real
controller/agent machine (portal safe-swap under a real build failure,
nginx rollback under a real bad config, systemd unit diffing across an
actual upgrade, backup/restore against a real PostgreSQL instance, etc.).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cos-install.sh"


def _run(args: list[str], timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT.read_text()


# --- Static checks -----------------------------------------------------------


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "cos-install.sh should be executable"


def test_bash_syntax_is_valid() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_strict_mode_enabled(script_text: str) -> None:
    assert "set -euo pipefail" in script_text
    assert "set -E" in script_text, "ERR trap must be inherited into functions (set -E)"


def test_never_deletes_admin_password(script_text: str) -> None:
    """Regression guard: a re-run must never wipe the generated admin password."""
    assert "rm -f /opt/cos/admin_password" not in script_text
    assert re.search(r"rm\s+-f\s+/opt/cos/admin_password", script_text) is None


def test_never_uses_destructive_git_or_reset(script_text: str) -> None:
    assert "git reset --hard" not in script_text
    assert "git clean -f" not in script_text
    assert "git pull --ff-only" in script_text


def test_git_update_discards_only_tracked_changes(script_text: str) -> None:
    assert "git checkout -- ." in script_text


def test_venv_created_only_if_missing(script_text: str) -> None:
    assert "-x /opt/cos/venv/bin/python" in script_text


def test_pip_install_is_force_reinstall_no_deps(script_text: str) -> None:
    assert "--force-reinstall" in script_text
    assert "--no-cache-dir" in script_text
    assert "--no-deps" in script_text
    assert "pip install -r" in script_text, "dependencies must be installed separately from the COS package"


def test_portal_uses_safe_atomic_swap(script_text: str) -> None:
    assert "/opt/cos/portal.new" in script_text
    assert "mv -T /opt/cos/portal.new /opt/cos/portal" in script_text


def test_nginx_config_validated_before_reload(script_text: str) -> None:
    test_idx = script_text.index("nginx -t")
    reload_idx = script_text.index("systemctl reload nginx")
    assert test_idx < reload_idx, "nginx -t must run before the config is reloaded"


def test_nginx_backs_up_before_overwrite(script_text: str) -> None:
    assert ".bak-${TS}" in script_text


def test_systemd_units_only_rewritten_when_changed(script_text: str) -> None:
    assert "install_unit_if_changed" in script_text
    assert script_text.count("install_unit_if_changed /etc/systemd/system/") == 2


def test_backup_and_restore_flags_present(script_text: str) -> None:
    assert "--backup" in script_text
    assert "--restore" in script_text
    assert "pg_dump cos" in script_text
    assert "DROP DATABASE IF EXISTS cos" in script_text


def test_step_numbering_is_internally_consistent(script_text: str) -> None:
    """STEP_TOTAL per path must match the number of step() calls it reaches.

    Guards against silently drifting "[N/total]" output the next time a
    step is added or removed from one branch but not the constants above.
    """
    step_calls = len(re.findall(r'^\s*step "', script_text, re.MULTILINE))

    # Non-overlapping regions in file order: do_backup() and do_restore() are
    # defined (but not yet called) before the COMMON section; the CONTROLLER
    # section later just *calls* do_restore(), so its step() calls are only
    # counted once, under "restore".
    backup_start = script_text.index("do_backup() {")
    restore_start = script_text.index("do_restore() {")
    common_start = script_text.index("# COMMON (steps")
    controller_start = script_text.index("# CONTROLLER (steps")
    agent_start = script_text.index("# AGENT (steps")

    def count_steps(region: str) -> int:
        return len(re.findall(r'^\s*step "', region, re.MULTILINE))

    backup_steps = count_steps(script_text[backup_start:restore_start])
    restore_steps = count_steps(script_text[restore_start:common_start])
    common_steps = count_steps(script_text[common_start:controller_start])
    controller_steps = count_steps(script_text[controller_start:agent_start])
    agent_steps = count_steps(script_text[agent_start:])

    assert common_steps == 9
    assert controller_steps == 13
    assert restore_steps == 7
    assert agent_steps == 9
    assert backup_steps == 4
    assert step_calls == common_steps + controller_steps + restore_steps + agent_steps + backup_steps

    assert "STEP_TOTAL=22" in script_text  # controller: common + controller-only
    assert "STEP_TOTAL=29" in script_text  # controller --restore: + restore steps
    assert "STEP_TOTAL=18" in script_text  # agent: common + agent-only
    assert "STEP_TOTAL=4" in script_text  # --backup


# --- Argument parsing / validation (runs before the root check) -------------


def test_no_args_prints_usage() -> None:
    result = _run([])
    assert result.returncode == 1
    assert "Usage:" in result.stdout or "Usage:" in result.stderr


def test_invalid_role_prints_usage() -> None:
    result = _run(["--role", "bogus"])
    assert result.returncode == 1
    assert "Usage:" in result.stdout or "Usage:" in result.stderr


def test_backup_rejected_for_agent_role() -> None:
    result = _run(["--role", "agent", "--backup"])
    assert result.returncode == 1
    assert "--backup is only supported with --role controller" in result.stderr


def test_restore_rejected_for_agent_role() -> None:
    result = _run(["--role", "agent", "--restore", "/tmp/x.tar.gz"])
    assert result.returncode == 1
    assert "--restore is only supported with --role controller" in result.stderr


def test_backup_and_restore_together_rejected() -> None:
    result = _run(["--role", "controller", "--backup", "--restore", "/tmp/x.tar.gz"])
    assert result.returncode == 1
    assert "cannot be used together" in result.stderr


def test_optional_backup_filename_does_not_swallow_next_flag() -> None:
    """--backup with no filename must not eat a following --flag as its value."""
    result = _run(
        [
            "--role",
            "agent",
            "--backup",
            "--controller-url",
            "http://10.0.0.2:8090",
            "--controller-api-key",
            "k",
        ]
    )
    assert result.returncode == 1
    assert "--backup is only supported with --role controller" in result.stderr


def test_restore_without_filename_prints_usage() -> None:
    result = _run(["--role", "controller", "--restore"])
    assert result.returncode == 1
    assert "Usage:" in result.stdout or "Usage:" in result.stderr


def test_valid_role_reaches_root_check_and_stops_without_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root - the script would proceed past the root check into apt-get")
    result = _run(["--role", "agent"])
    assert result.returncode != 0
    assert "must be run as root" in result.stderr
