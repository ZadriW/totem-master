"""Caminhos do arquivo SQLite e fábrica de conexão (único lugar para PRAGMA e commit)."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(_ROOT_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "totem.sqlite3")


def _ensure_dir() -> None:
    os.makedirs(DB_DIR, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
