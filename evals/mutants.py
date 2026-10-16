"""
evals/mutants.py — "break the check and watch it fail", applied to QAX itself
========================================================================================
The golden gate proves QAX finds the planted bugs. This asks the opposite question about
the GATE: if a probe silently stopped working, would anything notice?

For each probe function the scan can call, patch it to a no-op, re-run the quick scan on
the golden target, and compare with the unmodified baseline:
  DETECTS     — a CONFIRMED finding disappears → the gate would catch a broken detector;
  LEDGER-ONLY — only the not-tested ledger changes → the gate sees the probe *report*, but no
                bug is planted for it, so a broken *detection* would pass unseen;
  NOT COVERED — the scan is identical → nothing on the golden target exercises it.
That is a coverage map of the gate, not of the probes. Noise (anything that differs between
two unmodified baseline runs) is excluded so a flaky field can't fake a kill.

    ./.venv-exec/bin/python -m evals.mutants [--only name,name] [--json out.json]

Zero-LLM; ~5–10 min. Exit 0 always (it is a report); `--min-detects N` makes it a gate.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from contextlib import contextmanager


def discover() -> list[tuple[object, str]]:
    """(module, function name) for every public *_probe the scan can call."""
    import execution.probes as P
    import execution.typed_probes as T
    out = []
    for mod in (P, T):
        for name, fn in sorted(vars(mod).items()):
            if name.endswith("_probe") and not name.startswith("_") and inspect.isfunction(fn) \
                    and fn.__module__ == mod.__name__:
                out.append((mod, name))
    return out


def signature(result: dict) -> frozenset:
    """What a scan 'said': confirmed (probe, title) + ledger (probe, kind), as a set."""
    sig = {("confirmed", str(b.get("probe") or b.get("source")), str(b.get("title")))
           for b in result.get("confirmed", []) if isinstance(b, dict)}
    sig |= {("not-tested", str(n.get("probe")), str(n.get("kind")))
            for n in result.get("not_tested", []) if isinstance(n, dict)}
    return frozenset(sig)


@contextmanager
def dead(mod, name: str):
    """Patch `mod.name` to a no-op returning the right 'nothing' for its return type."""
    real = getattr(mod, name)
    ann = str(inspect.signature(real).return_annotation)
    empty = [] if "list" in ann.lower() else None

    def _noop(*a, **k):
        return list(empty) if isinstance(empty, list) else None
    _noop.__name__ = name
    setattr(mod, name, _noop)
    try:
        yield
    finally:
        setattr(mod, name, real)


def run(only: set[str] | None = None) -> dict:
    """Execute the mutation sweep and return the report dict."""
    import io
    from contextlib import redirect_stdout

    from evals import gate

    port = gate._free_port()
    proc = gate._boot(port, secure=False)
    base = f"http://127.0.0.1:{port}"

    def scan() -> frozenset:
        """All gate lanes: anonymous, two supplied identities (bola/access-revocation…), modelled source."""
        with redirect_stdout(io.StringIO()):
            r1, _ = gate._scan(base)
            r2, _ = gate._scan(base, auth=gate._two_user_auth(base))
        src = gate._source_lane(base)          # forge-jwt needs a modelled source (see gate._source_lane)
        return (signature(r1.to_dict()) | frozenset(("auth",) + x for x in signature(r2.to_dict()))
                | frozenset(("source",) + x for x in signature(src.to_dict())))

    try:
        a, b = scan(), scan()
        noise = a ^ b
        baseline = a - noise
        rows = []
        for mod, name in discover():
            if only and name not in only:
                continue
            with dead(mod, name):
                sig = scan() - noise
            lost, gained = sorted(baseline - sig), sorted(sig - baseline)
            detects = any("confirmed" in x[:2] for x in lost)
            rows.append({"probe": name, "killed": bool(lost or gained), "detects": detects,
                         "lost": [list(x) for x in lost][:5], "gained": [list(x) for x in gained][:5]})
        return {"baseline_items": len(baseline), "noise_items": len(noise), "mutants": rows,
                "killed": sum(r["killed"] for r in rows),
                "detects": sum(r["detects"] for r in rows), "total": len(rows)}
    finally:
        proc.terminate()


def render(rep: dict) -> str:
    """Human summary: killed vs not covered."""
    lines = [f"Gate mutation sweep — {rep['detects']}/{rep['total']} probes: the gate loses a CONFIRMED "
             f"finding if they die; {rep['killed'] - rep['detects']} more only change the not-tested ledger "
             f"(baseline {rep['baseline_items']} items, {rep['noise_items']} noisy ignored)", ""]
    for r in rep["mutants"]:
        tag = "DETECTS    " if r["detects"] else ("LEDGER-ONLY" if r["killed"] else "NOT COVERED")
        lines.append(f"  {tag}  {r['probe']}")
    weak = [r["probe"] for r in rep["mutants"] if not r["detects"]]
    if weak:
        lines += ["", f"{len(weak)} probe(s) have no planted bug on the golden target (LEDGER-ONLY = the gate "
                      "sees them report, NOT COVERED = it sees nothing). A silent regression of their "
                      "DETECTION would pass the gate. Plant a bug for each to close the gap."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry."""
    p = argparse.ArgumentParser(prog="evals.mutants")
    p.add_argument("--only", default="", help="comma-separated probe function names")
    p.add_argument("--json", default="", help="write the report here")
    p.add_argument("--min-detects", type=int, default=0, help="exit 1 if fewer probes are DETECTS")
    a = p.parse_args(argv)
    rep = run({s for s in a.only.split(",") if s} or None)
    print(render(rep))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=2)
    return 1 if rep["detects"] < a.min_detects else 0


if __name__ == "__main__":
    sys.exit(main())
