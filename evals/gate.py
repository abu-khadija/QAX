"""
evals/gate.py — the merge gate: measure every change before it lands.
=====================================================================
The discipline the roadmap locks in: a change doesn't merge unless it can be shown
to improve something without breaking anything. This runs that check end to end,
deterministically, in one command:

    python3 -m evals.gate          # quick-scan gate + A/B harness (zero-LLM path)

It boots the golden vulnerable target (evals/targets/vuln_jwt_app.py), runs a
deterministic QUICK scan against it, scores the confirmed findings against the
ground truth (evals/truth/golden-jwt.json), then runs the SECURE control (the same
app with the flaw fixed) to prove the probes go silent — no false positives — and
finally runs the orchestration A/B harness. It exits non-zero if any guarantee
regresses, so CI / a pre-merge hook can gate on it.

Needs the exec venv (Flask): run under ./.venv-exec/bin/python.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

APP = str(Path(__file__).parent / "targets" / "vuln_jwt_app.py")
TRUTH = str(Path(__file__).parent / "truth" / "golden-jwt.json")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _boot(port: int, *, secure: bool) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port))
    if secure:
        env["QAX_SECURE"] = "1"
    else:
        env.pop("QAX_SECURE", None)
    proc = subprocess.Popen([sys.executable, APP], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if urllib.request.urlopen(f"{base}/health", timeout=1).status == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("golden target did not come up")


def _scan(base_url: str):
    """Deterministic quick scan against the live target (empty repo → no source LLM pass)."""
    import json

    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService
    from services.scorecard import score_run

    truth = json.load(open(TRUTH))
    empty = tempfile.mkdtemp(prefix="qax_gate_")
    t0 = time.time()
    res = OrchestratorService(QAXContext.from_env()).run(
        empty, base_url, deep=False, use_cache=False)
    rec = score_run(res, repo="golden-jwt", url=base_url, mode="quick",
                    duration_sec=time.time() - t0, truth=truth)
    return res, rec


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    # 1) Vulnerable run — probes must catch the whole documented surface, 0 FP.
    proc = _boot(_free_port_val := _free_port(), secure=False)
    try:
        res, rec = _scan(f"http://127.0.0.1:{_free_port_val}")
    finally:
        proc.terminate()
    acc = rec.get("accuracy", {})
    recall = acc.get("recall_pct")
    fps = acc.get("false_positives")
    probes = {b.get("probe") for b in res.confirmed}
    checks.append(("quick-scan recall == 100%", recall == 100.0,
                   f"recall={recall}% caught {acc.get('caught')}/{acc.get('documented')} "
                   f"missed={acc.get('missed')}"))
    checks.append(("quick-scan false positives == 0", fps == 0, f"false_positives={fps}"))
    checks.append(("forged-token-replay fired (item 6 regression)",
                   "forged-token-replay" in probes, f"probes={sorted(p for p in probes if p)}"))

    # 2) Secure control — the SAME app with the flaw fixed must go silent (no FP).
    proc = _boot(sec_port := _free_port(), secure=True)
    try:
        sres, _ = _scan(f"http://127.0.0.1:{sec_port}")
    finally:
        proc.terminate()
    auth_bugs = [b for b in sres.confirmed
                 if b.get("probe") in ("jwt-flaws", "forged-token-replay", "forge-jwt")]
    checks.append(("secure control: auth probes silent (0)", len(auth_bugs) == 0,
                   f"auth findings on fixed app={len(auth_bugs)}"))

    # 3) Orchestration A/B — recall up, cost down, FP flat.
    from evals.orchestrator_ab import measure
    m = measure()
    r, c, fp = (m["recall_classes_expanded"], m["round2_suspects_reattacked"],
                m["unproven_findings_added"])
    checks.append(("A/B recall up", r["after"] > r["before"], f"{r['before']}->{r['after']}"))
    checks.append(("A/B cost down", c["after"] < c["before"], f"{c['before']}->{c['after']}"))
    checks.append(("A/B false positives flat", fp["after"] == fp["before"] == 0,
                   f"{fp['before']}->{fp['after']}"))

    # ── verdict ──
    print("\nQAX merge gate\n" + "=" * 60)
    ok = True
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:48} {detail}")
        ok = ok and passed
    print("=" * 60)
    print("GATE: PASS ✅  — safe to merge\n" if ok else "GATE: FAIL ❌  — do not merge\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
