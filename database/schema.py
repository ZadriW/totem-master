"""Initial DDL and idempotent migrations."""
from __future__ import annotations

import sqlite3
from typing import List

from .connection import _now_iso, get_conn
from .sku_helpers import _default_sku_for_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,
    sku          TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    description  TEXT,
    price        REAL    NOT NULL DEFAULT 0,
    image        TEXT,
    stock        INTEGER NOT NULL DEFAULT 0,
    min_stock    INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE (sku)
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number    TEXT    UNIQUE NOT NULL,
    created_at      TEXT    NOT NULL,
    total           REAL    NOT NULL,
    items_count     INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'confirmado',
    client_name     TEXT,
    client_cpf      TEXT,
    client_zipcode  TEXT,
    client_address  TEXT,
    client_number   TEXT,
    client_complement TEXT,
    client_city     TEXT,
    client_state    TEXT,
    seller_id       INTEGER,
    seller_name     TEXT,
    FOREIGN KEY (seller_id) REFERENCES sellers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS transaction_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    product_id     TEXT,
    product_name   TEXT    NOT NULL,
    category       TEXT,
    unit_price     REAL    NOT NULL,
    quantity       INTEGER NOT NULL,
    subtotal       REAL    NOT NULL,
    product_sku    TEXT,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL,
    movement_type  TEXT    NOT NULL
        CHECK (movement_type IN ('entrada','saida','venda','ajuste','inicial')),
    quantity       INTEGER NOT NULL,
    delta          INTEGER NOT NULL,
    balance_after  INTEGER NOT NULL,
    unit_cost      REAL,
    reason         TEXT,
    reference      TEXT,
    transaction_id INTEGER,
    created_by     TEXT,
    created_at     TEXT    NOT NULL,
    FOREIGN KEY (product_id)     REFERENCES products(id),
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sellers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    email          TEXT    UNIQUE NOT NULL,
    password_hash  TEXT    NOT NULL,
    pin_hash       TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    description  TEXT,
    badge_color  TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS event_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 0,
    min_stock   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (event_id, product_id),
    FOREIGN KEY (event_id)   REFERENCES events(id)   ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category);
CREATE INDEX IF NOT EXISTS idx_stock_movements_product_created
    ON stock_movements(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_movements_created_at
    ON stock_movements(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sellers_email
    ON sellers(email);
CREATE INDEX IF NOT EXISTS idx_event_products_event
    ON event_products(event_id);

CREATE TABLE IF NOT EXISTS promotions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    rule_type   TEXT    NOT NULL
        CHECK (rule_type IN ('percent', 'fixed', 'bogo')),
    rule_value  REAL    NOT NULL DEFAULT 0,
    min_qty     INTEGER NOT NULL DEFAULT 1,
    free_qty    INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS promotion_products (
    promotion_id INTEGER NOT NULL,
    product_id   INTEGER NOT NULL,
    PRIMARY KEY (promotion_id, product_id),
    FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id)   REFERENCES products(id)  ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_promotions_event
    ON promotions(event_id, active);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

def _ensure_products_sku_column(conn: sqlite3.Connection) -> None:
    """Bases antigas: adiciona ``sku``; preenche valores; garante índice único."""
    cols = _table_columns(conn, "products")
    if "sku" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN sku TEXT")
    for row in conn.execute("SELECT id, sku FROM products").fetchall():
        pid = int(row["id"])
        s = (row["sku"] or "").strip() if row["sku"] is not None else ""
        if not s:
            conn.execute(
                "UPDATE products SET sku = ?, updated_at = ? WHERE id = ?",
                (_default_sku_for_id(pid), _now_iso(), pid),
            )
    # Índice único (não conflita com UNIQUE de tabelas novas — idempotente)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sku ON products(sku)")


def _ensure_transaction_items_product_sku_column(conn: sqlite3.Connection) -> None:
    if "product_sku" in _table_columns(conn, "transaction_items"):
        return
    conn.execute("ALTER TABLE transaction_items ADD COLUMN product_sku TEXT")


def _ensure_transactions_payment_method(conn: sqlite3.Connection) -> None:
    if "payment_method" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN payment_method TEXT")


def _ensure_transactions_card_installments(conn: sqlite3.Connection) -> None:
    if "card_installments" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN card_installments INTEGER")


def _ensure_transactions_aut(conn: sqlite3.Connection) -> None:
    if "aut" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN aut TEXT")


def _ensure_transactions_event_id(conn: sqlite3.Connection) -> None:
    """Adiciona event_id em transactions para rastrear vendas de eventos."""
    if "event_id" in _table_columns(conn, "transactions"):
        return
    conn.execute("ALTER TABLE transactions ADD COLUMN event_id INTEGER")


def _ensure_transactions_client_columns(conn: sqlite3.Connection) -> None:
    """Adiciona colunas de dados do cliente/vendedor em transactions."""
    cols = _table_columns(conn, "transactions")
    client_fields = [
        "client_name", "client_cpf", "client_zipcode", "client_address",
        "client_number", "client_complement", "client_city", "client_state"
    ]
    for field in client_fields:
        if field not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {field} TEXT")
    if "seller_id" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN seller_id INTEGER")
    if "seller_name" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN seller_name TEXT")


def _ensure_transactions_cro_columns(conn: sqlite3.Connection) -> None:
    """Garante colunas de UF/número CRO e campos legados (categoria/validação) em transactions."""
    cols = _table_columns(conn, "transactions")
    cro_fields = {
        "client_cro_uf": "TEXT",
        "client_cro_numero": "TEXT",
        "client_cro_categoria": "TEXT",
        "client_cro_validated": "INTEGER DEFAULT 0",
        "client_cro_validation_data": "TEXT",
    }
    for field, ddl in cro_fields.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {field} {ddl}")


def _ensure_events_tables(conn: sqlite3.Connection) -> None:
    """Cria tabelas de eventos caso a base seja anterior à sua introdução."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            description  TEXT,
            badge_color  TEXT,
            active       INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            stock       INTEGER NOT NULL DEFAULT 0,
            min_stock   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            UNIQUE (event_id, product_id),
            FOREIGN KEY (event_id)   REFERENCES events(id)   ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_event_products_event
            ON event_products(event_id);
    """)


def _ensure_events_badge_color(conn: sqlite3.Connection) -> None:
    """Acrescenta ``badge_color`` (hex) em ``events`` em bases antigas."""
    if "badge_color" in _table_columns(conn, "events"):
        return
    conn.execute("ALTER TABLE events ADD COLUMN badge_color TEXT")


def _ensure_event_extensions(conn: sqlite3.Connection) -> None:
    """Adiciona event_id a stock_movements e cria event_sellers (idempotente)."""
    # Adicionar event_id a stock_movements se ainda não existir
    sm_cols = _table_columns(conn, "stock_movements")
    if "event_id" not in sm_cols:
        conn.execute(
            "ALTER TABLE stock_movements ADD COLUMN event_id INTEGER "
            "REFERENCES events(id) ON DELETE SET NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_movements_event "
        "ON stock_movements(event_id)"
    )
    # Tabela de associação evento ↔ vendedor
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS event_sellers (
            event_id   INTEGER NOT NULL,
            seller_id  INTEGER NOT NULL,
            added_at   TEXT    NOT NULL,
            PRIMARY KEY (event_id, seller_id),
            FOREIGN KEY (event_id)  REFERENCES events(id)  ON DELETE CASCADE,
            FOREIGN KEY (seller_id) REFERENCES sellers(id) ON DELETE CASCADE
        );
    """)
    _ensure_event_sellers_one_event_per_seller(conn)


def _ensure_event_sellers_one_event_per_seller(conn: sqlite3.Connection) -> None:
    """Remove vínculos duplicados por vendedor e garante índice único em ``seller_id``.

    Regra de negócio: cada vendedor pode estar associado a no máximo um evento.
    Em caso de histórico inconsistente, mantém o vínculo mais recente (``added_at``).
    """
    dup_rows = conn.execute(
        """
        SELECT seller_id FROM event_sellers GROUP BY seller_id HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in dup_rows:
        sid = int(row["seller_id"])
        keep = conn.execute(
            """
            SELECT rowid FROM event_sellers
             WHERE seller_id = ?
             ORDER BY datetime(added_at) DESC, event_id DESC
             LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if not keep:
            continue
        conn.execute(
            "DELETE FROM event_sellers WHERE seller_id = ? AND rowid != ?",
            (sid, int(keep["rowid"])),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_event_sellers_seller_unique "
        "ON event_sellers(seller_id)"
    )


def _ensure_transaction_items_promo_columns(conn: sqlite3.Connection) -> None:
    """Adiciona colunas de auditoria de promoção em transaction_items (idempotente)."""
    cols = _table_columns(conn, "transaction_items")
    if "original_price" not in cols:
        conn.execute("ALTER TABLE transaction_items ADD COLUMN original_price REAL")
    if "promotion_id" not in cols:
        conn.execute("ALTER TABLE transaction_items ADD COLUMN promotion_id INTEGER")


def _ensure_sellers_columns(conn: sqlite3.Connection) -> None:
    """Migrações leves para contas de vendedores."""
    cols = _table_columns(conn, "sellers")
    for field, ddl in {
        "name": "TEXT NOT NULL DEFAULT 'Vendedor'",
        "email": "TEXT",
        "password_hash": "TEXT",
        "pin_hash": "TEXT",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        "last_login_at": "TEXT",
    }.items():
        if field not in cols:
            conn.execute(f"ALTER TABLE sellers ADD COLUMN {field} {ddl}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sellers_email ON sellers(email)")


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Inicialização + seed
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Cria as tabelas, aplica migrações leves e remove resíduos do seed antigo."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _ensure_products_sku_column(conn)
        _ensure_transaction_items_product_sku_column(conn)
        _ensure_transactions_client_columns(conn)
        _ensure_transactions_cro_columns(conn)
        _ensure_transactions_payment_method(conn)
        _ensure_transactions_card_installments(conn)
        _ensure_transactions_aut(conn)
        _ensure_transactions_event_id(conn)
        _ensure_sellers_columns(conn)
        _ensure_events_tables(conn)
        _ensure_events_badge_color(conn)
        _ensure_event_extensions(conn)
        _ensure_transaction_items_promo_columns(conn)
        _purge_invalid_product_ids(conn)
        _purge_legacy_demo_products(conn)


def _purge_invalid_product_ids(conn: sqlite3.Connection) -> None:
    """Remove cadastros com ``id`` não positivo (resíduos de integrações Wake)."""
    rows = conn.execute("SELECT id FROM products WHERE id < 1").fetchall()
    if not rows:
        return
    ids = [int(r["id"]) for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM stock_movements WHERE product_id IN ({placeholders})",
        ids,
    )
    conn.execute(
        f"DELETE FROM products WHERE id IN ({placeholders})",
        ids,
    )


def _purge_legacy_demo_products(conn: sqlite3.Connection) -> None:
    """Remove produtos do catálogo fictício inicial (imagens picsum.photos).

    O catálogo passou a vir apenas da Wake Commerce; estes registros eram
    identificáveis pela URL de placeholder usada no seed antigo.
    """
    rows = conn.execute(
        "SELECT id FROM products WHERE image LIKE ?",
        ("%picsum.photos%",),
    ).fetchall()
    if not rows:
        return
    ids = [int(r["id"]) for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM stock_movements WHERE product_id IN ({placeholders})",
        ids,
    )
    conn.execute(
        f"DELETE FROM products WHERE id IN ({placeholders})",
        ids,
    )
