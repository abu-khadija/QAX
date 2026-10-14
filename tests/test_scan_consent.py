"""S4: active probing mutates the target — mutating probes are labelled, and the CLI
scan refuses without an explicit --authorized confirmation."""
from execution.probes import MUTATING_PROBES


def test_mutating_probes_label_covers_writers_not_readers():
    assert "bola" in MUTATING_PROBES and "stored-xss" in MUTATING_PROBES
    assert "mass-assignment" in MUTATING_PROBES and "persistence" in MUTATING_PROBES
    # read-only probes must NOT be labelled as mutating
    for ro in ("bfla", "forged-token-replay", "jwt-flaws", "security-headers", "cors"):
        assert ro not in MUTATING_PROBES


def test_scan_command_gates_on_authorized_flag():
    import inspect
    import main
    src = inspect.getsource(main.run_scan_command)
    assert "--authorized" in src and "Refusing to scan" in src   # consent gate present
