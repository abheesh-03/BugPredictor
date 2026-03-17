# 🐛 BugPredictor — AI-Powered Bug Detection

> Predict bugs before they ship. Real-time AI bug detection for VS Code + Web, powered by Claude AI and vector similarity memory.

![BugPredictor Demo](https://abheesh-03.github.io/BugPredictor/demo.gif)

**🌐 [Live Demo](https://abheesh-03.github.io/BugPredictor/) | 📦 [VS Code Extension](#installation) | 🚀 [API](https://web-production-cb79b.up.railway.app/health)**

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🤖 **AI Bug Detection** | Claude Haiku analyzes code and predicts bugs in real-time |
| 🧠 **Vector Memory** | Voyage AI embeddings remember past bugs (pgvector) |
| 🔧 **Fix with AI** | One-click AI-generated fixes with Apply button |
| 📍 **Exact Line Detection** | Pinpoints the exact line where the bug lives |
| 📊 **Severity Scoring** | 1-10 severity score + confidence percentage |
| 🗂️ **Language Rules** | Specialized rules for Python, JS, TS, Java, C++, SQL |
| 🔁 **Deduplication** | Skips unchanged code, reuses existing snapshots |
| 🚫 **Ignore List** | Mark false positives to never flag again |
| 👥 **Team Memory** | Share bug memory across your entire team |
| 📈 **Trends Dashboard** | Bug types, severity breakdown, 7-day timeline |
| 🔀 **GitHub PR Bot** | Auto-comments bug analysis on every Pull Request |
| 🧩 **VS Code Extension** | Real-time diagnostics as you type |

---

## 🏗️ Architecture
```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   VS Code Ext   │────▶│  FastAPI Backend  │────▶│    Supabase     │
│  (TypeScript)   │     │   (Railway)       │     │  pgvector DB    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │                       │                        │
         │               ┌───────┴───────┐                │
         │               │  Claude Haiku  │                │
         │               │  (Anthropic)  │                │
         │               └───────────────┘                │
         │                       │                        │
┌─────────────────┐     ┌──────────────────┐              │
│   Web App       │────▶│  Voyage AI        │──────────────┘
│ (GitHub Pages)  │     │  Embeddings       │
└─────────────────┘     └──────────────────┘
```

---

## 🚀 Tech Stack

- **Backend**: FastAPI, Python, Railway
- **AI**: Claude Haiku (`claude-haiku-4-5-20251001`) via Anthropic API
- **Embeddings**: Voyage AI `voyage-code-2` (1536 dimensions)
- **Database**: PostgreSQL + pgvector on Supabase
- **Frontend**: Vanilla JS, HTML/CSS (GitHub Pages)
- **Extension**: TypeScript, VS Code API
- **CI/CD**: GitHub Actions (PR Analysis bot)

---

## 📦 Installation

### VS Code Extension
```bash
# Clone the repo
git clone https://github.com/abheesh-03/BugPredictor.git

# Install the extension
code --install-extension bugpredictor-0.0.1.vsix
```

The extension automatically analyzes your code 2 seconds after you stop typing and shows diagnostics inline.

### Run Backend Locally
```bash
git clone https://github.com/abheesh-03/BugPredictor.git
cd BugPredictor

# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Add your ANTHROPIC_API_KEY, VOYAGE_API_KEY, DATABASE_URL

# Start server
uvicorn app.main:app --reload
```

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/analyze` | Analyze code for bugs |
| `POST` | `/fix` | Generate AI fix for buggy code |
| `POST` | `/log-bug` | Log a real bug event |
| `GET` | `/bug-history` | Get last 20 logged bugs |
| `GET` | `/stats` | Bug trends and statistics |
| `POST` | `/ignore` | Add code to ignore list |
| `DELETE` | `/ignore` | Remove from ignore list |
| `POST` | `/team/create` | Create a team |
| `POST` | `/team/join` | Join team with invite code |
| `GET` | `/team/{id}/stats` | Team bug statistics |
| `GET` | `/health` | Health check |

### Example Request
```bash
curl -X POST https://web-production-cb79b.up.railway.app/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "main.py",
    "code": "def divide(a, b):\n    return a / b\n\nresult = divide(10, 0)"
  }'
```

### Example Response
```json
{
  "snapshot_id": "d7c7a571-...",
  "prediction": "Division by zero error will occur when divide(10, 0) is called.",
  "severity": "Critical",
  "score": 10,
  "confidence": 95,
  "bug_line": 3,
  "similar_past_bugs": [],
  "ignored": false
}
```

---

## 🔀 GitHub PR Integration

BugPredictor automatically analyzes every Pull Request and posts a comment with found bugs:
```
🐛 BugPredictor Analysis
Found 1 potential bug(s) in this PR:

🔴 auth.py — Line 14
Severity: Critical (9/10) | Confidence: 95%
SQL injection vulnerability detected...

Powered by BugPredictor 🐛
```

The workflow is defined in `.github/workflows/bugpredictor.yml` and triggers on every PR open/sync.

---

## 👥 Team Shared Memory

Teams share a common bug memory pool. When one teammate encounters a bug, the system remembers it and warns others writing similar code.
```bash
# Create a team via API
curl -X POST .../team/create -d '{"name": "my-team"}'
# Returns invite_code: "a3f8bc12"

# Teammates join with the code
curl -X POST .../team/join -d '{"invite_code": "a3f8bc12", "user_identifier": "alice"}'
```

---

## 🗄️ Database Schema
```sql
code_snapshots  -- Stores code + vector embeddings
bug_events      -- Logs actual bug occurrences  
ignored_patterns -- Stores ignored code hashes
teams           -- Team definitions + invite codes
team_members    -- Team membership
```

---

## 🧠 How the Memory Works

1. When code is analyzed, Voyage AI generates a **1536-dim embedding**
2. The embedding is stored in **pgvector** (PostgreSQL)
3. On next analysis, a **cosine similarity search** finds past bugs with >50% match
4. Claude gets the similar bugs as **context** — making predictions smarter over time

---

## 📁 Project Structure
```
BugPredictor/
├── app/
│   ├── main.py          # FastAPI endpoints
│   ├── db.py            # Database connection
│   ├── embeddings.py    # Voyage AI embeddings
│   └── models.py        # Data models
├── .github/
│   └── workflows/
│       └── bugpredictor.yml  # PR analysis bot
├── index.html           # Web app (GitHub Pages)
├── requirements.txt
└── Procfile             # Railway deployment
```

---

## 🌐 Deployment

- **Backend**: Auto-deployed to [Railway](https://railway.app) on every push to `main`
- **Frontend**: Hosted on [GitHub Pages](https://abheesh-03.github.io/BugPredictor/)
- **Database**: [Supabase](https://supabase.com) (PostgreSQL + pgvector)

---

## 👨‍💻 Author

**Abheesh** — MS Data Science @ University at Buffalo  
Backend Engineer | Python | FastAPI | AWS

[![GitHub](https://img.shields.io/badge/GitHub-abheesh--03-black?style=flat&logo=github)](https://github.com/abheesh-03)

---

## 📄 License

MIT License — feel free to use, modify, and distribute.
