import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS code_snapshots (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            filename TEXT,
            code TEXT,
            embedding vector(1536),
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bug_events (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            snapshot_id UUID REFERENCES code_snapshots(id),
            error_message TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()