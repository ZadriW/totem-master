"""SKU helpers shared by Wake sync, migrations and catalog."""
from __future__ import annotations

import sqlite3

def _default_sku_for_id(product_id: int) -> str:
    """SKU de fallback quando o cadastro não possui código (formato ``OM-`` + id)."""
    pid = int(product_id)
    if pid <= 0:
        return f"OM-invalid-{abs(pid)}"
    return f"OM-{pid:05d}"


def _is_placeholder_product_name(name: str) -> bool:
    n = (name or "").strip()
    return not n or n == "Produto"


def _is_generated_fallback_sku(sku: str, product_id: int) -> bool:
    return (sku or "").strip() == _default_sku_for_id(int(product_id))


def _ensure_distinct_sku(conn: sqlite3.Connection, pid: int, sku: str) -> str:
    """Garante ``sku`` único na tabela (a Wake pode repetir SKU entre produtos)."""
    base = (sku or "").strip() or _default_sku_for_id(pid)
    clash = conn.execute(
        "SELECT id FROM products WHERE sku = ? AND id != ?",
        (base, pid),
    ).fetchone()
    if not clash:
        return base
    return _default_sku_for_id(pid)

