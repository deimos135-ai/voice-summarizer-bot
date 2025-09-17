import aiosqlite
from typing import List, Tuple

DB_PATH = "notes.db"

# Нова схема: час зберігаємо як UTC epoch (INTEGER)
CREATE_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_chat_time ON notes(chat_id, created_at_epoch);
"""

async def init_db():
    """Створює таблиці та запускає міграцію зі старого формату, якщо треба."""
    async with aiosqlite.connect(DB_PATH) as db:
        # створення поточної схеми
        for stmt in CREATE_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                await db.execute(s)
        await db.commit()
        # одноразова міграція зі старого поля created_at (TEXT ISO) -> created_at_epoch
        await migrate_if_needed(db)

async def migrate_if_needed(db: aiosqlite.Connection):
    try:
        cur = await db.execute("PRAGMA table_info(notes)")
        cols = [r[1] for r in await cur.fetchall()]
        if "created_at" in cols and "created_at_epoch" in cols:
            # заповнюємо порожні created_at_epoch на основі created_at
            cur = await db.execute("SELECT id, created_at, created_at_epoch FROM notes WHERE created_at_epoch IS NULL OR created_at_epoch = ''")
            rows = await cur.fetchall()
            if rows:
                import pytz
                from datetime import datetime
                for _id, created_at, _ in rows:
                    try:
                        dt = datetime.fromisoformat(created_at)
                        if dt.tzinfo is None:
                            dt = pytz.timezone("Europe/Kyiv").localize(dt)
                        ts = int(dt.astimezone(pytz.UTC).timestamp())
                    except Exception:
                        ts = 0
                    await db.execute("UPDATE notes SET created_at_epoch=? WHERE id=?", (ts, _id))
                await db.execute("CREATE INDEX IF NOT EXISTS idx_notes_chat_time ON notes(chat_id, created_at_epoch)")
                await db.commit()
    except Exception:
        # мовчки ігноруємо — міграція не критична, якщо старого поля немає
        pass

async def add_note(user_id: str, chat_id: str, text: str, created_at_epoch: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO notes (user_id, chat_id, text, created_at_epoch) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, text, created_at_epoch),
        )
        await db.commit()

async def get_notes_between(chat_id: str, start_epoch: int, end_epoch: int) -> List[Tuple[int, str, str, str, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, chat_id, text, created_at_epoch FROM notes "
            "WHERE chat_id=? AND created_at_epoch >= ? AND created_at_epoch < ? "
            "ORDER BY created_at_epoch ASC",
            (chat_id, start_epoch, end_epoch)
        )
        rows = await cur.fetchall()
    return rows
