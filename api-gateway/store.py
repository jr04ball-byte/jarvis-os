"""V24 P1: persistence layer extracted verbatim from main.py.

Conversation history, TF-IDF document RAG, and the performance monitor.
Connections are short-lived per operation (see Cycles 1/4); main.py
re-exports these names for backward compatibility.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)



class ConversationDB:
    """Conversation history. Opens a short-lived connection per operation:
    a single shared sqlite3 connection across async workers raises
    'Recursive use of cursors not allowed' under concurrent requests."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(
            os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data")), "conversations.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self.create_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_tables(self):
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    model TEXT,
                    assistant_profile TEXT DEFAULT 'general',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "assistant_profile" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN assistant_profile TEXT DEFAULT 'general'")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
            """)

    def create_conversation(self, title: str, model: str, assistant_profile: str = "general"):
        profile = assistant_profile.lower() if assistant_profile else "general"
        if profile not in {"general", "sales"}:
            profile = "general"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO conversations (title, model, assistant_profile) VALUES (?, ?, ?)",
                (title, model, profile)
            )
            return cursor.lastrowid

    def get_conversation_profile(self, conv_id: int) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(assistant_profile, 'general') FROM conversations WHERE id = ?",
                (conv_id,)
            ).fetchone()
            return (row[0] or "general").lower() if row else "general"

    def add_message(self, conv_id: int, role: str, content: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (conv_id, role, content)
            )

    def get_conversation(self, conv_id: int, limit: int | None = None):
        with self._lock, self._connect() as conn:
            if limit is None:
                cursor = conn.execute(
                    "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                    (conv_id,)
                )
                rows = cursor.fetchall()
            else:
                cursor = conn.execute(
                    """SELECT role, content FROM messages
                       WHERE conversation_id = ?
                       ORDER BY id DESC LIMIT ?""",
                    (conv_id, limit)
                )
                rows = list(reversed(cursor.fetchall()))
            return [{"role": row[0], "content": row[1]} for row in rows]

    def conversation_exists(self, conv_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conv_id,)
            )
            return cursor.fetchone() is not None

    def list_conversations(self):
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "SELECT id, title, model, COALESCE(assistant_profile, 'general'), created_at FROM conversations ORDER BY created_at DESC"
            )
            return [
                {"id": row[0], "title": row[1], "model": row[2], "assistant_profile": row[3], "created_at": row[4]}
                for row in cursor.fetchall()
            ]


class DocumentRAG:
    """TF-IDF knowledge base, persisted in SQLite alongside conversations.
    The index rebuilds from disk on startup, so documents survive restarts."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(
            os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data")), "conversations.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self.documents = {}
        self.vectorizer = TfidfVectorizer(stop_words='english', max_features=10000)
        self.tfidf_matrix = None
        self.doc_ids = []
        self._init_table()
        self._load_all()
        logger.info("RAG system initialized (TF-IDF)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_table(self):
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    doc_id TEXT PRIMARY KEY,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _load_all(self):
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_id, content FROM rag_documents ORDER BY created_at"
            ).fetchall()
        if rows:
            self.documents = {row[0]: row[1] for row in rows}
            self._rebuild_index()
            logger.info(f"RAG index rebuilt from disk ({len(self.doc_ids)} documents)")

    def _rebuild_index(self):
        self.doc_ids = list(self.documents.keys())
        corpus = [self.documents[did] for did in self.doc_ids]
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)

    def add_document(self, text: str, doc_id: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rag_documents (doc_id, content) VALUES (?, ?)",
                (doc_id, text)
            )
        self.documents[doc_id] = text
        self._rebuild_index()
        logger.info(f"Added document: {doc_id} (total: {len(self.doc_ids)})")

    def search(self, query: str, n_results: int = 3):
        if not self.doc_ids or self.tfidf_matrix is None:
            return []

        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        top_indices = similarities.argsort()[-n_results:][::-1]

        results = []
        for idx in top_indices:
            if similarities[idx] > 0.05:
                results.append(self.documents[self.doc_ids[idx]])
        return results

    def augment_prompt(self, user_query: str):
        relevant_docs = self.search(user_query)
        if not relevant_docs:
            return user_query

        context = "\n\n".join(relevant_docs)
        return f"""Based on the following context, answer the question.

Context:
{context}

Question: {user_query}

Answer:"""


class PerformanceMonitor:
    def __init__(self):
        self.stats = defaultdict(lambda: {
            "requests": 0,
            "tokens": 0,
            "total_time": 0.0
        })

    def record(self, model: str, tokens: int, duration: float):
        self.stats[model]["requests"] += 1
        self.stats[model]["tokens"] += tokens
        self.stats[model]["total_time"] += duration

    def get_stats(self):
        result = {}
        for model, data in self.stats.items():
            result[model] = {
                "requests": data["requests"],
                "total_tokens": data["tokens"],
                "avg_tokens_per_request": data["tokens"] / data["requests"] if data["requests"] > 0 else 0,
                "avg_time_seconds": data["total_time"] / data["requests"] if data["requests"] > 0 else 0,
                "tokens_per_second": data["tokens"] / data["total_time"] if data["total_time"] > 0 else 0
            }
        return result
