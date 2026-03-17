from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
from app.db import get_connection, init_db
from app.embeddings import get_embedding, find_similar_bugs
import anthropic
import os
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

@app.on_event("startup")
def startup():
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

@app.post("/analyze")
def analyze_code(input: CodeInput):
    try:
        conn = get_connection()
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        embedding = get_embedding(input.code)
        similar_bugs = find_similar_bugs(embedding, conn)
    except Exception as e:
        logger.error(f"Embedding/search failed: {e}")
        similar_bugs = []

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO code_snapshots (filename, code, embedding)
            VALUES (%s, %s, %s::vector) RETURNING id;
        """, (input.filename, input.code, embedding))
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

Check for these bug categories:
1. Division by zero
2. Null/None dereference
3. Off-by-one errors
4. Type mismatches
5. Resource leaks
6. Infinite loops
7. Logic errors
8. Unhandled exceptions
9. Race conditions
10. SQL/command injection

IMPORTANT: You must respond in this exact format:
LINE: <line number where the bug is, or 0 if unknown>
SEVERITY: <Critical or Warning>
MESSAGE: <your concise bug description, max 3 sentences>

If no bugs found respond exactly with:
LINE: 0
SEVERITY: None
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
    bug_message = raw

    for part in parts:
        if part.startswith("LINE:"):
            try:
                bug_line = int(part.split(":", 1)[1].strip()) - 1
            except:
                bug_line = 0
        elif part.startswith("SEVERITY:"):
            severity = part.split(":", 1)[1].strip()
        elif part.startswith("MESSAGE:"):
            bug_message = part.split(":", 1)[1].strip()

    return {
        "snapshot_id": str(snapshot_id),
        "prediction": bug_message,
        "severity": severity,
        "bug_line": max(0, bug_line),
        "similar_past_bugs": similar_bugs
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

@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.close()
        return {"status": "bugpredictor is alive 🚀", "database": "connected"}
    except Exception as e:
        return {"status": "bugpredictor is alive 🚀", "database": "disconnected", "error": str(e)}