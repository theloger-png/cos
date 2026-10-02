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
REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"


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


def test_requirements_installed_before_alembic_and_without_no_deps(script_text: str) -> None:
    """Regression guard: requirements.txt (which carries greenlet via the
    sqlalchemy[asyncio] extra) must be installed, without --no-deps, before
    any "alembic upgrade head" call - otherwise alembic fails on a clean venv.

    do_backup()/do_restore() are *defined* early in the file (their bodies
    contain "alembic upgrade head" for the post-restore migration) but are
    only *called* later, after the dependency install runs - so this checks
    call/execution sites, not raw text position of the function bodies.
    """
    deps_idx = script_text.index("pip install -r")
    deps_line = script_text.splitlines()[script_text.count("\n", 0, deps_idx)]
    assert "--no-deps" not in deps_line

    # Controller install path: runs directly after COMMON, i.e. after deps_idx.
    controller_alembic_idx = script_text.index(
        "alembic", script_text.index('step "Running database migrations"')
    )
    assert deps_idx < controller_alembic_idx

    # do_restore() is only invoked (both for --restore and post-install) from
    # call sites below COMMON; its definition (with its own alembic call)
    # sits above COMMON but never executes until called.
    restore_call_sites = [m.start() for m in re.finditer(r"^\s*do_restore$", script_text, re.MULTILINE)]
    assert restore_call_sites, "do_restore() must actually be called somewhere"
    for call_idx in restore_call_sites:
        assert deps_idx < call_idx


def test_requirements_pins_sqlalchemy_asyncio_extra() -> None:
    """Regression guard: plain sqlalchemy>=2.0 does not pull in greenlet, so
    "alembic upgrade head" fails on a clean venv with "SQLAlchemy asyncio
    module requires greenlet". The [asyncio] extra must be kept.
    """
    text = REQUIREMENTS.read_text()
    assert "sqlalchemy[asyncio]" in text
    assert re.search(r"^sqlalchemy>=", text, re.MULTILINE) is None


def test_requirements_has_no_test_only_packages() -> None:
    """pytest/pytest-asyncio belong in requirements-dev.txt, not in the
    production requirements installed on every controller/agent machine.
    """
    text = REQUIREMENTS.read_text()
    assert "pytest" not in text


def test_requirements_dev_extends_requirements() -> None:
    dev_requirements = REQUIREMENTS.with_name("requirements-dev.txt")
    text = dev_requirements.read_text()
    assert "-r requirements.txt" in text
    assert "pytest" in text
    assert "pytest-asyncio" in text


def test_portal_uses_safe_atomic_swap(script_text: str) -> None:
    """Regression guard: rename() cannot replace a non-empty directory, so
    "mv -T new portal" fails on a re-run once portal/ already exists. The
    swap must go through a .old side-step (with rollback) instead.
    """
    assert "/opt/cos/portal.new" in script_text
    assert "mv -T" not in script_text
    assert "/opt/cos/portal.old" in script_text
    assert "mv /opt/cos/portal /opt/cos/portal.old" in script_text
    assert 'mv /opt/cos/portal.new /opt/cos/portal' in script_text


def test_portal_swap_rolls_back_on_failed_final_move(script_text: str) -> None:
    swap_start = script_text.index('step "Deploying the portal (safe swap)"')
    swap_end = script_text.index('step "Installing nginx configuration"')
    swap_region = script_text[swap_start:swap_end]
    assert "if mv /opt/cos/portal.new /opt/cos/portal; then" in swap_region
    assert 'mv /opt/cos/portal.old /opt/cos/portal' in swap_region
    assert "die " in swap_region


def test_reexec_guard_present(script_text: str) -> None:
    """Regression guard: the script re-execs itself once after an in-place
    git update (since bash reads the running script incrementally), guarded
    by an env var so it cannot loop.
    """
    assert "COS_INSTALL_REEXEC" in script_text
    # The parse loop consumes "$@" with shift, so the original arguments must
    # be saved before it and replayed on re-exec (a bare "$@" would be empty
    # and the re-executed script would just print usage).
    assert 'ORIG_ARGS=("$@")' in script_text
    assert script_text.index('ORIG_ARGS=("$@")') < script_text.index("while [[ $# -gt 0 ]]")
    assert 'exec "$0" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}' in script_text
    assert 'exec "$0" "$@"' not in script_text


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
    assert agent_steps == 13
    assert backup_steps == 4
    assert step_calls == common_steps + controller_steps + restore_steps + agent_steps + backup_steps

    assert "STEP_TOTAL=22" in script_text  # controller: common + controller-only
    assert "STEP_TOTAL=29" in script_text  # controller --restore: + restore steps
    assert "STEP_TOTAL=22" in script_text  # agent: common + agent-only (coincidentally same total as controller)
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
