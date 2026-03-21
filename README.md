# BugPredictor — AI-Powered Bug Detection

> Predict bugs before they ship. Real-time AI bug detection for VS Code + web, powered by Claude AI and vector similarity memory.

**[Live Demo](https://abheesh-03.github.io/BugPredictor/) · [VS Code Extension](#installation) · [API](https://web-production-cb79b.up.railway.app/health)**

---

## Features

| Feature | Description |
|---------|-------------|
| AI Bug Detection | Claude Haiku analyzes code and predicts bugs in real-time |
| Vector Memory | Voyage AI embeddings remember past bugs via pgvector similarity search |
| Fix with AI | AI-generated fixes with Apply / Show Diff options directly in VS Code |
| CodeLens Integration | Inline bug annotations above affected lines with one-click fix and ignore |
| Exact Line Detection | Pinpoints the line where the bug lives, not just the file |
| Severity + Confidence Scoring | 1–10 severity score and confidence percentage per prediction |
| Language-Specific Rules | Specialized rule sets for Python, JS, TS, Java, C++, C, SQL |
| Deduplication | MD5 hash dedup skips unchanged code and reuses existing snapshots |
| Ignore List | Mark false positives — never flagged again for that user |
| Team Shared Memory | Share bug memory across a team via invite code |
| Bug Trends Dashboard | Severity breakdown, bug types, 7-day timeline |
| GitHub PR Bot | Auto-comments bug analysis on every pull request |
| JWT Authentication | Supabase Auth with per-user RLS on all tables |
| Rate Limiting | Per-user JWT-aware rate limiting via slowapi (10/min auth, 3/min anon) |
| Eval Framework | 20-case labeled test suite — 94.1% F1 on first run |

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   VS Code Ext   │────▶│  FastAPI Backend  │────▶│    Supabase     │
│  (TypeScript)   │     │   (Railway)       │     │  pgvector DB    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │                       │                        │
         │               ┌───────┴───────┐                │
         │               │  Claude Haiku  │                │
         │               │  (Anthropic)   │                │
         │               └───────────────┘                │
         │                       │                        │
┌─────────────────┐     ┌──────────────────┐              │
│   Web App       │────▶│  Voyage AI        │──────────────┘
│ (GitHub Pages)  │     │  Embeddings       │
└─────────────────┘     └──────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, Python 3.11, Railway |
| AI | Claude Haiku (`claude-haiku-4-5-20251001`) — Anthropic API |
| Embeddings | Voyage AI `voyage-code-2` (1536 dimensions) |
| Database | PostgreSQL + pgvector on Supabase |
| Auth | Supabase Auth + JWT, RLS on all tables |
| Frontend | Vanilla JS / HTML (GitHub Pages) |
| VS Code Extension | TypeScript, VS Code API (CodeLens, TreeView, Diagnostics) |
| Rate Limiting | slowapi — JWT-aware per-user buckets |
| CI/CD | GitHub Actions (PR analysis bot) |
| Testing | pytest, 38 unit tests (offline, fully mocked) |
| Evals | Custom eval framework — 20 labeled cases, precision/recall/F1 reporting |

---

## Eval Results

The project ships with a labeled evaluation suite that runs against the live API:

```
Accuracy   : 18/20 (90.0%)
Precision  : 94.1%
Recall     : 94.1%
F1 Score   : 94.1%
False Pos  : 33.3%  (1 false positive on clean code)

Confidence calibration : YES
  Avg confidence (correct)   : 79.7%
  Avg confidence (incorrect) : 37.5%

Per-language:
  JavaScript  : 4/4  (100%)
  TypeScript  : 2/2  (100%)
  Python      : 12/14 (86%)
```

Run it yourself:
```bash
python evals/run_evals.py --token <your_jwt>
```

---

## Installation

### VS Code Extension

```bash
git clone https://github.com/abheesh-03/BugPredictor.git
cd BugPredictor/extension
npm install && npm run compile
npx vsce package --no-dependencies --allow-missing-repository
code --install-extension bugpredictor-0.0.5.vsix
```

After installing, set your auth token via the command palette:
`Cmd+Shift+P` → `BugPredictor: Set Auth Token`

The extension auto-analyzes 2 seconds after you stop typing and on every save.

### Run Backend Locally

```bash
git clone https://github.com/abheesh-03/BugPredictor.git
cd BugPredictor

pip install -r requirements.txt

cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, VOYAGE_API_KEY, DATABASE_URL, SUPABASE_JWT_SECRET

uvicorn app.main:app --reload
```

---

## API Endpoints

| Method | Endpoint | Description | Rate Limit |
|--------|----------|-------------|------------|
| `POST` | `/analyze` | Analyze code for bugs | 10/min (auth), 3/min (anon) |
| `POST` | `/fix` | Generate AI fix for buggy code | 5/min |
| `POST` | `/log-bug` | Log a real bug event | 20/min |
| `GET` | `/bug-history` | Last 20 logged bugs | — |
| `GET` | `/stats` | Bug trends and statistics | — |
| `POST` | `/ignore` | Add code pattern to ignore list | — |
| `DELETE` | `/ignore` | Remove from ignore list | — |
| `POST` | `/team/create` | Create a team | — |
| `POST` | `/team/join` | Join team with invite code | — |
| `GET` | `/team/{id}/stats` | Team bug statistics | — |
| `GET` | `/health` | Health check | — |

### Example

```bash
curl -X POST https://web-production-cb79b.up.railway.app/analyze \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "filename": "main.py",
    "code": "def divide(a, b):\n    return a / b\n\nresult = divide(10, 0)"
  }'
```

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

## How the Memory Works

1. Code is analyzed → Voyage AI generates a **1536-dim embedding**
2. The embedding is stored in **pgvector** (PostgreSQL)
3. On the next analysis, a **cosine similarity search** finds past bugs with >50% match
4. Claude receives matching bugs as context — predictions improve over time as more bugs are logged
5. Team members share a memory pool via invite code, so one person's bug history benefits the whole team

---

## GitHub PR Integration

BugPredictor automatically analyzes every pull request and posts a comment:

```
BugPredictor Analysis

Found 1 potential bug(s) in this PR:

auth.py — Line 14
Severity: Critical (9/10) | Confidence: 95%
SQL injection vulnerability detected in query construction.

Powered by BugPredictor
```

Defined in `.github/workflows/bugpredictor.yml`, triggers on every PR open and sync event.

---

## Team Shared Memory

```bash
# Create a team
curl -X POST .../team/create \
  -H "Authorization: Bearer <token>" \
  -d '{"name": "my-team"}'
# Returns: { "invite_code": "a3f8bc12" }

# Teammates join with the code
curl -X POST .../team/join \
  -d '{"invite_code": "a3f8bc12", "user_identifier": "alice"}'
```

---

## Database Schema

```sql
code_snapshots   -- Code + 1536-dim vector embeddings (pgvector)
bug_events       -- Logged bug occurrences with user_id
predictions      -- Analysis results per snapshot
ignored_patterns -- Per-user ignored code hashes (MD5)
teams            -- Team definitions + invite codes
team_members     -- Team membership
```

---

## Project Structure

```
BugPredictor/
├── app/
│   ├── main.py           # FastAPI endpoints + rate limiting
│   ├── db.py             # Database connection
│   ├── embeddings.py     # Voyage AI embeddings + similarity search
│   └── models.py         # Data models
├── extension/
│   ├── src/
│   │   └── extension.ts  # VS Code extension (CodeLens, TreeView, Diagnostics)
│   ├── images/
│   │   └── bug.svg       # Activity bar icon
│   └── package.json      # Extension manifest
├── evals/
│   ├── eval_cases.json   # 20 labeled test cases
│   └── run_evals.py      # Eval runner (precision, recall, F1, calibration)
├── tests/
│   └── test_main.py      # 38 unit tests (fully mocked, no credentials needed)
├── .github/
│   └── workflows/
│       └── bugpredictor.yml  # PR analysis bot
├── index.html            # Web app (GitHub Pages)
├── requirements.txt
├── .python-version       # Pins Python 3.11.9 for Railway
└── Procfile              # Railway deployment
```

---

## Running Tests

```bash
# Unit tests (no credentials needed — all external I/O is mocked)
pytest tests/test_main.py -v

# Eval suite (hits live Railway API)
python evals/run_evals.py --token <your_jwt>

# Filter to a specific language
python evals/run_evals.py --token <your_jwt> --filter py_
```

---

## Deployment

- **Backend**: Auto-deployed to [Railway](https://railway.app) on every push to `main`
- **Frontend**: Hosted on [GitHub Pages](https://abheesh-03.github.io/BugPredictor/)
- **Database**: [Supabase](https://supabase.com) — PostgreSQL + pgvector, RLS enabled

---

## Author

**Abheesh** — MS Data Science, University at Buffalo  
2 years backend experience · Python · FastAPI · TypeScript

[![GitHub](https://img.shields.io/badge/GitHub-abheesh--03-black?style=flat&logo=github)](https://github.com/abheesh-03)

---

## License

MIT
