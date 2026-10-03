"""Избранные модели — SQLite в каталоге плагина.

Отдельный файл, а не память процесса: избранное должно переживать рестарт gateway
и оставаться общим для всех профилей, которые читают один и тот же плагин.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import List

_LOCK = threading.Lock()


def _db_path() -> Path:
    import os
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    data_dir = home / "hum"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "favorites.db"


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), timeout=15)
    con.execute(
        """CREATE TABLE IF NOT EXISTS favorites (
               model_id TEXT PRIMARY KEY,
               added_at  REAL NOT NULL
           )"""
    )
    return con


def list_favorites() -> List[str]:
    """Список избранных id, новые сверху."""
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT model_id FROM favorites ORDER BY added_at DESC").fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()


def is_favorite(model_id: str) -> bool:
    with _LOCK:
        con = _connect()
        try:
            return con.execute(
                "SELECT 1 FROM favorites WHERE model_id = ?", (model_id,)
            ).fetchone() is not None
        finally:
            con.close()


def toggle(model_id: str) -> bool:
    """Переключить избранное. Возвращает новое состояние (True — в избранном)."""
    if not model_id:
        return False
    with _LOCK:
        con = _connect()
        try:
            exists = con.execute(
                "SELECT 1 FROM favorites WHERE model_id = ?", (model_id,)
            ).fetchone()
            if exists:
                con.execute("DELETE FROM favorites WHERE model_id = ?", (model_id,))
                return False
            con.execute(
                "INSERT INTO favorites (model_id, added_at) VALUES (?, ?)",
                (model_id, time.time()),
            )
            con.commit()
            return True
        finally:
            con.close()