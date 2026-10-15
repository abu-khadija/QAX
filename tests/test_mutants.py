"""Unit tests for the helpers of evals/mutants.py (the sweep itself is run via `make eval-mutants`)."""
import execution.probes as P
from evals import mutants


def test_discover_finds_every_public_probe_incl_typed():
    names = {n for _, n in mutants.discover()}
    assert {"bola_probe", "jwt_flaws_probe", "boolean_false_probe", "access_revocation_probe"} <= names
    assert len(names) >= 38 and not any(n.startswith("_") for n in names)


def test_dead_patches_and_restores_with_right_empty_type():
    real = P.cors_probe
    with mutants.dead(P, "cors_probe"):
        assert P.cors_probe(None, "http://x") == []          # list-returning probe
    with mutants.dead(P, "forge_jwt_probe"):
        assert P.forge_jwt_probe(None, "http://x") is None   # single-finding probe
    assert P.cors_probe is real


def test_signature_captures_findings_and_ledger():
    sig = mutants.signature({"confirmed": [{"probe": "cors", "title": "t"}],
                             "not_tested": [{"probe": "ssrf", "kind": "not-applicable"}]})
    assert ("confirmed", "cors", "t") in sig and ("not-tested", "ssrf", "not-applicable") in sig
    assert mutants.signature({}) == frozenset()
