# 🐳 Multi-Container Docker Guide

This document describes the container architecture for the **Personal AI Content Agent** and how to run each service in its dedicated Docker container.

---

## 🏗️ Architecture Overview

The application is decomposed into **4 specialized microservices** communicating over an internal bridge network (`news-agent-net`) and sharing runtime directories (`artifacts/`, `assets/`, `userdata/`).

```
                              ┌──────────────────────────────────────────────┐
                              │            Host OS / User Browser            │
                              └───────┬──────────────────────────────┬───────┘
                                      │ Port 8000                    │ Telegram App
                                      ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Docker Network: news-agent-net                                              │
│                                                                             │
│   ┌───────────────────────────┐           ┌─────────────────────────────┐   │
│   │     news-agent-api        │           │      news-agent-bot         │   │
│   │   FastAPI Orchestrator    │           │  Interactive Telegram Bot   │   │
│   │   Swagger UI & REST API   │           │  (Stage 1 & 2 Human Loop)   │   │
│   └─────────────┬─────────────┘           └──────────────┬──────────────┘   │
│                 │                                        │                  │
│                 │          ┌──────────────────┐          │                  │
│                 ├─────────►│ news-agent-redis │◄─────────┤                  │
│                 │          │  Cache & States  │          │                  │
│                 │          └──────────────────┘          │                  │
│                 │                                        │                  │
│   ┌─────────────┴─────────────┐                          │                  │
│   │   news-agent-scheduler    │                          │                  │
│   │   Autonomous 24h Scanner  │                          │                  │
│   │   (Strict 4h Interval)    │                          │                  │
│   └─────────────┬─────────────┘                          │                  │
│                 │                                        │                  │
└─────────────────┼────────────────────────────────────────┼──────────────────┘
                  ▼                                        ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ Shared Host Volumes:                                        │
    │ • ./artifacts  -> Generated videos, 3:4 carousels, scripts  │
    │ • ./assets     -> Fonts, brand templates, avatar selfies    │
    │ • ./userdata   -> Scraping sessions & cookies               │
    └─────────────────────────────────────────────────────────────┘
```

---

## 📦 Container Services Breakdown

| Container | Image / Dockerfile | Internal Command | Host Port | Role |
| :--- | :--- | :--- | :--- | :--- |
| **`news-agent-api`** | `Dockerfile` | `uvicorn src.api.main:app` | `8000:8000` | REST API, OpenAPI docs (`/docs`), programmatic triggers |
| **`news-agent-bot`** | `Dockerfile` | `python -m src.cli bot` | - | Telegram Bot polling, interactive buttons (`Know more`, `Generate Vid`, `Post as Images`), HeyGen video synthesis |
| **`news-agent-scheduler`** | `Dockerfile` | `python -m src.cli scheduler` | - | Autonomous cron loop scanning 24h AI news & pushing cards |
| **`news-agent-redis`** | `redis:7-alpine` | `redis-server` | `6379:6379` | In-memory cache & state broker |

---

## 🚀 How to Run

### 1. Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS) or Docker Engine + Docker Compose (Linux).
- Configure your `.env` file with your API keys:
  - `GEMINI_API_KEY`
  - `HEYGEN_API_KEY`
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`

---

### 2. Launch All Services

#### On Windows:
Double-click **`docker-start.bat`** or run:
```cmd
docker compose up -d --build
```

#### On Linux / macOS / Cloud:
```bash
chmod +x docker-start.sh docker-stop.sh
./docker-start.sh
```

---

### 3. Verify Containers

Check the health and status of all services:
```bash
docker compose ps
```

Expected output:
```text
NAME                    IMAGE               COMMAND                  SERVICE      STATUS
news-agent-api          ...                 "uvicorn src.api.mai…"   api          Up (healthy)
news-agent-bot          ...                 "python -m src.cli b…"   bot          Up
news-agent-scheduler    ...                 "python -m src.cli s…"   scheduler    Up
news-agent-redis        redis:7-alpine      "docker-entrypoint.s…"   redis        Up (healthy)
```

---

## 🌐 Endpoints & Dashboards

- **FastAPI Interactive Docs (Swagger UI):**  
  [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health Check:**  
  [http://localhost:8000/health](http://localhost:8000/health)
- **Telegram Bot:**  
  Open your Telegram chat and send `"news updates"` or `"hi"` to interact with your agent!

---

## 🛠️ Common Operations & Commands

### View Live Logs
View all logs:
```bash
docker compose logs -f
```
Or view logs for a specific service:
```bash
docker compose logs -f bot
docker compose logs -f api
docker compose logs -f scheduler
```

### Trigger an Instant Scan via API
```bash
curl -X POST http://localhost:8000/api/v1/research/run
```

### Stop Containers Cleanly
```bash
docker compose down
```
*(Or double-click `docker-stop.bat`)*

### Rebuild a Specific Service after Code Change
```bash
docker compose up -d --build bot
```
