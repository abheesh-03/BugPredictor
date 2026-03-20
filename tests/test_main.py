import pytest
import hashlib
import jwt
import time
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

import sys
from unittest.mock import MagicMock

mock_db = MagicMock()
mock_embeddings = MagicMock()
sys.modules["app.db"] = mock_db
sys.modules["app.embeddings"] = mock_embeddings

mock_db.get_connection = MagicMock()
mock_db.init_db = MagicMock()
mock_embeddings.get_embedding = MagicMock(return_value=[0.1] * 1536)
mock_embeddings.find_similar_bugs = MagicMock(return_value=[])

from app.main import app, get_code_hash, get_language_rules, parse_claude_response

client = TestClient(app, raise_server_exceptions=False)

TEST_JWT_SECRET = "test-secret-for-unit-tests"
TEST_USER_ID = "user-abc-123"

def make_jwt(user_id: str = TEST_USER_ID, secret: str = TEST_JWT_SECRET) -> str:
    payload = {"sub": user_id, "iat": int(time.time()), "exp": int(time.time()) + 3600}
    return jwt.encode(payload, secret, algorithm="HS256")

def auth_headers(user_id: str = TEST_USER_ID) -> dict:
    return {"Authorization": f"Bearer {make_jwt(user_id)}"}

def make_cursor(fetchone_values=None, fetchall_values=None):
    cur = MagicMock()
    if fetchone_values is not None:
        cur.fetchone.side_effect = fetchone_values
    if fetchall_values is not None:
        cur.fetchall.return_value = fetchall_values
    return cur

@pytest.fixture(autouse=True)
def reset_rate_limits():
    from app.main import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()

# ---------------------------------------------------------------------------
# TestGetCodeHash
# ---------------------------------------------------------------------------
class TestGetCodeHash:
    def test_returns_md5_hex(self):
        code = "def foo(): pass"
        expected = hashlib.md5(code.strip().encode()).hexdigest()
        assert get_code_hash(code) == expected

    def test_strips_whitespace(self):
        assert get_code_hash("  hello  ") == get_code_hash("hello")

    def test_different_code_different_hash(self):
        assert get_code_hash("foo") != get_code_hash("bar")

    def test_same_code_same_hash(self):
        assert get_code_hash("x = 1") == get_code_hash("x = 1")


# ---------------------------------------------------------------------------
# TestGetLanguageRules
# ---------------------------------------------------------------------------
class TestGetLanguageRules:
    def test_python(self):
        rules = get_language_rules("main.py")
        assert "Python" in rules
        assert "None/null dereference" in rules

    def test_javascript(self):
        rules = get_language_rules("app.js")
        assert "JavaScript" in rules

    def test_typescript(self):
        rules = get_language_rules("app.ts")
        assert "TypeScript" in rules

    def test_java(self):
        rules = get_language_rules("Main.java")
        assert "Java" in rules

    def test_cpp(self):
        rules = get_language_rules("main.cpp")
        assert "C++" in rules

    def test_sql(self):
        rules = get_language_rules("query.sql")
        assert "SQL" in rules

    def test_unknown_extension(self):
        rules = get_language_rules("file.xyz")
        assert "Null/None dereference" in rules


# ---------------------------------------------------------------------------
# TestParseClaudeResponse
# ---------------------------------------------------------------------------
class TestParseClaudeResponse:
    def test_happy_path(self):
        raw = "LINE: 3\nSEVERITY: Critical\nSCORE: 9\nCONFIDENCE: 95\nMESSAGE: Division by zero risk."
        result = parse_claude_response(raw)
        assert result["bug_line"] == 2
        assert result["severity"] == "Critical"
        assert result["score"] == 9
        assert result["confidence"] == 95
        assert result["prediction"] == "Division by zero risk."

    def test_no_bug(self):
        raw = "LINE: 0\nSEVERITY: None\nSCORE: 0\nCONFIDENCE: 0\nMESSAGE: No obvious bugs detected."
        result = parse_claude_response(raw)
        assert result["severity"] == "None"
        assert result["score"] == 0
        assert result["bug_line"] == 0

    def test_negative_line_clamped(self):
        raw = "LINE: -5\nSEVERITY: Warning\nSCORE: 3\nCONFIDENCE: 50\nMESSAGE: Something."
        result = parse_claude_response(raw)
        assert result["bug_line"] == 0

    def test_malformed_score_defaults_zero(self):
        raw = "LINE: 1\nSEVERITY: Warning\nSCORE: bad\nCONFIDENCE: 50\nMESSAGE: Something."
        result = parse_claude_response(raw)
        assert result["score"] == 0

    def test_malformed_confidence_defaults_zero(self):
        raw = "LINE: 1\nSEVERITY: Warning\nSCORE: 5\nCONFIDENCE: bad\nMESSAGE: Something."
        result = parse_claude_response(raw)
        assert result["confidence"] == 0

    def test_garbage_input_returns_raw_as_prediction(self):
        raw = "totally unexpected output"
        result = parse_claude_response(raw)
        assert result["prediction"] == raw

    def test_warning_severity(self):
        raw = "LINE: 2\nSEVERITY: Warning\nSCORE: 5\nCONFIDENCE: 70\nMESSAGE: Possible null deref."
        result = parse_claude_response(raw)
        assert result["severity"] == "Warning"


# ---------------------------------------------------------------------------
# TestAnalyzeEndpoint
# ---------------------------------------------------------------------------
class TestAnalyzeEndpoint:
    def test_empty_code_returns_422(self):
        resp = client.post("/analyze", json={"filename": "main.py", "code": "   "})
        assert resp.status_code == 422

    def test_missing_filename_returns_422(self):
        resp = client.post("/analyze", json={"filename": "", "code": "x = 1"})
        assert resp.status_code == 422

    def test_db_unavailable_returns_503(self):
        mock_db.get_connection.side_effect = Exception("no db")
        resp = client.post(
            "/analyze",
            json={"filename": "main.py", "code": "def foo(): pass"},
            headers=auth_headers(),
        )
        mock_db.get_connection.side_effect = None
        assert resp.status_code == 503

    def test_ignored_pattern_returns_early(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = ("some-id",)
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        resp = client.post(
            "/analyze",
            json={"filename": "main.py", "code": "def foo(): pass"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ignored"] is True
        assert data["severity"] == "None"

    def test_happy_path(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, None, ("snap-uuid",)]
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn
        mock_embeddings.get_embedding.return_value = [0.1] * 1536
        mock_embeddings.find_similar_bugs.return_value = []

        with patch("app.main.client") as mock_claude:
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="LINE: 2\nSEVERITY: Critical\nSCORE: 9\nCONFIDENCE: 95\nMESSAGE: Division by zero.")]
            mock_claude.messages.create.return_value = mock_msg

            resp = client.post(
                "/analyze",
                json={"filename": "main.py", "code": "def divide(a,b): return a/b"},
                headers=auth_headers(),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["severity"] == "Critical"
        assert data["score"] == 9
        assert data["snapshot_id"] == "snap-uuid"

    def test_dedup_reuses_existing_snapshot(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, ("existing-snap-id",)]
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        with patch("app.main.client") as mock_claude:
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="LINE: 0\nSEVERITY: None\nSCORE: 0\nCONFIDENCE: 0\nMESSAGE: No bugs.")]
            mock_claude.messages.create.return_value = mock_msg

            resp = client.post(
                "/analyze",
                json={"filename": "main.py", "code": "x = 1"},
                headers=auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["snapshot_id"] == "existing-snap-id"

    def test_claude_failure_returns_502(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, None, ("snap-id",)]
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        with patch("app.main.client") as mock_claude:
            mock_claude.messages.create.side_effect = Exception("Claude down")
            resp = client.post(
                "/analyze",
                json={"filename": "main.py", "code": "def foo(): pass"},
                headers=auth_headers(),
            )

        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# TestLogBugEndpoint
# ---------------------------------------------------------------------------
class TestLogBugEndpoint:
    def test_happy_path(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        resp = client.post(
            "/log-bug",
            json={"snapshot_id": "snap-123", "error_message": "ZeroDivisionError"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "bug logged successfully"

    def test_empty_message_returns_422(self):
        resp = client.post(
            "/log-bug",
            json={"snapshot_id": "snap-123", "error_message": "   "},
        )
        assert resp.status_code == 422

    def test_db_failure_returns_500(self):
        mock_db.get_connection.side_effect = Exception("db error")
        resp = client.post(
            "/log-bug",
            json={"snapshot_id": "snap-123", "error_message": "Some error"},
            headers=auth_headers(),
        )
        mock_db.get_connection.side_effect = None
        assert resp.status_code == 500

    def test_correct_values_inserted(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        client.post(
            "/log-bug",
            json={"snapshot_id": "snap-xyz", "error_message": "NullPointerException"},
            headers=auth_headers(),
        )
        call_args = cur.execute.call_args
        assert "snap-xyz" in str(call_args)
        assert "NullPointerException" in str(call_args)


# ---------------------------------------------------------------------------
# TestBugAnalysisPipeline
# ---------------------------------------------------------------------------
class TestBugAnalysisPipeline:
    @pytest.mark.parametrize("filename,expected_keyword", [
        ("main.py", "Python"),
        ("app.js", "JavaScript"),
        ("app.ts", "TypeScript"),
        ("Main.java", "Java"),
        ("main.cpp", "C++"),
        ("main.c", "C"),
        ("query.sql", "SQL"),
    ])
    def test_language_rules_content(self, filename, expected_keyword):
        rules = get_language_rules(filename)
        assert expected_keyword in rules

    def test_memory_context_included_when_similar_bugs(self):
        similar = [{"filename": "old.py", "code": "x/0", "error_message": "ZeroDivision", "similarity_score": 0.95}]
        mock_embeddings.find_similar_bugs.return_value = similar

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, None, ("snap-mem",)]
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        with patch("app.main.client") as mock_claude:
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="LINE: 1\nSEVERITY: Warning\nSCORE: 5\nCONFIDENCE: 80\nMESSAGE: Similar to past bug.")]
            mock_claude.messages.create.return_value = mock_msg

            resp = client.post(
                "/analyze",
                json={"filename": "main.py", "code": "def divide(a,b): return a/b"},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            call_content = mock_claude.messages.create.call_args.kwargs["messages"][0]["content"]
            assert "Similar bugs seen before" in call_content

        mock_embeddings.find_similar_bugs.return_value = []

    def test_no_memory_context_when_no_similar_bugs(self):
        mock_embeddings.find_similar_bugs.return_value = []

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, None, ("snap-no-mem",)]
        conn.cursor.return_value = cur
        mock_db.get_connection.return_value = conn

        with patch("app.main.client") as mock_claude:
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="LINE: 0\nSEVERITY: None\nSCORE: 0\nCONFIDENCE: 0\nMESSAGE: No bugs.")]
            mock_claude.messages.create.return_value = mock_msg

            resp = client.post(
                "/analyze",
                json={"filename": "main.py", "code": "x = 1 + 1"},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            call_content = mock_claude.messages.create.call_args.kwargs["messages"][0]["content"]
            assert "Similar bugs seen before" not in call_content