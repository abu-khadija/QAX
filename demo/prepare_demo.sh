#!/usr/bin/env bash
# QAX demo — start everything the demo needs (local only, nothing leaves this machine). ~3 minutes the first time.
#   bash demo/prepare_demo.sh        then open http://127.0.0.1:8900/app
# Needs: the corpus in ~/Downloads/qax-corpus (see docs/HANDOVER.md), Java 17+, the QAX venv.
set -uo pipefail
QAX="$(cd "$(dirname "$0")/.." && pwd)"
CORPUS="${QAX_CORPUS:-$HOME/Downloads/qax-corpus}"
# Java 17+: QAX_JAVA, else Homebrew's OpenJDK, else JAVA_HOME, else the java on PATH (macOS's /usr/bin/java may be 8)
JAVA="${QAX_JAVA:-}"
for c in /opt/homebrew/opt/openjdk/bin/java /usr/local/opt/openjdk/bin/java "${JAVA_HOME:-/nonexistent}/bin/java" "$(command -v java)"; do
  [ -n "$JAVA" ] && break; [ -x "$c" ] && JAVA="$c"
done
JV=$("$JAVA" -version 2>&1 | head -1 | sed -E 's/.*version "([0-9]+).*/\1/')
if [ "${JV:-0}" -lt 17 ] 2>/dev/null; then echo "Java 17+ needed (found $JAVA, version ${JV:-?}) — set QAX_JAVA=/path/to/java"; exit 1; fi
PY="$QAX/.venv-exec/bin/python"; [ -x "$PY" ] || PY="$QAX/.venv/bin/python"
PIDS="$CORPUS/.bench.pids"
up() { curl -s -o /dev/null --max-time 3 "$1"; }

echo "· corpus apps (PetClinic :8945, linkding :9090, …)"
up http://127.0.0.1:8945/ || bash "$CORPUS/start-all.sh"

echo "· linkding BEFORE its upstream fixes on :9190 (two real bugs)"
if ! up http://127.0.0.1:9190/login/; then
  (cd "$CORPUS/linkding-old" && nohup .venv/bin/python manage.py runserver 127.0.0.1:9190 --noreload > bench.log 2>&1 < /dev/null & echo $! >> "$PIDS")
fi

echo "· PetClinic with a seeded regression on :8946 (phone validation dropped)"
WT="$CORPUS/.demo-petclinic-seeded"
if ! ls "$WT"/target/spring-petclinic-*.jar >/dev/null 2>&1; then
  git -C "$CORPUS/spring-petclinic" worktree remove --force "$WT" >/dev/null 2>&1
  git -C "$CORPUS/spring-petclinic" worktree add -q -f -B qax-demo-seed "$WT" HEAD
  "$PY" - "$WT" <<'PYEOF'
import sys, pathlib
f = pathlib.Path(sys.argv[1]) / "src/main/java/org/springframework/samples/petclinic/owner/Owner.java"
s = f.read_text(); old = r'@Pattern(regexp = "\\d{10}"'
assert old in s, "seed anchor not found"
f.write_text(s.replace(old, '@Pattern(regexp = ".*"', 1))
PYEOF
  git -C "$WT" commit -qam "demo seed: owner phone validation dropped"
  (cd "$WT" && JAVA_HOME="$(dirname "$(dirname "$JAVA")")" ./mvnw -q -DskipTests -Dcheckstyle.skip -Dspring-javaformat.skip=true package) || { echo "  build failed"; exit 1; }
fi
if ! up http://127.0.0.1:8946/; then
  (cd "$WT" && nohup "$JAVA" -jar target/spring-petclinic-*.jar --server.port=8946 > bench.log 2>&1 < /dev/null & echo $! >> "$PIDS")
fi

echo "· the bundled vulnerable API (for the security scan) on :8930"
up http://127.0.0.1:8930/ || (cd "$QAX" && PORT=8930 nohup "$PY" evals/targets/vuln_jwt_app.py > "$CORPUS/vuln-demo.log" 2>&1 < /dev/null & echo $! >> "$PIDS")

echo "· QAX web on :8900"
up http://127.0.0.1:8900/ || (cd "$QAX" && nohup "$PY" -m web.server > "$CORPUS/qax-web.log" 2>&1 < /dev/null & echo $! >> "$PIDS")

echo -n "· waiting for the apps"
for i in $(seq 1 60); do up http://127.0.0.1:8946/ && up http://127.0.0.1:9190/login/ && up http://127.0.0.1:8900/ && break; echo -n .; sleep 3; done; echo
for u in 8900/ 8930/ 8945/ 8946/ 9090/login/ 9190/login/; do printf "  %-22s %s\n" "127.0.0.1:$u" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$u)"; done
echo "Ready: open http://127.0.0.1:8900/app  ·  script: demo/DEMO_SCRIPT.md  ·  stop: bash demo/stop_demo.sh"
