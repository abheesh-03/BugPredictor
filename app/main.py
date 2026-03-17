from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, validator
from app.db import get_connection, init_db
from app.embeddings import get_embedding, find_similar_bugs
import anthropic
import os
import hashlib
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

class CodeInput(BaseModel):
    filename: str
    code: str
    team_id: str = None

    @validator('code')
    def code_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Code cannot be empty')
        if len(v) > 10000:
            raise ValueError('Code too long (max 10000 characters)')
        return v

    @validator('filename')
    def filename_must_be_valid(cls, v):
        if not v.strip():
            raise ValueError('Filename cannot be empty')
        if len(v) > 255:
            raise ValueError('Filename too long')
        return v

class BugEvent(BaseModel):
    snapshot_id: str
    error_message: str

    @validator('error_message')
    def message_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Error message cannot be empty')
        return v

class IgnoreRequest(BaseModel):
    code: str
    filename: str

class TeamCreate(BaseModel):
    name: str

class TeamJoin(BaseModel):
    invite_code: str
    user_identifier: str

def get_code_hash(code: str) -> str:
    return hashlib.md5(code.strip().encode()).hexdigest()

def get_language_rules(filename: str) -> str:
    ext = filename.split('.')[-1].lower() if '.' in filename else ''
    rules = {
        'py': """Language: Python. Focus on:
- None/null dereference (AttributeError, TypeError)
- Mutable default arguments (def f(x=[]))
- Integer division vs float division
- Missing exception handling for IO/network ops
- Using == instead of 'is' for None checks
- Division by zero in calculations""",
        'js': """Language: JavaScript. Focus on:
- undefined/null reference errors
- == vs === comparison bugs
- Async/await missing try-catch
- var hoisting issues
- Missing return statements in arrow functions""",
        'ts': """Language: TypeScript. Focus on:
- Type assertion errors (as any overuse)
- Optional chaining missing (?.)
- Null/undefined not handled in strict mode
- Generic type mismatches""",
        'java': """Language: Java. Focus on:
- NullPointerException risks
- Unchecked type casting
- Resource leaks (streams, connections not closed)
- String comparison with == instead of .equals()""",
        'cpp': """Language: C++. Focus on:
- Memory leaks (new without delete)
- Buffer overflow risks
- Dangling pointers
- Uninitialized variables""",
        'c': """Language: C. Focus on:
- Buffer overflow (strcpy, gets)
- Memory leaks
- Null pointer dereference
- Format string vulnerabilities""",
        'sql': """Language: SQL. Focus on:
- SQL injection vulnerabilities
- Missing WHERE clause in UPDATE/DELETE
- NULL handling in comparisons"""
    }
    return rules.get(ext, """Focus on common bugs:
- Null/None dereference
- Division by zero
- Off-by-one errors
- Resource leaks
- Injection vulnerabilities""")

@app.on_event("startup")
def startup():
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

@app.post("/team/create")
def create_team(team: TeamCreate):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO teams (name)
            VALUES (%s)
            RETURNING id, invite_code, name;
        """, (team.name,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "team_id": str(row[0]),
            "invite_code": row[1],
            "name": row[2],
            "message": f"Team '{row[2]}' created! Share invite code: {row[1]}"
        }
    except Exception as e:
        logger.error(f"Failed to create team: {e}")
        raise HTTPException(status_code=500, detail="Failed to create team")

@app.post("/team/join")
def join_team(data: TeamJoin):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM teams WHERE invite_code = %s", (data.invite_code,))
        team = cur.fetchone()
        if not team:
            raise HTTPException(status_code=404, detail="Invalid invite code")

        cur.execute("""
            INSERT INTO team_members (team_id, user_identifier)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
        """, (team[0], data.user_identifier))
        conn.commit()
        cur.close()
        conn.close()
        return {
            "team_id": str(team[0]),
            "team_name": team[1],
            "message": f"Joined team '{team[1]}' successfully!"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to join team: {e}")
        raise HTTPException(status_code=500, detail="Failed to join team")

@app.get("/team/{team_id}/stats")
def team_stats(team_id: str):
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("SELECT name, invite_code FROM teams WHERE id = %s", (team_id,))
        team = cur.fetchone()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        cur.execute("""
            SELECT COUNT(*) FROM bug_events be
            JOIN code_snapshots cs ON be.snapshot_id = cs.id
            WHERE cs.team_id = %s
        """, (team_id,))
        total_bugs = cur.fetchone()[0]

        cur.execute("""
            SELECT cs.filename, COUNT(*) as count
            FROM bug_events be
            JOIN code_snapshots cs ON be.snapshot_id = cs.id
            WHERE cs.team_id = %s
            GROUP BY cs.filename
            ORDER BY count DESC LIMIT 5
        """, (team_id,))
        bugs_by_file = [{"filename": r[0], "count": r[1]} for r in cur.fetchall()]

        cur.close()
        conn.close()
        return {
            "team_name": team[0],
            "invite_code": team[1],
            "total_bugs": total_bugs,
            "bugs_by_file": bugs_by_file
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get team stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get team stats")

@app.post("/analyze")
def analyze_code(input: CodeInput):
    try:
        conn = get_connection()
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        code_hash = get_code_hash(input.code)
        cur = conn.cursor()
        cur.execute("SELECT id FROM ignored_patterns WHERE code_hash = %s", (code_hash,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return {
                "snapshot_id": "ignored",
                "prediction": "No obvious bugs detected.",
                "severity": "None",
                "score": 0,
                "confidence": 0,
                "bug_line": 0,
                "similar_past_bugs": [],
                "ignored": True
            }
        cur.close()
    except Exception as e:
        logger.error(f"Ignore list check failed: {e}")

    try:
        embedding = get_embedding(input.code)
        similar_bugs = find_similar_bugs(embedding, conn, team_id=input.team_id)
    except Exception as e:
        logger.error(f"Embedding/search failed: {e}")
        similar_bugs = []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM code_snapshots
            WHERE code = %s AND filename = %s
            ORDER BY created_at DESC LIMIT 1;
        """, (input.code, input.filename))
        existing = cur.fetchone()

        if existing:
            snapshot_id = existing[0]
        else:
            cur.execute("""
                INSERT INTO code_snapshots (filename, code, embedding, team_id)
                VALUES (%s, %s, %s::vector, %s) RETURNING id;
            """, (input.filename, input.code, embedding, input.team_id))
            snapshot_id = cur.fetchone()[0]

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to store snapshot: {e}")
        raise HTTPException(status_code=500, detail="Failed to store code snapshot")

    memory_context = ""
    if similar_bugs:
        memory_context = "\n\nSimilar bugs seen before:\n" + "\n".join(
            f"- [{int(b['similarity_score']*100)}% match] {b['error_message'][:150]}"
            for b in similar_bugs[:3]
        )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": f"""You are an expert bug prediction AI embedded in a VS Code extension. Analyze the code and predict potential bugs.

Code from {input.filename}:
{input.code}
{memory_context}

{get_language_rules(input.filename)}

IMPORTANT: You must respond in this exact format:
LINE: <line number where the bug is, or 0 if unknown>
SEVERITY: <Critical or Warning or None>
SCORE: <severity score from 1-10, where 10 is most severe, 0 if no bug>
CONFIDENCE: <confidence percentage 0-100 of how sure you are this is a bug>
MESSAGE: <your concise bug description, max 3 sentences>

If no bugs found respond exactly with:
LINE: 0
SEVERITY: None
SCORE: 0
CONFIDENCE: 0
MESSAGE: No obvious bugs detected."""
                }
            ]
        )
    except Exception as e:
        logger.error(f"Claude API failed: {e}")
        raise HTTPException(status_code=502, detail="AI analysis unavailable, try again")

    raw = message.content[0].text
    parts = raw.strip().split('\n')

    bug_line = 0
    severity = "Warning"
    score = 0
    confidence = 0
    bug_message = raw

    for part in parts:
        if part.startswith("LINE:"):
            try:
                bug_line = int(part.split(":", 1)[1].strip()) - 1
            except:
                bug_line = 0
        elif part.startswith("SEVERITY:"):
            severity = part.split(":", 1)[1].strip()
        elif part.startswith("SCORE:"):
            try:
                score = int(part.split(":", 1)[1].strip())
            except:
                score = 0
        elif part.startswith("CONFIDENCE:"):
            try:
                confidence = int(part.split(":", 1)[1].strip())
            except:
                confidence = 0
        elif part.startswith("MESSAGE:"):
            bug_message = part.split(":", 1)[1].strip()

    return {
        "snapshot_id": str(snapshot_id),
        "prediction": bug_message,
        "severity": severity,
        "score": score,
        "confidence": confidence,
        "bug_line": max(0, bug_line),
        "similar_past_bugs": similar_bugs,
        "ignored": False
    }

@app.post("/fix")
def fix_code(input: CodeInput):
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": f"""You are an expert code fixer. Fix the bugs in this code.

Code from {input.filename}:
{input.code}

IMPORTANT: You must respond in this exact format:
FIXED_CODE: <the complete fixed code with no markdown, no backticks, just raw code>
EXPLANATION: <one sentence explaining what you fixed>"""
                }
            ]
        )

        raw = message.content[0].text.strip()
        fixed_code = ""
        explanation = ""

        if "FIXED_CODE:" in raw and "EXPLANATION:" in raw:
            fixed_code = raw.split("FIXED_CODE:", 1)[1].split("EXPLANATION:")[0].strip()
            explanation = raw.split("EXPLANATION:", 1)[1].strip()

        return {
            "fixed_code": fixed_code,
            "explanation": explanation
        }
    except Exception as e:
        logger.error(f"Fix failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fix code")

@app.post("/ignore")
def ignore_code(request: IgnoreRequest):
    try:
        conn = get_connection()
        cur = conn.cursor()
        code_hash = get_code_hash(request.code)
        cur.execute("""
            INSERT INTO ignored_patterns (code_hash, filename)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
        """, (code_hash, request.filename))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "Code pattern ignored successfully"}
    except Exception as e:
        logger.error(f"Failed to ignore pattern: {e}")
        raise HTTPException(status_code=500, detail="Failed to ignore pattern")

@app.delete("/ignore")
def unignore_code(request: IgnoreRequest):
    try:
        conn = get_connection()
        cur = conn.cursor()
        code_hash = get_code_hash(request.code)
        cur.execute("DELETE FROM ignored_patterns WHERE code_hash = %s", (code_hash,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "Code pattern unignored successfully"}
    except Exception as e:
        logger.error(f"Failed to unignore pattern: {e}")
        raise HTTPException(status_code=500, detail="Failed to unignore pattern")

@app.post("/log-bug")
def log_bug(bug: BugEvent):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO bug_events (snapshot_id, error_message)
            VALUES (%s, %s);
        """, (bug.snapshot_id, bug.error_message))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "bug logged successfully"}
    except Exception as e:
        logger.error(f"Failed to log bug: {e}")
        raise HTTPException(status_code=500, detail="Failed to log bug")

@app.get("/bug-history")
def bug_history():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT cs.filename, cs.code, be.error_message, be.created_at
            FROM bug_events be
            JOIN code_snapshots cs ON be.snapshot_id = cs.id
            ORDER BY be.created_at DESC
            LIMIT 20;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {
                "filename": row[0],
                "code": row[1][:100],
                "error_message": row[2][:200],
                "created_at": row[3].isoformat()
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Failed to fetch history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch bug history")

@app.get("/stats")
def get_stats():
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM bug_events;")
        total_bugs = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM code_snapshots;")
        total_snapshots = cur.fetchone()[0]

        cur.execute("""
            SELECT cs.filename, COUNT(*) as bug_count
            FROM bug_events be
            JOIN code_snapshots cs ON be.snapshot_id = cs.id
            GROUP BY cs.filename
            ORDER BY bug_count DESC
            LIMIT 5;
        """)
        bugs_by_file = [{"filename": row[0], "count": row[1]} for row in cur.fetchall()]

        cur.execute("""
            SELECT
                COUNT(CASE WHEN LOWER(error_message) LIKE '%critical%' THEN 1 END) as critical,
                COUNT(CASE WHEN LOWER(error_message) LIKE '%warning%' THEN 1 END) as warning,
                COUNT(CASE WHEN LOWER(error_message) NOT LIKE '%critical%'
                      AND LOWER(error_message) NOT LIKE '%warning%' THEN 1 END) as other
            FROM bug_events;
        """)
        row = cur.fetchone()
        severity_breakdown = {"critical": row[0], "warning": row[1], "other": row[2]}

        cur.execute("""
            SELECT DATE(created_at) as day, COUNT(*) as count
            FROM bug_events
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY day
            ORDER BY day ASC;
        """)
        bugs_over_time = [{"day": str(row[0]), "count": row[1]} for row in cur.fetchall()]

        cur.execute("""
            SELECT
                CASE
                    WHEN LOWER(error_message) LIKE '%division%' OR LOWER(error_message) LIKE '%zero%' THEN 'Division by Zero'
                    WHEN LOWER(error_message) LIKE '%null%' OR LOWER(error_message) LIKE '%none%' THEN 'Null Dereference'
                    WHEN LOWER(error_message) LIKE '%injection%' OR LOWER(error_message) LIKE '%sql%' THEN 'SQL Injection'
                    WHEN LOWER(error_message) LIKE '%index%' OR LOWER(error_message) LIKE '%bounds%' THEN 'Index Error'
                    WHEN LOWER(error_message) LIKE '%loop%' OR LOWER(error_message) LIKE '%infinite%' THEN 'Infinite Loop'
                    WHEN LOWER(error_message) LIKE '%leak%' THEN 'Resource Leak'
                    ELSE 'Other'
                END as bug_type,
                COUNT(*) as count
            FROM bug_events
            GROUP BY bug_type
            ORDER BY count DESC;
        """)
        bug_types = [{"type": row[0], "count": row[1]} for row in cur.fetchall()]

        cur.close()
        conn.close()

        return {
            "total_bugs": total_bugs,
            "total_snapshots": total_snapshots,
            "bugs_by_file": bugs_by_file,
            "severity_breakdown": severity_breakdown,
            "bugs_over_time": bugs_over_time,
            "bug_types": bug_types
        }
    except Exception as e:
        logger.error(f"Failed to fetch stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch stats")

@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.close()
        return {"status": "bugpredictor is alive 🚀", "database": "connected"}
    except Exception as e:
        return {"status": "bugpredictor is alive 🚀", "database": "disconnected", "error": str(e)}