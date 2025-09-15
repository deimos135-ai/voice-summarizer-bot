import aiosqlite
from typing import List, Tuple
from datetime import datetime

DB_PATH = "notes.db"

CREATE_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in CREATE_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await db.execute(s)
        await db.commit()

async def add_note(user_id: str, chat_id: str, text: str, created_at: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO notes (user_id, chat_id, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, text, created_at.isoformat()),
        )
        await db.commit()

async def get_notes_between(chat_id: str, start: datetime, end: datetime) -> List[Tuple[int, str, str, str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, chat_id, text, created_at FROM notes WHERE chat_id=? AND created_at BETWEEN ? AND ? ORDER BY created_at ASC",
            (chat_id, start.isoformat(), end.isoformat())
        )
        rows = await cur.fetchall()
    return rows
