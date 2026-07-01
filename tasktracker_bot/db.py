"""
Task Tracker - Database
"""
import sqlite3
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum
import tarfile
import time
import glob

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from config import DATABASE_PATH


class TaskStatus(Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Task:
    id: int
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    created_at: datetime
    updated_at: datetime
    created_by: str
    raw_message: str
    tags: List[str] = None


def _backup_database():
    backups_dir = os.path.join(os.path.dirname(DATABASE_PATH), "backups")
    os.makedirs(backups_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(backups_dir, f"tasks_db_{ts}.tar.gz")

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(DATABASE_PATH, arcname=os.path.basename(DATABASE_PATH))

    _apply_backup_retention(backups_dir)


def _apply_backup_retention(backups_dir: str):
    pattern = os.path.join(backups_dir, "tasks_db_*.tar.gz")
    files = [p for p in glob.glob(pattern) if os.path.isfile(p)]
    if not files:
        return

    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    newest = files[0]
    cutoff = time.time() - 7 * 24 * 60 * 60

    for path in files[1:]:
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except FileNotFoundError:
            pass

    try:
        os.utime(newest, None)
    except FileNotFoundError:
        pass


def init_db():
    """Initialize database."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'new',
            priority TEXT DEFAULT 'medium',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            raw_message TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_tags (
            task_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (task_id, tag_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def create_task(title: str, description: str, priority: str, created_by: str, raw_message: str) -> int:
    """Create new task."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tasks (title, description, status, priority, created_by, raw_message, created_at, updated_at)
        VALUES (?, ?, 'new', ?, ?, ?, datetime('now'), datetime('now'))
    """, (title, description, priority, created_by, raw_message))
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    _backup_database()
    return task_id


def get_all_tasks(status: Optional[str] = None, tag_filter: Optional[List[int]] = None) -> List[Task]:
    """Get all tasks, optionally filtered by status and/or tags."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT t.id, t.title, t.description, t.status, t.priority,
               t.created_at, t.updated_at, t.created_by, t.raw_message
        FROM tasks t
    """

    params = []
    where_clauses = []

    if tag_filter:
        query += " INNER JOIN task_tags tt ON t.id = tt.task_id"
        placeholders = ','.join(['?'] * len(tag_filter))
        where_clauses.append(f"tt.tag_id IN ({placeholders})")
        params.extend(tag_filter)

    if status:
        where_clauses.append("t.status = ?")
        params.append(status)

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY t.created_at DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    all_tags = get_all_tags_dict(conn)

    tasks = []
    for row in rows:
        task = Task(
            id=row[0],
            title=row[1],
            description=row[2],
            status=TaskStatus(row[3]),
            priority=TaskPriority(row[4]),
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
            created_by=row[7],
            raw_message=row[8],
            tags=all_tags.get(row[0], [])
        )
        tasks.append(task)

    conn.close()
    return tasks


def get_task_by_id(task_id: int) -> Optional[Task]:
    """Get single task by ID."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, description, status, priority, created_at, updated_at, created_by, raw_message
        FROM tasks WHERE id = ?
    """, (task_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return None

    tags = get_task_tags(conn, task_id)

    task = Task(
        id=row[0],
        title=row[1],
        description=row[2],
        status=TaskStatus(row[3]),
        priority=TaskPriority(row[4]),
        created_at=datetime.fromisoformat(row[5]),
        updated_at=datetime.fromisoformat(row[6]),
        created_by=row[7],
        raw_message=row[8],
        tags=tags
    )
    conn.close()
    return task


def get_all_tags_dict(conn=None) -> dict:
    """Get all tags as dict {task_id: [tag_names]}."""
    should_close = False
    if conn is None:
        conn = sqlite3.connect(DATABASE_PATH)
        should_close = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT tt.task_id, tg.name
        FROM task_tags tt
        JOIN tags tg ON tt.tag_id = tg.id
    """)
    rows = cursor.fetchall()

    result = {}
    for task_id, tag_name in rows:
        if task_id not in result:
            result[task_id] = []
        result[task_id].append(tag_name)

    if should_close:
        conn.close()

    return result


def get_task_tags(conn, task_id) -> List[str]:
    """Get tags for a task."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tg.name
        FROM task_tags tt
        JOIN tags tg ON tt.tag_id = tg.id
        WHERE tt.task_id = ?
    """, (task_id,))
    return [row[0] for row in cursor.fetchall()]


def get_all_tags() -> List[str]:
    """Get all tags."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM tags ORDER BY name")
    tags = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tags


def add_tag(task_id: int, tag_name: str):
    """Add tag to task."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
    cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
    tag_id = cursor.fetchone()[0]

    cursor.execute("INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)", (task_id, tag_id))
    conn.commit()
    conn.close()
    _backup_database()


def remove_tag(task_id: int, tag_name: str):
    """Remove tag from task."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM task_tags
        WHERE task_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)
    """, (task_id, tag_name))

    cursor.execute("""
        DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM task_tags)
    """)

    conn.commit()
    conn.close()
    _backup_database()


def set_task_tags(task_id: int, tag_names: List[str]):
    """Set tags for a task (replace all)."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))

    for tag_name in tag_names:
        if tag_name.strip():
            cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name.strip(),))
            cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name.strip(),))
            tag_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO task_tags (task_id, tag_id) VALUES (?, ?)", (task_id, tag_id))

    cursor.execute("""
        DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM task_tags)
    """)

    conn.commit()
    conn.close()
    _backup_database()


def update_task_status(task_id: int, status: str):
    """Update task status."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?
    """, (status, task_id))
    conn.commit()
    conn.close()
    _backup_database()


def update_task(task_id: int, title: str, description: str):
    """Update task title and description."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE tasks SET title = ?, description = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (title, description, task_id))
    conn.commit()
    conn.close()
    _backup_database()


def delete_task(task_id: int):
    """Delete task."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    _backup_database()


def bulk_update_status(task_ids: List[int], status: str):
    """Update status for multiple tasks."""
    if not task_ids:
        return
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    placeholders = ','.join(['?'] * len(task_ids))
    cursor.execute(f"""
        UPDATE tasks SET status = ?, updated_at = datetime('now')
        WHERE id IN ({placeholders})
    """, [status] + task_ids)
    conn.commit()
    conn.close()
    _backup_database()


def bulk_delete(task_ids: List[int]):
    """Delete multiple tasks."""
    if not task_ids:
        return
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    placeholders = ','.join(['?'] * len(task_ids))

    cursor.execute(f"DELETE FROM task_tags WHERE task_id IN ({placeholders})", task_ids)
    cursor.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)

    cursor.execute("""
        DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM task_tags)
    """)

    conn.commit()
    conn.close()
    _backup_database()


def cleanup_unused_tags():
    """Remove tags that are not associated with any task."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM task_tags)
    """)
    conn.commit()
    conn.close()
    _backup_database()


if __name__ == "__main__":
    init_db()
    print("Database initialized!")
