"""S1: DRY_RUN must be request-scoped and safe-by-default — never push live by accident."""
import core.xray_client as xc


def test_explicit_dry_run_flag_wins_over_env(monkeypatch):
    monkeypatch.setattr(xc, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DRY_RUN", "false")           # env says live…
    assert xc.XrayClient(dry_run=True).dry_run is True   # …explicit True still wins (no push)

    monkeypatch.setenv("DRY_RUN", "true")            # env says dry…
    for k in ("XRAY_CLIENT_ID", "XRAY_CLIENT_SECRET", "JIRA_CLOUD_ID"):
        monkeypatch.setenv(k, "x")                   # creds present so live path doesn't raise
    assert xc.XrayClient(dry_run=False).dry_run is False  # …explicit False wins


def test_defaults_to_safe_dry_run_when_unset(monkeypatch):
    monkeypatch.setattr(xc, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert xc.XrayClient().dry_run is True            # safe default: no live push without opt-in
