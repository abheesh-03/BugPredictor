import os
import voyageai
from dotenv import load_dotenv
import hashlib

load_dotenv()

voyage_client = voyageai.Client(api_key=os.getenv("Vpa-9h7cl524z1W1N5W_xPXOi-l_eUfD3RDeHZOqeX9SOT9"))

def get_embedding(code: str) -> list[float]:
    try:
        result = voyage_client.embed(
            [code[:2000]],
            model="voyage-code-2",
            input_type="document"
        )
        return result.embeddings[0]
    except Exception as e:
        print(f"Voyage embedding failed, using fallback: {e}")
        return _fallback_embedding(code)

def _fallback_embedding(code: str) -> list[float]:
    hash_bytes = hashlib.sha512(code.encode()).digest()
    vector = [b / 255.0 for b in hash_bytes]
    while len(vector) < 1536:
        vector.extend(vector[:min(64, 1536 - len(vector))])
    vector = vector[:1536]
    magnitude = sum(x**2 for x in vector) ** 0.5
    return [x / magnitude if magnitude else 0.0 for x in vector]

def find_similar_bugs(embedding: list[float], conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT cs.filename, cs.code, be.error_message,
               1 - (cs.embedding <-> %s::vector) AS similarity
        FROM code_snapshots cs
        JOIN bug_events be ON be.snapshot_id = cs.id
        WHERE 1 - (cs.embedding <-> %s::vector) > 0.5
        ORDER BY similarity DESC
        LIMIT 5;
    """, (embedding, embedding))
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "filename": row[0],
            "code": row[1],
            "error_message": row[2],
            "similarity_score": round(row[3], 4)
        }
        for row in rows
    ]