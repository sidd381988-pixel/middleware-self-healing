# Middleware Self-Healing Agent

An AI-powered autonomous agent that monitors Apache Tomcat logs on RHEL and automatically remediates common production incidents — no human intervention required.

Built with **Claude claude-opus-4-7** (Anthropic) as the decision engine.

---

## How It Works

```
catalina.out / access log
        │
        ▼
  ┌─────────────┐     regex pre-filter      ┌───────────────┐
  │  Log Tailer │ ─── (only signal lines) ──▶│   AI Engine   │
  └─────────────┘                            │ (Claude API)  │
                                             └──────┬────────┘
                                                    │ JSON action decision
                                                    ▼
                                         ┌──────────────────────┐
                                         │   Action Executor    │
                                         │  jstack / jmap /     │
                                         │  systemctl restart / │
                                         │  heap bump / telnet  │
                                         └──────────┬───────────┘
                                                    │
                                         ┌──────────▼───────────┐
                                         │   State Manager      │
                                         │   (SQLite)           │
                                         └──────────┬───────────┘
                                                    │  notify_admin=true
                                                    ▼
                                         ┌──────────────────────┐
                                         │   Email Notifier     │
                                         │   (smtplib / TLS)    │
                                         └──────────────────────┘
```

Every **30 seconds** the agent:
1. Reads only newly-written lines from `catalina.out` and the Tomcat access log
2. Pre-filters with regex to keep only actionable signal lines (errors, exceptions, 5xx, GC, DB)
3. Sends filtered lines + current incident state to Claude for a structured JSON decision
4. Executes the decided actions
5. Records the incident and action in SQLite
6. Emails admins when required

---

## Self-Healing Scenarios

| # | Trigger | Automatic Response |
|---|---------|-------------------|
| 1 | **≥5 HTTP 500s in 2 min** | Health-check app → if still 500: thread dump + heap dump + restart |
| 2 | **OutOfMemoryError** | Thread dump → increase heap +500 MB; if OOM > 2× → email admins |
| 3 | **DB connectivity failure** | Socket check (telnet) to DB → email admins |
| 4 | **NullPointerException** | Restart Tomcat; if NPE recurs after restart → email admins |
| 5 | **GC overhead / FullGC** | Analyze → email admins with tuning recommendations |

---

## Project Structure

```
middleware-self-healing/
├── agent/
│   ├── main.py              # Orchestrator — 30-second polling loop
│   ├── config_loader.py     # Loads settings.yaml + .env secrets
│   ├── log_tailer.py        # Tails catalina.out + access log (seek-based)
│   ├── ai_engine.py         # Claude API — adaptive thinking + prompt caching
│   ├── action_executor.py   # jstack, jmap, systemctl, heap bump, socket check
│   ├── state_manager.py     # SQLite incident history & action tracking
│   └── notifier.py          # Email via smtplib (TLS)
├── config/
│   └── settings.yaml        # All configuration
├── scripts/
│   ├── install_tomcat.sh    # One-shot RHEL Tomcat install (Java, systemd, firewall, SELinux)
│   └── simulate_errors.sh   # Inject test log lines for each scenario
├── requirements.txt
└── .env.example
```

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| OS | RHEL 8 or 9 |
| Python | 3.11+ |
| Java | OpenJDK 11+ |
| Apache Tomcat | 10.x (installed by `install_tomcat.sh`) |
| Anthropic API key | claude-opus-4-7 access |
| SMTP relay | TLS-capable (e.g. SendGrid, company relay) |

---

## Installation

### 1 — Install Tomcat on RHEL (skip if already installed)

```bash
sudo bash scripts/install_tomcat.sh
```

This script:
- Installs `java-11-openjdk-devel` via `dnf`
- Downloads and extracts Tomcat 10.1 to `/opt/tomcat`
- Creates a `tomcat` system user
- Registers a `systemd` service (`systemctl start/stop/restart tomcat`)
- Opens port `8080` via `firewall-cmd`
- Applies SELinux context to the port
- Creates `/var/lib/middleware-agent/dumps/` for thread/heap dumps

### 2 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3 — Configure secrets

```bash
cp .env.example .env
```

Edit `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
SMTP_PASSWORD=your_smtp_password
```

### 4 — Configure the agent

Edit `config/settings.yaml` — minimum required changes:

```yaml
database:
  host: your-db-host.internal   # ← real DB host for connectivity checks
  port: 5432

email:
  smtp_host: smtp.yourcompany.com
  smtp_user: middleware-agent@yourcompany.com
  admin_addrs:
    - admin@yourcompany.com
```

All other defaults work out of the box for a standard Tomcat install.

---

## Running the Agent

```bash
# Foreground (recommended for initial testing)
python agent/main.py

# Background via systemd (production)
sudo cp scripts/middleware-agent.service /etc/systemd/system/
sudo systemctl enable --now middleware-agent
```

Sample output:

```
2024-01-15 10:32:01 INFO     main — Middleware self-healing agent started (poll=30s)
2024-01-15 10:32:31 INFO     main — Collected 3 signal lines — consulting Claude
2024-01-15 10:32:33 INFO     ai_engine — AI decision: oom / thread_dump (urgency=high)
2024-01-15 10:32:33 INFO     main — Executing action: thread_dump (incident=oom)
2024-01-15 10:32:34 INFO     main —   ✓ thread_dump succeeded: Saved to /var/lib/middleware-agent/dumps/thread_dump_20240115_103234.txt
2024-01-15 10:32:34 INFO     main — Executing action: increase_heap (incident=oom)
2024-01-15 10:32:34 INFO     main —   ✓ increase_heap succeeded: Heap increased by 500MB → Xmx=1012m (restart required)
```

---

## Testing Scenarios

Use the simulator to inject log entries without needing a real error:

```bash
# Inject OutOfMemoryError
sudo bash scripts/simulate_errors.sh oom

# Inject 6 HTTP 500 responses
sudo bash scripts/simulate_errors.sh http500

# Inject DB connectivity failure
sudo bash scripts/simulate_errors.sh db

# Inject NullPointerException
sudo bash scripts/simulate_errors.sh npe

# Inject GC overhead limit exceeded
sudo bash scripts/simulate_errors.sh gc
```

Watch the agent respond in real time:

```bash
# Agent logs
python agent/main.py

# In a second terminal
tail -f /opt/tomcat/logs/catalina.out
```

---

## Configuration Reference

```yaml
# config/settings.yaml

tomcat:
  catalina_out: /opt/tomcat/logs/catalina.out
  access_log: /opt/tomcat/logs/localhost_access_log.*.txt
  service_name: tomcat            # systemd service name
  setenv_sh: /opt/tomcat/bin/setenv.sh
  webapps_url: http://localhost:8080
  health_check_path: /            # path for HTTP 200 check

java:
  heap_increment_mb: 500          # bytes added per OOM event

database:
  host: db.internal               # host for socket connectivity check
  port: 5432

agent:
  poll_interval_seconds: 30       # how often to check logs
  state_db: /var/lib/middleware-agent/state.db
  dumps_dir: /var/lib/middleware-agent/dumps

thresholds:
  http500_window_seconds: 120     # sliding window for 500-count
  http500_min_count: 5            # 500s in window before acting
  oom_max_occurrences_before_notify: 2
  npe_max_restarts_before_notify: 2

email:
  smtp_host: smtp.company.com
  smtp_port: 587
  smtp_user: middleware-agent@company.com
  from_addr: middleware-agent@company.com
  admin_addrs:
    - admin@company.com
  use_tls: true
```

---

## Architecture Notes

### Claude API usage
- **Model**: `claude-opus-4-7` with `thinking: {type: "adaptive"}`
- **Prompt caching**: The static runbook system prompt is cached via `cache_control: {"type": "ephemeral"}`, saving ~90% on repeated input token costs each poll cycle
- **Structured output**: Claude returns a strict JSON schema (`output_config.format`) — no string parsing needed
- **Fallback**: If the API is unreachable, the agent defaults to `action: "watch"` and logs the error

### State management
Incident counts survive agent restarts via SQLite. This enables logic like:
- "OOM has occurred 3 times total → notify admin" (even across restarts)
- "Restart cooldown: don't restart more than once every 5 minutes"

### Log tailing
Tracks seek position per file. Detects log rotation via inode change. Only forwards lines matching the signal regex patterns — everything else is discarded before touching the LLM.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Agent sees no lines | Verify `catalina_out` path in `settings.yaml`; check file permissions |
| `ANTHROPIC_API_KEY` error | Ensure `.env` is in project root and key is valid |
| `thread_dump failed: Tomcat PID not found` | Tomcat must be running: `systemctl status tomcat` |
| `heap_dump failed` | `jmap` requires the agent to run as the same user as Tomcat (`tomcat`) or root |
| Email not sent | Check SMTP credentials, port, TLS setting; test with `python -c "import smtplib; ..."` |
| `Cannot reach db:5432` | Expected if DB is down — agent will notify admins |
