"""Camada de persistência do totem.

Usa SQLite (stdlib) para armazenar vendas realizadas no totem. O objetivo
é manter uma base analítica local — simples, confiável e sem dependências
extras — para alimentar o painel administrativo.

Esquema:

- ``transactions`` — uma linha por venda confirmada (após o cliente tocar
  em "Pagamento realizado"). Guarda total, quantidade de itens, status
  e horário.

- ``transaction_items`` — uma linha por produto da venda, com snapshot
  do preço e nome no momento da compra (para que mudanças futuras no
  catálogo não alterem o histórico).

O arquivo do banco fica em ``database/totem.sqlite3`` na raiz do projeto.
"""

from __future__ import annotations

import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterable, List, Optional


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "totem.sqlite3")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT UNIQUE NOT NULL,
    created_at  TEXT    NOT NULL,
    total       REAL    NOT NULL,
    items_count INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'confirmado'
);

CREATE TABLE IF NOT EXISTS transaction_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    product_id     TEXT,
    product_name   TEXT    NOT NULL,
    category       TEXT,
    unit_price     REAL    NOT NULL,
    quantity       INTEGER NOT NULL,
    subtotal       REAL    NOT NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at DESC);
"""


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


def init_db() -> None:
    """Cria as tabelas caso ainda não existam."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Helpers de negócio
# ---------------------------------------------------------------------------

def generate_order_number() -> str:
    """Gera um identificador legível, único por data + aleatório.

    Formato: ``OMyymmdd-####``.
    """
    now = datetime.now()
    prefix = f"OM{now.strftime('%y%m%d')}"
    for _ in range(10):
        number = f"{prefix}-{random.randint(1000, 9999)}"
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM transactions WHERE order_number = ?",
                (number,),
            ).fetchone()
        if row is None:
            return number
    # fallback extremamente improvável
    return f"{prefix}-{int(datetime.now().timestamp())}"


def create_transaction(items: Iterable[Dict]) -> Dict:
    """Registra uma venda e seus itens.

    ``items`` deve conter dicionários no formato:
        ``{ id, nome, categoria, preco, quantidade }``.
    Campos ausentes são tratados com valores seguros.

    Retorna um dict com ``id``, ``order_number``, ``total`` e
    ``items_count``.
    """
    normalized: List[Dict] = []
    for raw in items or []:
        try:
            qty = int(raw.get("quantidade", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        try:
            price = float(raw.get("preco", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        if qty <= 0:
            continue
        name = str(raw.get("nome") or "Produto sem nome")
        normalized.append(
            {
                "product_id": str(raw.get("id")) if raw.get("id") is not None else None,
                "product_name": name,
                "category": raw.get("categoria"),
                "unit_price": price,
                "quantity": qty,
                "subtotal": round(price * qty, 2),
            }
        )

    if not normalized:
        raise ValueError("Nenhum item válido na transação.")

    total = round(sum(i["subtotal"] for i in normalized), 2)
    items_count = sum(i["quantity"] for i in normalized)
    order_number = generate_order_number()
    created_at = datetime.now().isoformat(timespec="seconds")

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions
                (order_number, created_at, total, items_count, status)
            VALUES (?, ?, ?, ?, 'confirmado')
            """,
            (order_number, created_at, total, items_count),
        )
        tx_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO transaction_items
                (transaction_id, product_id, product_name, category,
                 unit_price, quantity, subtotal)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    tx_id,
                    i["product_id"],
                    i["product_name"],
                    i["category"],
                    i["unit_price"],
                    i["quantity"],
                    i["subtotal"],
                )
                for i in normalized
            ],
        )

    return {
        "id": tx_id,
        "order_number": order_number,
        "total": total,
        "items_count": items_count,
        "created_at": created_at,
    }


def _items_for(conn: sqlite3.Connection, tx_id: int) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT id, product_id, product_name, category,
               unit_price, quantity, subtotal
          FROM transaction_items
         WHERE transaction_id = ?
         ORDER BY id
        """,
        (tx_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_transactions(limit: int = 200) -> List[Dict]:
    """Retorna as transações mais recentes com seus itens agrupados."""
    with get_conn() as conn:
        tx_rows = conn.execute(
            """
            SELECT id, order_number, created_at, total, items_count, status
              FROM transactions
             ORDER BY datetime(created_at) DESC, id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        results: List[Dict] = []
        for tx in tx_rows:
            tx_dict = dict(tx)
            tx_dict["items"] = _items_for(conn, tx["id"])
            results.append(tx_dict)
        return results


def get_stats() -> Dict:
    """Total de vendas e montante arrecadado (apenas transações confirmadas)."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)              AS transactions_count,
                COALESCE(SUM(total), 0)       AS total_revenue,
                COALESCE(SUM(items_count), 0) AS items_sold
              FROM transactions
             WHERE status = 'confirmado'
            """
        ).fetchone()
        today = conn.execute(
            """
            SELECT
                COUNT(*)              AS transactions_today,
                COALESCE(SUM(total), 0)       AS revenue_today
              FROM transactions
             WHERE status = 'confirmado'
               AND date(created_at) = date('now','localtime')
            """
        ).fetchone()

    return {
        "transactions_count": int(row["transactions_count"] or 0),
        "total_revenue": float(row["total_revenue"] or 0.0),
        "items_sold": int(row["items_sold"] or 0),
        "transactions_today": int(today["transactions_today"] or 0),
        "revenue_today": float(today["revenue_today"] or 0.0),
    }


def get_transaction(tx_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["items"] = _items_for(conn, tx_id)
        return data
