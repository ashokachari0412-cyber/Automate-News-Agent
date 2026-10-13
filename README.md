# 🤖 Personal AI Content Agent — Google-Powered Workflow

> **End-to-End Workflow:** Strict 24h AI Research → Clean Telegram Digest → Interactive Video Blueprint (35–40s) → Google Video Generation (Veo 3.1 / Avatar) → Video Delivered in Telegram.

---

## 🏛 System Architecture (Steps 1 & 2 Completed)

```
STEP 1: Clean Breaking News (Strict 24h Window)
┌────────────────────────────────────────────────────────┐
│ 📰 #1 | OpenAI Expands 'Operator' Agent to Pro Users    │
│ 🏢 Source: @OpenAI • Category: #AgenticWorkflow        │
│                                                        │
│ 📝 What Happened:                                      │
│ OpenAI has officially rolled out Operator to Pro       │
│ subscribers. It executes autonomous multi-step web     │
│ tasks like forms, data cross-checking, and booking.    │
│                                                        │
│ 💡 Key Takeaways:                                      │
│ • Handles complex browser navigation and auth portals  │
│ • Strict security guardrails for credentials           │
│ • API entering private beta for developers             │
│                                                        │
│ [🔗 Original Post]   [🎬 Generate Video for This]       │ <--- YOU TAP THIS
└────────────────────────────────────────────────────────┘
                           │
                           ▼ (Tapping "Generate Video")
STEP 2: 35-40s Video Blueprint & Avatar Selection
┌────────────────────────────────────────────────────────┐
│ 🎬 35-40s VIDEO BLUEPRINT:                             │
│ 📌 Topic: OpenAI Operator Agent                        │
│ ⏱ Duration: 38s (9:16 Vertical Short)                  │
│                                                        │
│ 🎣 Hook (0-5s):                                        │
│ "OpenAI just killed browser tabs forever—meet          │
│ Operator."                                             │
│                                                        │
│ 🎞 Clip Breakdown:                                     │
│ • Scene 1 (00:00 - 00:08): Avatar Intro                │
│ • Scene 2 (00:08 - 00:20): Core Web Demo / Benchmark   │
│ • Scene 3 (00:20 - 00:32): Developer Impact            │
│ • Scene 4 (00:32 - 00:38): Call to Action & Outro      │
│                                                        │
│ 👤 Active Avatar: Tech Founder (Google Gemini)         │
│                                                        │
│ [🚀 Render Final Video]  [👤 Switch Avatar]            │ <--- YOU TAP THIS
│ [✏️ Edit Script]                                       │
└────────────────────────────────────────────────────────┘
                           │
                           ▼ (Tapping "Render Final Video")
STEP 3: Live Progress & Playable Video Delivered in Telegram
┌────────────────────────────────────────────────────────┐
│ ⏳ Synthesizing Avatar & Rendering 9:16 Video...        │
│ [██████████░░░░] 65% - Finalizing H.264 encode...      │
└────────────────────────────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 📹 [Playable MP4 Video directly in Telegram chat]      │
│ 🎬 Duration: 38s Vertical Short                        │
│                                                        │
│ [👍 Approve Video]  [🔄 Re-generate]                   │
└────────────────────────────────────────────────────────┘
```

---

---

## 🐳 Running with Docker (Multi-Container Architecture)

The system is fully dockerized with a dedicated container for each core service:
1. **`news-agent-api`**: FastAPI Orchestrator & REST Engine (Port `8000`)
2. **`news-agent-bot`**: Interactive Telegram Bot (Human-in-the-Loop review, button clicks, HeyGen clone generation, Instagram 3:4 carousels)
3. **`news-agent-scheduler`**: Autonomous News Pipeline Scheduler (Periodic strict 24h AI news scanning & dispatch)
4. **`news-agent-redis`**: Shared In-Memory Cache & Message Broker (Port `6379`)

### Quick Start with Docker
```bash
# Build and run all services in background
docker compose up -d --build

# View live streaming logs across all containers
docker compose logs -f

# Check container health status
docker compose ps
```
*(Windows users can simply double-click `docker-start.bat`)*

See [DOCKER.md](file:///d:/Projects/Automate-News-Agent/DOCKER.md) for full details on volume mounts, scaling, and endpoint testing.

---

## 💻 Running Locally (Without Docker)

### 1. Start the Interactive Bot Listener
```bash
python -m src.cli bot
```
*(Or double-click `start_bot.bat`)*

### 2. Run a News Scan & Send Cards to Telegram
```bash
python -m src.cli run --telegram
```
*(Or double-click `start_daily_pipeline.bat`)*

### 3. Start the FastAPI Server
```bash
python -m src.cli serve --port 8000
```
Interactive API docs at: `http://localhost:8000/docs`

---

## 🧪 Testing

Run the full automated test suite:
```bash
pytest -v
```
*(All tests passing: collectors, 24h strict window, LangGraph state machine, Content Agent, 9:16 Video Generator, and FastAPI endpoints).*
