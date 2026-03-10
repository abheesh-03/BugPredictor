from fastapi import FastAPI
from pydantic import BaseModel
from app.db import get_connection, init_db
from app.embeddings import get_embedding, find_similar_bugs
import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

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

class BugEvent(BaseModel):
    snapshot_id: str
    error_message: str

@app.on_event("startup")
def startup():
    init_db()

@app.post("/analyze")
def analyze_code(input: CodeInput):
    conn = get_connection()
    embedding = get_embedding(input.code)
    similar_bugs = find_similar_bugs(embedding, conn)

    # Store snapshot
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO code_snapshots (filename, code, embedding)
        VALUES (%s, %s, %s::vector) RETURNING id;
    """, (input.filename, input.code, embedding))
    snapshot_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    # Ask Claude for a prediction
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"""You are a bug prediction AI. Analyze this code and predict potential bugs.

Code from {input.filename}:
{input.code}

Similar past bugs found:
{similar_bugs}

Give a concise warning if you spot any likely bugs."""
            }
        ]
    )

    return {
        "snapshot_id": str(snapshot_id),
        "prediction": message.content[0].text,
        "similar_past_bugs": similar_bugs
    }

@app.post("/log-bug")
def log_bug(bug: BugEvent):
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

@app.get("/health")
def health():
    return {"status": "bugpredictor is alive 🚀"}