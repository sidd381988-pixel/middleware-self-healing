# Middleware Self-Healing Agent

An AI-powered autonomous agent that monitors Apache Tomcat logs on RHEL and automatically remediates common production incidents — no human intervention required.

Powered by **Ollama** running a local LLM — no external API keys, no internet dependency, no inference costs.

---

## How It Works

```
catalina.out / access log
        │
        ▼
  ┌─────────────┐     regex pre-filter      ┌───────────────────┐
  │  Log Tailer │ ─── (only signal lines) ──▶│    AI Engine      │
  └─────────────┘                            │  (Ollama / local) │
                                             └────────┬──────────┘
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
3. Sends filtered lines + current incident state to Ollama for a structured JSON decision
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
│   ├── ai_engine.py         # Ollama client — local LLM inference
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

| Requirement | Notes |
|-------------|-------|
| OS | RHEL 8 or 9 |
| Python | 3.11+ |
| Java | OpenJDK 11+ |
| Apache Tomcat | 10.x — installed by `install_tomcat.sh` |
| Ollama | Installed on the same server (see below) |
| SMTP relay | TLS-capable (e.g. SendGrid, company relay) |

> **No API key required.** Everything runs locally on your server.

---

## Choosing a Model

Pick based on your available RAM. The model name goes in `config/settings.yaml → ollama.model`.

| Model | RAM usage | Speed (CPU) | Quality | Recommended for |
|-------|-----------|-------------|---------|-----------------|
| `gemma2:2b` | ~1.6 GB | Fastest | Good | Very tight RAM (<4 GB free) |
| `phi3:mini` | ~2.3 GB | Fast | Very good | ✅ **Default — best balance** |
| `llama3.2:3b` | ~2.0 GB | Fast | Very good | Alternative to phi3:mini |
| `mistral:7b` | ~4.1 GB | Slow on CPU | Excellent | If you have 6+ GB free RAM |

For a **2 vCPU / 7.4 GB** server running Tomcat, `phi3:mini` leaves enough RAM for Tomcat (512 MB heap) and the OS.

---

## Installation

### 1 — Install Tomcat on RHEL (skip if already installed)

```bash
sudo bash scripts/install_tomcat.sh
```

### 2 — Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Start the Ollama service
sudo systemctl enable --now ollama
```

### 3 — Pull the LLM model

```bash
ollama pull phi3:mini
# Verify it works
ollama run phi3:mini "Say hello"
```

### 4 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 5 — Configure secrets

```bash
cp .env.example .env
```

Edit `.env`:

```env
SMTP_PASSWORD=your_smtp_password
```

### 6 — Configure the agent

Edit `config/settings.yaml` — minimum required changes:

```yaml
ollama:
  model: phi3:mini          # ← match the model you pulled

database:
  host: your-db-host.internal   # ← real DB host for connectivity checks
  port: 5432

email:
  smtp_host: smtp.yourcompany.com
  smtp_user: middleware-agent@yourcompany.com
  admin_addrs:
    - admin@yourcompany.com
```

---

## Running the Agent

```bash
# Foreground (recommended for initial testing)
python agent/main.py

# Background via nohup
nohup python agent/main.py > /var/log/middleware-agent.log 2>&1 &
```

Sample output:

```
2024-01-15 10:32:01 INFO     main — Middleware self-healing agent started (poll=30s)
2024-01-15 10:32:01 INFO     ai_engine — AIEngine ready — model=phi3:mini host=http://localhost:11434
2024-01-15 10:32:31 INFO     main — Collected 3 signal lines — consulting Ollama
2024-01-15 10:32:34 INFO     ai_engine — AI decision: oom / thread_dump (urgency=critical)
2024-01-15 10:32:34 INFO     main — Executing action: thread_dump (incident=oom)
2024-01-15 10:32:35 INFO     main —   ✓ thread_dump succeeded: Saved to /var/lib/middleware-agent/dumps/thread_dump_20240115_103235.txt
2024-01-15 10:32:35 INFO     main — Executing action: increase_heap (incident=oom)
2024-01-15 10:32:35 INFO     main —   ✓ increase_heap succeeded: Heap increased by 500MB → Xmx=1012m (restart required)
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
# Agent logs (terminal 1)
python agent/main.py

# Inject error (terminal 2)
sudo bash scripts/simulate_errors.sh oom
```

---

## Configuration Reference

```yaml
# config/settings.yaml

ollama:
  host: http://localhost:11434  # Ollama API address
  model: phi3:mini              # Model to use for inference
  timeout: 120                  # Seconds to wait for a response

tomcat:
  catalina_out: /opt/tomcat/logs/catalina.out
  access_log: /opt/tomcat/logs/localhost_access_log.*.txt
  service_name: tomcat          # systemd service name
  setenv_sh: /opt/tomcat/bin/setenv.sh
  webapps_url: http://localhost:8080
  health_check_path: /          # path for HTTP 200 check

java:
  heap_increment_mb: 500        # MB added per OOM event

database:
  host: db.internal             # host for socket connectivity check
  port: 5432

agent:
  poll_interval_seconds: 30     # how often to check logs
  state_db: /var/lib/middleware-agent/state.db
  dumps_dir: /var/lib/middleware-agent/dumps

thresholds:
  http500_window_seconds: 120   # sliding window for 500-count
  http500_min_count: 5
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

### Ollama integration
- **`format="json"`** — forces the model to emit valid JSON; no markdown or prose
- **`temperature: 0`** — deterministic decisions; same log lines always produce the same action
- **`num_predict: 512`** — caps output tokens since the decision payload is small (~100 tokens)
- **Robust JSON parsing** — strips accidental code fences, extracts first `{...}` block, fills missing keys with safe defaults
- **Fallback** — if Ollama is unreachable or returns unparseable output, the agent defaults to `action: "watch"` and logs the error

### State management
Incident counts survive agent restarts via SQLite:
- "OOM has occurred 3 times total → notify admin"
- Restart cooldown: no more than one restart every 5 minutes
- Heap increase cooldown: no more than one increase every 10 minutes

### Log tailing
Tracks seek position per file. Detects log rotation via inode change. Only forwards lines matching the signal regex patterns — everything else is discarded before touching the LLM, keeping inference fast.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `Connection refused` on Ollama | `systemctl status ollama` — make sure service is running |
| `model not found` error | `ollama pull phi3:mini` — model must be pulled before use |
| Slow inference (>30s per cycle) | Switch to a smaller model (`gemma2:2b`) or reduce `poll_interval_seconds` |
| Agent sees no log lines | Verify `catalina_out` path in `settings.yaml`; check file permissions |
| `thread_dump failed: Tomcat PID not found` | Tomcat must be running: `systemctl status tomcat` |
| `heap_dump failed` | `jmap` requires running as the same user as Tomcat (`tomcat`) or root |
| Email not sent | Check SMTP credentials and TLS setting |
| `Cannot reach db:5432` | Expected if DB is down — agent will notify admins |
