# Middleware Self-Healing Agent

An AI-powered autonomous agent that monitors Apache Tomcat logs on RHEL and automatically remediates common production incidents — no human intervention required.

Powered by **Claude Sonnet on Amazon Bedrock** — serverless LLM inference, no GPU required on your server.

---

## How It Works

```
catalina.out / access log
        │
        ▼
  ┌─────────────┐     regex pre-filter      ┌────────────────────────┐
  │  Log Tailer │ ─── (only signal lines) ──▶│       AI Engine        │
  └─────────────┘                            │  Claude Sonnet via     │
                                             │  Amazon Bedrock        │
                                             │  (boto3 Converse API)  │
                                             └──────────┬─────────────┘
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
3. Sends filtered lines + current incident state to Claude Sonnet via Bedrock for a JSON decision
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
│   ├── ai_engine.py         # boto3 Bedrock Converse API — Claude Sonnet
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
| AWS account | Bedrock enabled, Claude Sonnet model access requested |
| SMTP relay | TLS-capable (e.g. SendGrid, company relay) |

> **No GPU or local model needed.** Inference runs entirely on AWS Bedrock.

---

## AWS Setup

### 1 — Enable Bedrock model access

1. Open the [AWS Console → Amazon Bedrock → Model access](https://console.aws.amazon.com/bedrock/home#/modelaccess)
2. Click **Manage model access**
3. Enable **Claude Sonnet** (Anthropic)
4. Wait for status to show **Access granted**

### 2 — Create an IAM user or role

The agent only needs one permission:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"
    }
  ]
}
```

- **EC2 / on-prem with instance role**: attach the policy to the role — no keys needed in `.env`
- **Local / CI**: create an IAM user, generate access keys, put them in `.env`

---

## Installation

### 1 — Install Tomcat on RHEL (skip if already installed)

```bash
sudo bash scripts/install_tomcat.sh
```

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
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
SMTP_PASSWORD=your_smtp_password
```

> If running on EC2 with an IAM instance role, you can omit the AWS keys — boto3 will pick up the role automatically.

### 4 — Configure the agent

Edit `config/settings.yaml` — minimum required changes:

```yaml
bedrock:
  region: us-east-1            # ← region where you enabled Bedrock
  model_id: us.anthropic.claude-sonnet-4-5-20250514-v1:0  # ← or your preferred Sonnet ID

database:
  host: your-db-host.internal  # ← real DB host for connectivity checks

email:
  smtp_host: smtp.yourcompany.com
  smtp_user: middleware-agent@yourcompany.com
  admin_addrs:
    - admin@yourcompany.com
```

---

## Available Claude Sonnet Model IDs

| Model | Bedrock Model ID | Notes |
|-------|-----------------|-------|
| Claude Sonnet 4.5 | `us.anthropic.claude-sonnet-4-5-20250514-v1:0` | ✅ Latest — recommended |
| Claude 3.5 Sonnet v2 | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` | Previous generation |
| Claude 3.5 Sonnet | `anthropic.claude-3-5-sonnet-20240620-v1:0` | Older |

> The `us.` prefix uses **cross-region inference profiles** — Bedrock automatically routes to the least-loaded US region, giving higher throughput and fewer throttle errors. Recommended over single-region IDs.

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
2024-01-15 10:32:01 INFO     ai_engine — AIEngine ready — model=us.anthropic.claude-sonnet-4-5-20250514-v1:0 region=us-east-1
2024-01-15 10:32:31 INFO     main — Collected 3 signal lines — consulting Claude via Bedrock
2024-01-15 10:32:33 INFO     ai_engine — AI decision: oom / thread_dump (urgency=critical)
2024-01-15 10:32:33 INFO     main — Executing action: thread_dump (incident=oom)
2024-01-15 10:32:34 INFO     main —   ✓ thread_dump succeeded: Saved to /var/lib/middleware-agent/dumps/thread_dump_20240115_103234.txt
2024-01-15 10:32:34 INFO     main — Executing action: increase_heap (incident=oom)
2024-01-15 10:32:34 INFO     main —   ✓ increase_heap succeeded: Heap increased by 500MB → Xmx=1012m (restart required)
```

---

## Testing Scenarios

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
# Terminal 1 — run agent
python agent/main.py

# Terminal 2 — inject error
sudo bash scripts/simulate_errors.sh oom
```

---

## Configuration Reference

```yaml
bedrock:
  region: us-east-1
  model_id: us.anthropic.claude-sonnet-4-5-20250514-v1:0
  max_tokens: 1024              # cap on response length

tomcat:
  catalina_out: /opt/tomcat/logs/catalina.out
  access_log: /opt/tomcat/logs/localhost_access_log.*.txt
  service_name: tomcat
  setenv_sh: /opt/tomcat/bin/setenv.sh
  webapps_url: http://localhost:8080
  health_check_path: /

java:
  heap_increment_mb: 500        # MB added per OOM event

database:
  host: db.internal
  port: 5432

agent:
  poll_interval_seconds: 30
  state_db: /var/lib/middleware-agent/state.db
  dumps_dir: /var/lib/middleware-agent/dumps

thresholds:
  http500_window_seconds: 120
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

### Bedrock integration
- **Converse API** (`boto3 bedrock-runtime.converse`) — standardized API that works across all Bedrock models; cleaner than the raw `invoke_model` endpoint
- **`temperature: 0`** — deterministic decisions; same log lines always produce the same action
- **Robust JSON parsing** — strips accidental code fences, extracts first `{...}` block, fills missing keys with safe defaults
- **Error handling** — distinguishes `ClientError` (auth, throttle, model not enabled) from `BotoCoreError` (network); both fall back to `action: "watch"` safely
- **Cross-region inference** (`us.*` prefix) — higher availability, automatic load balancing across AWS US regions

### State management
Incident counts survive agent restarts via SQLite:
- "OOM has occurred 3 times total → notify admin"
- Restart cooldown: no more than one restart every 5 minutes
- Heap increase cooldown: no more than one increase every 10 minutes

### Log tailing
Tracks seek position per file. Detects log rotation via inode change. Only forwards lines matching the signal regex patterns before touching the LLM, keeping inference cost low.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `AccessDeniedException` | IAM policy missing `bedrock:InvokeModel` — see AWS Setup above |
| `ResourceNotFoundException` | Model ID wrong or model access not granted in Bedrock console |
| `ThrottlingException` | Reduce `poll_interval_seconds` or switch to cross-region `us.*` model ID |
| `NoCredentialsError` | Check `.env` has `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, or attach IAM role |
| Agent sees no log lines | Verify `catalina_out` path in `settings.yaml`; check file permissions |
| `thread_dump failed: Tomcat PID not found` | Tomcat must be running: `systemctl status tomcat` |
| `heap_dump failed` | `jmap` requires running as the same user as Tomcat or root |
| Email not sent | Check SMTP credentials and TLS setting |
