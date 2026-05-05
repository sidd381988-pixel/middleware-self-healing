#!/usr/bin/env bash
# Simulate Tomcat error scenarios by appending realistic log lines to catalina.out.
# Usage: bash scripts/simulate_errors.sh <scenario>
#
# Scenarios:
#   http500      — inject 6 HTTP 500 access log entries
#   oom          — inject OutOfMemoryError stack trace
#   db           — inject DB connection failure
#   npe          — inject NullPointerException stack trace
#   gc           — inject GC overhead limit exceeded

set -euo pipefail

CATALINA_OUT="${CATALINA_OUT:-/opt/tomcat/logs/catalina.out}"
ACCESS_LOG_DIR="${ACCESS_LOG_DIR:-/opt/tomcat/logs}"
ACCESS_LOG="$ACCESS_LOG_DIR/localhost_access_log.$(date +%Y-%m-%d).txt"
SCENARIO="${1:-}"

# NOTE: call as $(ts) not $(ts()) — ts is a function, not a command with args
ts()     { date '+%d-%b-%Y %H:%M:%S.000'; }
log_ts() { date '+%Y-%m-%d %H:%M:%S'; }

inject_catalina() { echo "$*" >> "$CATALINA_OUT"; }
inject_access() {
    echo "127.0.0.1 - - [$(date '+%d/%b/%Y:%H:%M:%S %z')] \"$1\" $2 -" >> "$ACCESS_LOG"
}

case "$SCENARIO" in
# ── HTTP 500 ───────────────────────────────────────────────────────────────
http500)
    echo "[$(log_ts)] Injecting 6 HTTP 500 responses into access log ..."
    for i in $(seq 1 6); do
        inject_access "GET /app/api/users HTTP/1.1" 500
        sleep 0.2
    done
    inject_catalina "$(ts) SEVERE [http-nio-8080-exec-1] org.apache.catalina.core.StandardWrapperValve.invoke Servlet.service() threw exception"
    inject_catalina "  java.lang.RuntimeException: Unhandled application error"
    inject_catalina "    at com.example.AppServlet.doGet(AppServlet.java:42)"
    echo "Done. Injected HTTP 500 errors."
    ;;

# ── OutOfMemoryError ───────────────────────────────────────────────────────
oom)
    echo "[$(log_ts)] Injecting OutOfMemoryError ..."
    inject_catalina "$(ts) SEVERE [http-nio-8080-exec-3] org.apache.catalina.core.ContainerBase\$ContainerBackgroundProcessor.run"
    inject_catalina "Exception in thread \"http-nio-8080-exec-3\" java.lang.OutOfMemoryError: Java heap space"
    inject_catalina "  at java.util.Arrays.copyOf(Arrays.java:3210)"
    inject_catalina "  at java.util.ArrayList.grow(ArrayList.java:265)"
    inject_catalina "  at com.example.DataProcessor.loadAll(DataProcessor.java:88)"
    inject_catalina "  at com.example.AppServlet.doPost(AppServlet.java:101)"
    echo "Done. Injected OutOfMemoryError."
    ;;

# ── DB connectivity ────────────────────────────────────────────────────────
db)
    echo "[$(log_ts)] Injecting DB connectivity error ..."
    inject_catalina "$(ts) SEVERE [http-nio-8080-exec-5] com.example.db.ConnectionPool.getConnection"
    inject_catalina "Cannot get a connection, pool error Timeout waiting for idle object"
    inject_catalina "  Caused by: com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: Communications link failure"
    inject_catalina "  The last packet sent successfully to the server was 0 milliseconds ago."
    inject_catalina "    at sun.reflect.NativeConstructorAccessorImpl.newInstance0(Native Method)"
    inject_catalina "    at com.example.db.ConnectionPool.borrow(ConnectionPool.java:213)"
    echo "Done. Injected DB connectivity error."
    ;;

# ── NullPointerException ───────────────────────────────────────────────────
npe)
    echo "[$(log_ts)] Injecting NullPointerException ..."
    inject_catalina "$(ts) SEVERE [http-nio-8080-exec-2] org.apache.catalina.core.StandardWrapperValve.invoke"
    inject_catalina "  java.lang.NullPointerException"
    inject_catalina "    at com.example.service.UserService.getUser(UserService.java:57)"
    inject_catalina "    at com.example.controller.UserController.handleRequest(UserController.java:34)"
    inject_catalina "    at org.apache.catalina.core.ApplicationFilterChain.internalDoFilter(ApplicationFilterChain.java:231)"
    echo "Done. Injected NullPointerException."
    ;;

# ── GC issues ─────────────────────────────────────────────────────────────
gc)
    echo "[$(log_ts)] Injecting GC overhead limit exceeded ..."
    inject_catalina "$(ts) ERROR [http-nio-8080-exec-4] org.apache.catalina.core.ContainerBase"
    inject_catalina "Exception in thread \"http-nio-8080-exec-4\" java.lang.OutOfMemoryError: GC overhead limit exceeded"
    inject_catalina "  at java.util.HashMap.resize(HashMap.java:703)"
    inject_catalina "  at java.util.HashMap.putVal(HashMap.java:663)"
    inject_catalina "[Full GC (Ergonomics) [PSYoungGen: 512K->0K(512K)] [ParOldGen: 87040K->87040K(87040K)] 87552K->87040K(87552K)"
    inject_catalina "GCLocker: Trying to start a GC during full GC"
    echo "Done. Injected GC overhead error."
    ;;

*)
    echo "Usage: $0 <http500|oom|db|npe|gc>"
    echo ""
    echo "Scenarios:"
    echo "  http500  — 6 x HTTP 500 access log entries + SEVERE in catalina.out"
    echo "  oom      — OutOfMemoryError: Java heap space"
    echo "  db       — DB connection pool / Communications link failure"
    echo "  npe      — NullPointerException"
    echo "  gc       — GC overhead limit exceeded + FullGC + GCLocker"
    exit 1
    ;;
esac

echo ""
echo "Tail catalina.out:  tail -f $CATALINA_OUT"
echo "Tail access log:    tail -f $ACCESS_LOG"
