import anthropic
import os
import hashlib
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def get_embedding(code: str) -> list[float]:
    hash_bytes = hashlib.sha512(code.encode()).digest()
    # sha512 gives 64 bytes, make 64 floats by using each byte
    vector = [b / 255.0 for b in hash_bytes]
    # Pad to 1536 dimensions by repeating
    while len(vector) < 1536:
        vector.extend(vector[:min(64, 1536 - len(vector))])
    vector = vector[:1536]
    # Normalize
    magnitude = sum(x**2 for x in vector) ** 0.5
    return [x / magnitude if magnitude else 0.0 for x in vector]

def find_similar_bugs(embedding: list[float], conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT cs.filename, cs.code, be.error_message,
               cs.embedding <-> %s::vector AS distance
        FROM code_snapshots cs
        JOIN bug_events be ON be.snapshot_id = cs.id
        ORDER BY distance ASC
        LIMIT 5;
    """, (embedding,))
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "filename": row[0],
            "code": row[1],
            "error_message": row[2],
            "similarity_score": round(1 - row[3], 4)
        }
        for row in rows
    ]