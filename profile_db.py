"""
profile_db.py — local storage for JARVIS
 
handles everything that needs to persist between sessions:
user prefs, conversation history, notes, projects, wake word logs.
 
all data stays on-device in a sqlite file next to this script.
nothing leaves the machine.
 
usage:
    from profile_db import JarvisDB
    db = JarvisDB()
    db.set_preference("voice_pack", "genz")
"""
 
import sqlite3
import os
import json
from datetime import datetime
 
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_profile.db")
 
 
class JarvisDB:
 
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()
 
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
 
    def _init_db(self):
        with self._connect() as conn:
 
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profile (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
 
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    role        TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content     TEXT NOT NULL,
                    voice_pack  TEXT DEFAULT 'professional',
                    timestamp   TEXT NOT NULL
                )
            """)
 
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT,
                    content     TEXT NOT NULL,
                    tags        TEXT DEFAULT '[]',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
 
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL UNIQUE,
                    description     TEXT DEFAULT '',
                    completion_pct  REAL DEFAULT 0.0,
                    repo_url        TEXT DEFAULT '',
                    last_opened     TEXT,
                    created_at      TEXT NOT NULL
                )
            """)
 
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wake_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger     TEXT NOT NULL,
                    timestamp   TEXT NOT NULL
                )
            """)
 
            conn.commit()
 
        self._seed_defaults()
 
    def _seed_defaults(self):
        defaults = {
            "user_name":         "Sachin",
            "voice_pack":        "professional",
            "voice_speed":       "0.82",
            "voice_model":       "en_US-joe-medium.onnx",
            "llm_model":         "qwen2.5:0.5b",
            "max_history":       "20",
            "silence_threshold": "0.01",
            "silence_duration":  "1.2",
            "theme":             "dark_cyan",
            "wake_words":        json.dumps([
                "jarvis daddy's home",
                "let's get cookin",
                "hey jarvis"
            ]),
        }
        with self._connect() as conn:
            for key, value in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO profile (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, _now())
                )
            conn.commit()
 
 
    # --- preferences ---------------------------------------------------------
 
    def get_preference(self, key, fallback=None):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM profile WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else fallback
 
    def set_preference(self, key, value):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO profile (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, str(value), _now()))
            conn.commit()
 
    def get_all_preferences(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM profile").fetchall()
        return {row["key"]: row["value"] for row in rows}
 
    def get_wake_words(self):
        raw = self.get_preference("wake_words", "[]") or "[]"
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
 
    def set_wake_words(self, words):
        self.set_preference("wake_words", json.dumps(words))
 
 
    # --- conversation history -------------------------------------------------
 
    def save_message(self, role, content, voice_pack="professional"):
        """call this after every exchange so history survives a restart"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (role, content, voice_pack, timestamp) VALUES (?, ?, ?, ?)",
                (role, content, voice_pack, _now())
            )
            conn.commit()
 
    def get_recent_history(self, limit=None):
        """returns the last N messages, oldest first"""
        if limit is None:
            limit = int(self.get_preference("max_history") or 20)
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT role, content, voice_pack, timestamp
                FROM conversations ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in reversed(rows)]
 
    def get_llm_context(self, limit=None):
        """
        returns history in the format mainchat.py expects:
        [{"role": "user", "content": "..."}, ...]
        """
        return [
            {"role": h["role"], "content": h["content"]}
            for h in self.get_recent_history(limit)
        ]
 
    def clear_history(self):
        """wipe conversation log, keeps everything else"""
        with self._connect() as conn:
            conn.execute("DELETE FROM conversations")
            conn.commit()
 
 
    # --- notes ----------------------------------------------------------------
 
    def save_note(self, content, title=None, tags=None):
        """saves a note and returns its id"""
        if not title:
            title = content[:50].strip().replace("\n", " ")
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO notes (title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (title, content, json.dumps(tags or []), now, now)
            )
            conn.commit()
            return cursor.lastrowid
 
    def get_notes(self, limit=20):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, content, tags, created_at FROM notes ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d["tags"])
            result.append(d)
        return result
 
    def delete_note(self, note_id):
        with self._connect() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            conn.commit()
 
 
    # --- projects -------------------------------------------------------------
 
    def upsert_project(self, name, description="", completion_pct=0.0, repo_url=""):
        """add a project or update it if it already exists"""
        now = _now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO projects (name, description, completion_pct, repo_url, last_opened, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description    = excluded.description,
                    completion_pct = excluded.completion_pct,
                    repo_url       = excluded.repo_url,
                    last_opened    = excluded.last_opened
            """, (name, description, completion_pct, repo_url, now, now))
            conn.commit()
 
    def get_projects(self):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, name, description, completion_pct, repo_url, last_opened
                FROM projects ORDER BY last_opened DESC
            """).fetchall()
        return [dict(row) for row in rows]
 
    def update_project_completion(self, name, pct):
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET completion_pct = ? WHERE name = ?",
                (min(100.0, max(0.0, pct)), name)
            )
            conn.commit()
 
    def mark_project_opened(self, name):
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET last_opened = ? WHERE name = ?",
                (_now(), name)
            )
            conn.commit()
 
 
    # --- wake word log --------------------------------------------------------
 
    def log_wake_event(self, trigger):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO wake_events (trigger, timestamp) VALUES (?, ?)",
                (trigger, _now())
            )
            conn.commit()
 
    def get_wake_stats(self):
        """how many times each wake word has fired"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT trigger, COUNT(*) as count
                FROM wake_events GROUP BY trigger ORDER BY count DESC
            """).fetchall()
        return {row["trigger"]: row["count"] for row in rows}
 
 
    # --- boot summary ---------------------------------------------------------
 
    def get_summary(self):
        """
        called on startup to build the welcome message
        e.g. "welcome back Sachin, you have 3 notes and 2 active projects"
        """
        with self._connect() as conn:
            msg_count  = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            proj_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            wake_count = conn.execute("SELECT COUNT(*) FROM wake_events").fetchone()[0]
 
        return {
            "user_name":     self.get_preference("user_name", "User"),
            "voice_pack":    self.get_preference("voice_pack", "professional"),
            "messages":      msg_count,
            "notes":         note_count,
            "projects":      proj_count,
            "wake_triggers": wake_count,
        }
 
 
# --- helpers ------------------------------------------------------------------
 
def _now():
    return datetime.utcnow().isoformat()
 
 
# --- run this file directly to test everything --------------------------------
 
if __name__ == "__main__":
    print("testing jarvis db...\n")
    db = JarvisDB()
 
    print("-- preferences")
    print("user:", db.get_preference("user_name"))
    print("voice pack:", db.get_preference("voice_pack"))
    db.set_preference("voice_pack", "genz")
    print("after update:", db.get_preference("voice_pack"))
    db.set_preference("voice_pack", "professional")
 
    print("\n-- wake words")
    print(db.get_wake_words())
    db.log_wake_event("hey jarvis")
    db.log_wake_event("hey jarvis")
    db.log_wake_event("jarvis daddy's home")
    print("stats:", db.get_wake_stats())
 
    print("\n-- conversation history")
    db.save_message("user", "open my repos")
    db.save_message("assistant", "opening your github repos now.")
    for msg in db.get_llm_context():
        print(f"  [{msg['role']}] {msg['content']}")
 
    print("\n-- notes")
    nid = db.save_note("fix silence detection threshold", tags=["dev", "listen.py"])
    print("saved note id:", nid)
    for note in db.get_notes():
        print(f"  [{note['id']}] {note['title']}  tags={note['tags']}")
 
    print("\n-- projects")
    db.upsert_project("JARVIS", "main assistant", 35.0, "https://github.com/splat1745/Jarvis")
    db.upsert_project("Portfolio", "personal site", 60.0)
    for p in db.get_projects():
        print(f"  {p['name']} — {p['completion_pct']}%")
 
    print("\n-- boot summary")
    print(db.get_summary())
 
    print(f"\ndb file: {DB_PATH}")
 