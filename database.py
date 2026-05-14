"""Camada de persistência do totem.

Usa SQLite (stdlib) para armazenar **produtos**, **movimentações de estoque**
e **vendas** realizadas no totem. O catálogo é sincronizado a partir da
**Wake Commerce**; o estoque operacional é gerido no painel administrativo.

Esquema
-------

- ``products`` — catálogo. Inclui **sku** (código interno, único), preço,
  categoria, imagem, estoque atual (``stock``) e estoque mínimo (``min_stock``).
  ``active = 0`` oculta o produto do cliente sem apagar o histórico.

- ``stock_movements`` — toda variação de estoque (entrada, saída manual,
  venda automática, ajuste, estoque inicial). Guarda ``quantity`` (módulo),
  ``delta`` (sinalizado) e ``balance_after`` (saldo após a movimentação),
  de modo que o histórico sobreviva a qualquer recálculo futuro.

- ``transactions`` — uma linha por venda confirmada no totem.
- ``transaction_items`` — itens com *snapshot* de nome/preço/categoria
  (e **product_sku** quando disponível).
- ``sellers`` — credenciais dos vendedores que acessam o painel somente
  leitura de apoio aos totens.

Invariante: toda alteração de ``products.stock`` é feita na mesma conexão
que insere a ``stock_movements`` correspondente, garantindo consistência.
"""

from __future__ import annotations

import math
import os
import random
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "totem.sqlite3")


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
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


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


_HEX_BADGE_6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX_BADGE_3 = re.compile(r"^#[0-9a-fA-F]{3}$")


def normalize_event_badge_color(raw: Optional[str]) -> Optional[str]:
    """Aceita ``#RGB`` ou ``#RRGGBB``. Retorna ``None`` para usar o estilo padrão do tema."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if _HEX_BADGE_6.match(s):
        return s.lower()
    m = _HEX_BADGE_3.match(s)
    if m:
        h = m.group(0)[1:]
        return f"#{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}".lower()
    return None


def event_badge_fg_hex(bg_hex: str) -> str:
    """Cor de texto legível sobre ``bg_hex`` (#RRGGBB)."""
    h = (normalize_event_badge_color(bg_hex) or "#0e167a").lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def lin(x: float) -> float:
        c = x / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#0f172a" if lum > 0.55 else "#ffffff"


def event_badge_style_pairs(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Retorna (background_hex, foreground_hex) ou (None, None) para badge sem cor personalizada."""
    bg = normalize_event_badge_color(raw)
    if not bg:
        return None, None
    return bg, event_badge_fg_hex(bg)


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


# ---------------------------------------------------------------------------
# Sincronização com Wake Commerce
# ---------------------------------------------------------------------------

def sync_products_from_wake(products: Iterable[Dict]) -> Dict[str, int]:
    """Sincroniza a biblioteca local de produtos com a Wake Commerce.

    Faz *upsert* por ``id`` (productId da Wake):
    - Produto novo → insere com estoque ``0`` para o admin configurar.
    - Produto existente → atualiza dados de catálogo (nome, categoria, preço,
      imagem e sku), preservando estoque, estoque mínimo e status ativo locais.
    - Produtos locais que **não** vieram da Wake permanecem intactos.

    A Wake é tratada como biblioteca de produtos; o estoque operacional do
    totem é sempre gerido pelo painel administrativo.

    Retorna contadores ``{"inserted": N, "updated": N, "skipped": N}``.
    """
    inserted = updated = skipped = 0
    now = _now_iso()

    with get_conn() as conn:
        for p in products:
            pid = int(p["id"])
            if pid <= 0:
                skipped += 1
                continue

            raw_sku_wake = (p.get("sku") or "").strip()
            nome_wake = str(p.get("nome") or "").strip()
            name = nome_wake if nome_wake else "Produto"
            category = str(p.get("categoria") or "Geral")
            price = float(p.get("preco") or 0)
            image = p.get("imagem") or ""

            existing = conn.execute(
                "SELECT id, name, sku FROM products WHERE id = ?", (pid,)
            ).fetchone()

            if existing is None:
                sku = raw_sku_wake or _default_sku_for_id(pid)
            else:
                ex_name = (existing["name"] or "").strip()
                ex_sku = (existing["sku"] or "").strip()
                if _is_placeholder_product_name(name) and not _is_placeholder_product_name(
                    ex_name
                ):
                    name = ex_name
                if raw_sku_wake:
                    sku = raw_sku_wake
                elif ex_sku and not _is_generated_fallback_sku(ex_sku, pid):
                    sku = ex_sku
                else:
                    sku = _default_sku_for_id(pid)

            sku = _ensure_distinct_sku(conn, pid, sku)

            description = f"{name} — {category}"

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO products
                        (id, sku, name, category, description, price, image,
                         stock, min_stock, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (pid, sku, name, category, description, price, image,
                     0, 5, 1, now, now),
                )
                inserted += 1
            else:
                conn.execute(
                    """
                    UPDATE products
                       SET sku = ?, name = ?, category = ?, description = ?,
                           price = ?, image = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (sku, name, category, description, price, image,
                     now, pid),
                )
                updated += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Catálogo (produtos)
# ---------------------------------------------------------------------------

def _product_row_to_client(row: sqlite3.Row) -> Dict:
    """Converte um row em dict com os nomes usados pelo front (pt-BR)."""
    pid = int(row["id"])
    try:
        sku_val = row["sku"]
    except (KeyError, IndexError):
        sku_val = None
    sku = (sku_val or "").strip() if sku_val is not None else ""
    if not sku:
        sku = _default_sku_for_id(pid)
    return {
        "id": pid,
        "sku": sku,
        "nome": row["name"],
        "categoria": row["category"],
        "descricao": row["description"] or "",
        "preco": float(row["price"] or 0),
        "imagem": row["image"],
        "estoque": int(row["stock"] or 0),
        "estoque_minimo": int(row["min_stock"] or 0),
        "ativo": bool(row["active"]),
    }


def list_products_for_client(
    category: Optional[str] = None,
    query: Optional[str] = None,
    include_out_of_stock: bool = True,
    include_inactive: bool = False,
) -> List[Dict]:
    """Produtos para consumo do front do totem/cliente."""
    sql = "SELECT * FROM products WHERE 1=1"
    params: List = []
    if not include_inactive:
        sql += " AND active = 1"
    if not include_out_of_stock:
        sql += " AND stock > 0"
    if category and category.lower() != "todos":
        sql += " AND LOWER(category) = LOWER(?)"
        params.append(category)
    if query:
        like = f"%{query.lower()}%"
        sql += (
            " AND (LOWER(name) LIKE ? OR LOWER(description) LIKE ? "
            "OR LOWER(COALESCE(sku, '')) LIKE ?)"
        )
        params.extend([like, like, like])
    sql += " ORDER BY category, name"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_product_row_to_client(r) for r in rows]


def list_active_product_stocks() -> List[Dict[str, int]]:
    """Id e estoque dos produtos ativos (mesmo conjunto base do catálogo ao cliente)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, stock FROM products WHERE active = 1 ORDER BY id"
        ).fetchall()
    return [
        {"id": int(r["id"]), "estoque": int(r["stock"] or 0)} for r in rows
    ]


def list_products_admin() -> List[Dict]:
    """Todos os produtos para o painel administrativo (inclui inativos)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY category, name"
        ).fetchall()
    out: List[Dict] = []
    for r in rows:
        d = _product_row_to_client(r)
        d.update(
            {
                "abaixo_minimo": d["estoque"] < d["estoque_minimo"],
                "sem_estoque": d["estoque"] <= 0,
            }
        )
        out.append(d)
    return out


def list_distinct_product_categories() -> List[str]:
    """Valores distintos de categoria na biblioteca de produtos (filtro admin)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(category)
              FROM products
             WHERE TRIM(COALESCE(category, '')) != ''
             ORDER BY LOWER(TRIM(category))
            """
        ).fetchall()
    # Índice posicional: compatível com todos os sqlite3.Row / builds onde alias falha.
    return [str(row[0]) for row in rows if row[0] is not None and str(row[0]).strip()]


def _admin_products_library_filter_clause(
    q: Optional[str],
    categoria: str,
    status: str,
) -> Tuple[str, List]:
    """Filtros da biblioteca de produtos (saldos agregados em todos os eventos).

    Com texto em ``q``: nome, descrição, SKU (LIKE) e, se o trecho for só dígitos (opc. ``#``), ID do produto.
    """
    parts: List[str] = ["1=1"]
    params: List = []
    ev = "COALESCE(ev_agg.ev_stock_total, 0)"
    if q:
        qs = q.strip()
        like = f"%{qs.lower()}%"
        or_parts = [
            "LOWER(p.name) LIKE ?",
            "LOWER(COALESCE(p.description, '')) LIKE ?",
            "LOWER(COALESCE(p.sku, '')) LIKE ?",
        ]
        or_params: List = [like, like, like]
        id_part = qs.lstrip("#").strip()
        if id_part.isdigit():
            or_parts.append("p.id = ?")
            or_params.append(int(id_part))
            or_parts.append("INSTR(CAST(p.id AS TEXT), ?) > 0")
            or_params.append(id_part)
        parts.append("(" + " OR ".join(or_parts) + ")")
        params.extend(or_params)
    if categoria and categoria.lower() != "todos":
        parts.append("LOWER(p.category) = LOWER(?)")
        params.append(categoria)
    st = (status or "todos").strip().lower()
    if st == "ok":
        parts.append(
            f"p.active = 1 AND {ev} > 0 AND "
            f"(p.min_stock <= 0 OR {ev} >= p.min_stock)"
        )
    elif st == "baixo":
        parts.append(f"{ev} > 0 AND {ev} < p.min_stock")
    elif st == "sem_estoque":
        parts.append(f"{ev} <= 0")
    elif st == "inativo":
        parts.append("p.active = 0")
    return " AND ".join(parts), params


_EVT_PRODUCTS_JOIN = """
FROM products p
LEFT JOIN (
    SELECT product_id, COALESCE(SUM(stock), 0) AS ev_stock_total
      FROM event_products
     GROUP BY product_id
) ev_agg ON ev_agg.product_id = p.id
"""


def _admin_products_library_row_to_admin_product(row: sqlite3.Row) -> Dict:
    rd = dict(row)
    ev_total = int(rd.pop("stock_events_total") or 0)
    d = _product_row_to_client(rd)  # type: ignore[arg-type]
    d["estoque"] = ev_total
    d["abaixo_minimo"] = d["estoque_minimo"] > 0 and ev_total < d["estoque_minimo"]
    d["sem_estoque"] = ev_total <= 0
    return d


def _row_to_admin_product(row) -> Dict:
    d = _product_row_to_client(row)
    d.update(
        {
            "abaixo_minimo": d["estoque"] < d["estoque_minimo"],
            "sem_estoque": d["estoque"] <= 0,
        }
    )
    return d


def count_products_admin_filtered(
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
) -> int:
    """Conta produtos na biblioteca admin (filtros sobre saldo agregado nos eventos)."""
    where, params = _admin_products_library_filter_clause(q, categoria, status)
    sql = f"SELECT COUNT(*) AS c {_EVT_PRODUCTS_JOIN} WHERE {where}"
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["c"] if row else 0)


def list_products_admin_slice(
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
    *,
    limit: int,
    offset: int,
) -> List[Dict]:
    """Página da biblioteca de produtos com saldo total nos eventos."""
    where, params = _admin_products_library_filter_clause(q, categoria, status)
    sql = (
        f"SELECT p.*, COALESCE(ev_agg.ev_stock_total, 0) AS stock_events_total "
        f"{_EVT_PRODUCTS_JOIN} WHERE {where} "
        "ORDER BY p.category, p.name LIMIT ? OFFSET ?"
    )
    qparams = list(params) + [int(limit), int(max(0, offset))]
    with get_conn() as conn:
        rows = conn.execute(sql, qparams).fetchall()
    return [_admin_products_library_row_to_admin_product(r) for r in rows]


def get_product(product_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
    if not row:
        return None
    d = _product_row_to_client(row)
    d.update(
        {
            "abaixo_minimo": d["estoque"] < d["estoque_minimo"],
            "sem_estoque": d["estoque"] <= 0,
        }
    )
    return d


def get_product_in_event(event_id: int, product_id: int) -> Optional[Dict]:
    """Catálogo + saldos do produto dentro do evento (formato compatível com ``get_product``)."""
    base = get_product(product_id)
    if base is None:
        return None
    with get_conn() as conn:
        ep = conn.execute(
            "SELECT stock, min_stock FROM event_products WHERE event_id = ? AND product_id = ?",
            (int(event_id), int(product_id)),
        ).fetchone()
    if ep is None:
        return None
    est = int(ep["stock"] or 0)
    mn = int(ep["min_stock"] or 0)
    out = dict(base)
    out["estoque"] = est
    out["estoque_minimo"] = mn
    out["abaixo_minimo"] = mn > 0 and est < mn
    out["sem_estoque"] = est <= 0
    return out


def update_product_min_stock(product_id: int, min_stock: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE products SET min_stock = ?, updated_at = ? WHERE id = ?",
            (max(0, int(min_stock)), _now_iso(), int(product_id)),
        )
        return cur.rowcount > 0


def set_product_active(product_id: int, active: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE products SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, _now_iso(), int(product_id)),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Vendedores (autenticação do painel somente leitura)
# ---------------------------------------------------------------------------

def ensure_seller_account(
    name: str,
    email: str,
    password_hash: str,
    pin_hash: Optional[str] = None,
) -> Dict:
    """Cria uma conta de vendedor se o e-mail ainda não existir."""
    normalized_email = (email or "").strip().lower()
    seller_name = (name or "").strip() or "Vendedor"
    if not normalized_email:
        raise ValueError("E-mail do vendedor é obrigatório.")
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sellers WHERE LOWER(email) = LOWER(?)",
            (normalized_email,),
        ).fetchone()
        if row:
            if pin_hash and not row["pin_hash"]:
                conn.execute(
                    "UPDATE sellers SET pin_hash = ?, updated_at = ? WHERE id = ?",
                    (pin_hash, _now_iso(), int(row["id"])),
                )
                row = conn.execute(
                    "SELECT * FROM sellers WHERE id = ?",
                    (int(row["id"]),),
                ).fetchone()
            return dict(row)
        cur = conn.execute(
            """
            INSERT INTO sellers
                (name, email, password_hash, pin_hash, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (seller_name, normalized_email, password_hash, pin_hash, now, now),
        )
        created = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(created)


def create_seller_account(
    name: str,
    email: str,
    password_hash: str,
    pin_hash: Optional[str] = None,
) -> Dict:
    """Cria uma conta de vendedor, falhando se o e-mail já estiver em uso.

    ``pin_hash`` é opcional (PIN de venda não é mais usado no fluxo atual).
    """
    normalized_email = (email or "").strip().lower()
    seller_name = (name or "").strip()
    if not seller_name:
        raise ValueError("Nome do vendedor é obrigatório.")
    if not normalized_email:
        raise ValueError("E-mail do vendedor é obrigatório.")
    if "@" not in normalized_email:
        raise ValueError("Informe um e-mail válido.")
    if not (password_hash or "").strip():
        raise ValueError("Senha do vendedor é obrigatória.")
    ph = (pin_hash or "").strip() or None
    now = _now_iso()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sellers WHERE LOWER(email) = LOWER(?)",
            (normalized_email,),
        ).fetchone()
        if exists:
            raise ValueError("Já existe um vendedor com este e-mail.")
        cur = conn.execute(
            """
            INSERT INTO sellers
                (name, email, password_hash, pin_hash, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (seller_name, normalized_email, password_hash, ph, now, now),
        )
        row = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def list_sellers() -> List[Dict]:
    """Lista vendedores com métricas agregadas de vendas e eventos associados."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                s.*,
                COALESCE(COUNT(t.id), 0) AS transactions_count,
                COALESCE(SUM(t.total), 0) AS total_revenue,
                COALESCE(SUM(t.items_count), 0) AS items_sold,
                MAX(t.created_at) AS last_sale_at
              FROM sellers s
              LEFT JOIN transactions t
                ON t.seller_id = s.id AND t.status = 'confirmado'
             GROUP BY s.id
             ORDER BY s.active DESC, LOWER(s.name), LOWER(s.email)
            """
        ).fetchall()
        sellers = [dict(r) for r in rows]
        if not sellers:
            return sellers
        seller_ids = [int(s["id"]) for s in sellers]
        placeholders = ",".join("?" * len(seller_ids))
        ev_sql = (
            f"""
            SELECT es.seller_id AS seller_id,
                   e.id AS event_id,
                   e.name AS event_name,
                   e.badge_color AS badge_color,
                   e.active AS event_active
              FROM event_sellers es
              JOIN events e ON e.id = es.event_id
             WHERE es.seller_id IN ({placeholders})
             ORDER BY e.active DESC, LOWER(e.name)
            """
        )
        ev_rows = conn.execute(ev_sql, seller_ids).fetchall()
        by_seller: Dict[int, List[Dict]] = {}
        for er in ev_rows:
            d = dict(er)
            sid = int(d["seller_id"])
            bucket = by_seller.setdefault(sid, [])
            bucket.append(
                {
                    "event_id": int(d["event_id"]),
                    "name": d["event_name"],
                    "badge_color": d["badge_color"],
                    "active": bool(d["event_active"]),
                }
            )
        for s in sellers:
            s["assigned_events"] = by_seller.get(int(s["id"]), [])
    return sellers


def list_seller_pin_hashes() -> List[Dict]:
    """Retorna hashes de PIN para validação/autenticação por PIN."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, email, active, pin_hash
              FROM sellers
             WHERE pin_hash IS NOT NULL AND pin_hash <> ''
             ORDER BY id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_seller_by_email(email: str) -> Optional[Dict]:
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sellers WHERE LOWER(email) = LOWER(?)",
            (normalized_email,),
        ).fetchone()
    return dict(row) if row else None


def get_seller(seller_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (int(seller_id),),
        ).fetchone()
    return dict(row) if row else None


def delete_seller(seller_id: int) -> Dict:
    """Remove o cadastro do vendedor. Transações ligadas ficam com ``seller_id`` nulo (FK)."""
    sid = int(seller_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name FROM sellers WHERE id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            raise ValueError("Vendedor não encontrado.")
        name = str(row["name"] or "")
        conn.execute("DELETE FROM sellers WHERE id = ?", (sid,))
    return {"id": sid, "name": name}


def update_seller_last_login(seller_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sellers SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (_now_iso(), _now_iso(), int(seller_id)),
        )


def update_seller_account(
    seller_id: int,
    *,
    name: str,
    email: str,
    active: bool,
    password_hash: Optional[str] = None,
    pin_hash: Optional[str] = None,
    clear_pin_hash: bool = False,
) -> Dict:
    """Atualiza dados principais do vendedor e opcionalmente redefine a senha."""
    normalized_email = (email or "").strip().lower()
    seller_name = (name or "").strip()
    if not seller_name:
        raise ValueError("Nome do vendedor é obrigatório.")
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("Informe um e-mail válido.")
    now = _now_iso()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM sellers WHERE LOWER(email) = LOWER(?) AND id <> ?",
            (normalized_email, int(seller_id)),
        ).fetchone()
        if exists:
            raise ValueError("Já existe outro vendedor com este e-mail.")
        updates = [
            "name = ?",
            "email = ?",
            "active = ?",
        ]
        params: List = [seller_name, normalized_email, 1 if active else 0]
        if password_hash:
            updates.append("password_hash = ?")
            params.append(password_hash)
        if clear_pin_hash:
            updates.append("pin_hash = NULL")
        elif pin_hash:
            updates.append("pin_hash = ?")
            params.append(pin_hash)
        updates.append("updated_at = ?")
        params.extend([now, int(seller_id)])
        conn.execute(
            f"""
            UPDATE sellers
               SET {", ".join(updates)}
             WHERE id = ?
            """,
            params,
        )
        row = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (int(seller_id),),
        ).fetchone()
    if row is None:
        raise ValueError("Vendedor não encontrado.")
    return dict(row)


# ---------------------------------------------------------------------------
# Núcleo de estoque (operação atômica)
# ---------------------------------------------------------------------------

_VALID_TYPES = {"entrada", "saida", "venda", "ajuste", "inicial"}


def _apply_movement(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    movement_type: str,
    delta: int,
    reason: Optional[str] = None,
    unit_cost: Optional[float] = None,
    reference: Optional[str] = None,
    transaction_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> Dict:
    """Aplica uma movimentação e atualiza o saldo do produto.

    - ``delta`` é **sinalizado** (positivo para entrada/ajuste+,
      negativo para saída/venda/ajuste-).
    - Levanta ``ValueError`` se o saldo resultante ficaria negativo.
    - Deve ser chamado dentro de uma conexão já aberta (transação SQLite).
    """
    if movement_type not in _VALID_TYPES:
        raise ValueError(f"Tipo de movimentação inválido: {movement_type}")
    if delta == 0:
        raise ValueError("Movimentação com quantidade zero.")

    row = conn.execute(
        "SELECT id, name, stock FROM products WHERE id = ?", (int(product_id),)
    ).fetchone()
    if row is None:
        raise ValueError(f"Produto {product_id} não encontrado.")

    current = int(row["stock"] or 0)
    new_stock = current + int(delta)
    if new_stock < 0:
        raise ValueError(
            f"Estoque insuficiente para '{row['name']}': "
            f"disponível {current}, necessário {abs(delta)}."
        )

    now = _now_iso()
    conn.execute(
        "UPDATE products SET stock = ?, updated_at = ? WHERE id = ?",
        (new_stock, now, int(product_id)),
    )
    cur = conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, movement_type, quantity, delta, balance_after,
             unit_cost, reason, reference, transaction_id, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(product_id),
            movement_type,
            abs(int(delta)),
            int(delta),
            new_stock,
            float(unit_cost) if unit_cost is not None else None,
            reason,
            reference,
            transaction_id,
            created_by,
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "product_id": int(product_id),
        "movement_type": movement_type,
        "delta": int(delta),
        "balance_after": new_stock,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Entrada, saída e ajuste (API de alto nível do painel admin)
# ---------------------------------------------------------------------------

def register_stock_entry(
    product_id: int,
    quantity: int,
    *,
    unit_cost: Optional[float] = None,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict:
    """Entrada de estoque (compra/reposição)."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da entrada deve ser maior que zero.")
    with get_conn() as conn:
        return _apply_movement(
            conn,
            product_id=product_id,
            movement_type="entrada",
            delta=qty,
            unit_cost=unit_cost,
            reason=reason or None,
            created_by=created_by,
        )


def register_stock_exit(
    product_id: int,
    quantity: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Saída manual (perda, quebra, vencimento, devolução ao fornecedor...)."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da saída deve ser maior que zero.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo da saída.")
    with get_conn() as conn:
        return _apply_movement(
            conn,
            product_id=product_id,
            movement_type="saida",
            delta=-qty,
            reason=reason.strip(),
            created_by=created_by,
        )


def register_stock_adjustment(
    product_id: int,
    new_stock: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Ajusta o estoque para um valor absoluto (conferência/inventário)."""
    target = int(new_stock)
    if target < 0:
        raise ValueError("O estoque final não pode ser negativo.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo do ajuste.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT stock FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if row is None:
            raise ValueError(f"Produto {product_id} não encontrado.")
        delta = target - int(row["stock"] or 0)
        if delta == 0:
            raise ValueError("O estoque informado é igual ao atual.")
        return _apply_movement(
            conn,
            product_id=product_id,
            movement_type="ajuste",
            delta=delta,
            reason=reason.strip(),
            created_by=created_by,
        )


# ---------------------------------------------------------------------------
# Movimentações (leitura para o painel)
# ---------------------------------------------------------------------------

def _normalize_order_reference(value: Optional[str]) -> str:
    """Remove espaços e ``#`` inicial do código de pedido (ex.: ``#OM...``)."""
    s = (value or "").strip()
    if s.startswith("#"):
        s = s[1:].strip()
    return s


def _stock_movements_product_search_sql(product_search: Optional[str]) -> Tuple[str, List]:
    """Trecho ``AND (...)`` + parâmetros para filtrar por nome/descrição/SKU/ID do produto (JOIN ``p`` + ``m``)."""
    ps = (product_search or "").strip()
    if not ps:
        return "", []
    like = f"%{ps.lower()}%"
    or_parts = [
        "LOWER(p.name) LIKE ?",
        "LOWER(COALESCE(p.description, '')) LIKE ?",
        "LOWER(COALESCE(p.sku, '')) LIKE ?",
    ]
    or_params: List = [like, like, like]
    id_part = ps.lstrip("#").strip()
    if id_part.isdigit():
        or_parts.append("m.product_id = ?")
        or_params.append(int(id_part))
        or_parts.append("INSTR(CAST(m.product_id AS TEXT), ?) > 0")
        or_params.append(id_part)
    return " AND (" + " OR ".join(or_parts) + ")", or_params


def _stock_movements_filter_sql(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
) -> Tuple[str, List]:
    """Trecho ``AND ...`` + parâmetros compartilhado por list/count/max das movimentações."""
    sql = ""
    params: List = []
    if product_id is not None:
        sql += " AND m.product_id = ?"
        params.append(int(product_id))
    frag, extra = _stock_movements_product_search_sql(product_search)
    sql += frag
    params.extend(extra)
    if movement_type and movement_type in _VALID_TYPES:
        sql += " AND m.movement_type = ?"
        params.append(movement_type)
    ref_norm = _normalize_order_reference(reference)
    if ref_norm:
        sql += (
            " AND m.reference IS NOT NULL "
            "AND INSTR(LOWER(m.reference), LOWER(?)) > 0"
        )
        params.append(ref_norm)
    if seller_id is not None and int(seller_id) > 0:
        sql += (
            " AND m.movement_type = 'venda' "
            "AND COALESCE(t.seller_id, -1) = ?"
        )
        params.append(int(seller_id))
    if event_id is not None and int(event_id) > 0:
        sql += " AND m.event_id = ?"
        params.append(int(event_id))
    return sql, params


def count_stock_movements(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
) -> int:
    filt_sql, filt_params = _stock_movements_filter_sql(
        product_id=product_id,
        product_search=product_search,
        movement_type=movement_type,
        reference=reference,
        seller_id=seller_id,
        event_id=event_id,
    )
    sql = (
        "SELECT COUNT(*) AS c FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE 1=1" + filt_sql
    )
    with get_conn() as conn:
        row = conn.execute(sql, filt_params).fetchone()
    return int(row["c"] if row else 0)


def max_stock_movement_id_filtered(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
) -> int:
    """Maior ``m.id`` entre movimentações que passam pelos mesmos filtros da listagem."""
    filt_sql, filt_params = _stock_movements_filter_sql(
        product_id=product_id,
        product_search=product_search,
        movement_type=movement_type,
        reference=reference,
        seller_id=seller_id,
        event_id=event_id,
    )
    sql = (
        "SELECT MAX(m.id) AS mx FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE 1=1" + filt_sql
    )
    with get_conn() as conn:
        row = conn.execute(sql, filt_params).fetchone()
    return int(row["mx"] if row and row["mx"] is not None else 0)


def list_stock_movements(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict]:
    """Lista movimentações. ``reference`` filtra pelo código do pedido (vendas no totem).

    ``product_search`` restringe por nome, descrição, SKU ou ID numérico do produto
    (subtexto em texto; para trechos só com dígitos também casa ``product_id``).

    ``seller_id`` (quando > 0): apenas linhas de **venda** (`movement_type = 'venda'`)
    cuja transação tem ``seller_id`` igual ao informado (via JOIN ``transactions``).
    Demais tipos de movimentação ficam de fora da lista enquanto o filtro estiver ativo.

    ``event_id`` (quando > 0): apenas linhas com ``stock_movements.event_id`` igual ao informado.

    ``offset``: deslocamento para paginação (ordenado por data decrescente).
    """
    filt_sql, filt_params = _stock_movements_filter_sql(
        product_id=product_id,
        product_search=product_search,
        movement_type=movement_type,
        reference=reference,
        seller_id=seller_id,
        event_id=event_id,
    )
    sql = (
        "SELECT m.*, p.name AS product_name, p.category AS product_category, "
        "p.sku AS product_sku, "
        "evt.name AS event_name, "
        "evt.badge_color AS event_badge_color, "
        "t.client_name, t.client_cpf, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_city, t.client_state, "
        "t.payment_method, t.card_installments, t.aut, "
        "t.client_cro_uf, t.client_cro_numero "
        "FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN events evt ON evt.id = m.event_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE 1=1" + filt_sql
        + " ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ? OFFSET ?"
    )
    params = filt_params + [int(limit), max(0, int(offset))]

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_stock_stats() -> Dict:
    """Métricas agregadas de catálogo/estoque para o dashboard."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS products_count,
                COALESCE(SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END), 0) AS products_active,
                COALESCE(SUM(stock), 0) AS units_in_stock,
                COALESCE(SUM(stock * price), 0) AS stock_value,
                COALESCE(SUM(CASE WHEN stock < min_stock THEN 1 ELSE 0 END), 0) AS below_min,
                COALESCE(SUM(CASE WHEN stock <= 0 THEN 1 ELSE 0 END), 0) AS out_of_stock
              FROM products
            """
        ).fetchone()
    return {
        "products_count": int(row["products_count"] or 0),
        "products_active": int(row["products_active"] or 0),
        "units_in_stock": int(row["units_in_stock"] or 0),
        "stock_value": float(row["stock_value"] or 0.0),
        "below_min": int(row["below_min"] or 0),
        "out_of_stock": int(row["out_of_stock"] or 0),
    }


def get_product_events_stock_total(product_id: int) -> int:
    """Soma das quantidades deste produto em todos os eventos."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(stock), 0) AS t FROM event_products WHERE product_id = ?",
            (int(product_id),),
        ).fetchone()
    return int(row["t"] if row else 0)


def get_products_library_stats() -> Dict:
    """Catálogo (cadastro) + inventário somado em todos os eventos (para dashboard/listagens)."""
    with get_conn() as conn:
        row_products = conn.execute(
            """
            SELECT
                COUNT(*) AS products_count,
                COALESCE(SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END), 0) AS products_active
              FROM products
            """
        ).fetchone()
        row_agg = conn.execute(
            """
            SELECT
                COALESCE(SUM(ep.stock), 0) AS units_in_stock,
                COALESCE(SUM(ep.stock * pr.price), 0) AS stock_value
              FROM event_products ep
              JOIN products pr ON pr.id = ep.product_id
            """
        ).fetchone()
        row_below = conn.execute(
            f"""
            SELECT COUNT(*) AS c
                  {_EVT_PRODUCTS_JOIN}
             WHERE COALESCE(ev_agg.ev_stock_total, 0) > 0
               AND COALESCE(ev_agg.ev_stock_total, 0) < p.min_stock
            """
        ).fetchone()
        row_out = conn.execute(
            f"""
            SELECT COUNT(*) AS c
                  {_EVT_PRODUCTS_JOIN}
             WHERE COALESCE(ev_agg.ev_stock_total, 0) <= 0
            """
        ).fetchone()
    return {
        "products_count": int(row_products["products_count"] or 0),
        "products_active": int(row_products["products_active"] or 0),
        "units_in_stock": int(row_agg["units_in_stock"] or 0),
        "stock_value": float(row_agg["stock_value"] or 0.0),
        "below_min": int(row_below["c"] if row_below else 0),
        "out_of_stock": int(row_out["c"] if row_out else 0),
    }


# ---------------------------------------------------------------------------
# Transações (vendas)
# ---------------------------------------------------------------------------

def generate_order_number(conn: Optional[sqlite3.Connection] = None) -> str:
    """Formato ``OMyymmdd-####`` (único por data + aleatório)."""
    now = datetime.now()
    prefix = f"OM{now.strftime('%y%m%d')}"

    def _exists(c: sqlite3.Connection, number: str) -> bool:
        return c.execute(
            "SELECT 1 FROM transactions WHERE order_number = ?", (number,)
        ).fetchone() is not None

    if conn is not None:
        for _ in range(10):
            number = f"{prefix}-{random.randint(1000, 9999)}"
            if not _exists(conn, number):
                return number
        return f"{prefix}-{int(datetime.now().timestamp())}"

    with get_conn() as c:
        for _ in range(10):
            number = f"{prefix}-{random.randint(1000, 9999)}"
            if not _exists(c, number):
                return number
    return f"{prefix}-{int(datetime.now().timestamp())}"


_MIN_TOTAL_PARCELAS_REAIS = 120.0
_MIN_PARCELA_REAIS = 120.0
_MAX_PARCELAS_CARTAO = 24


def _max_card_installments_allowed(total: float) -> int:
    """Mesma regra do checkout: total > R$120 e cada parcela > R$120 (estrito)."""
    t = float(total)
    if not math.isfinite(t) or t <= _MIN_TOTAL_PARCELAS_REAIS:
        return 1
    max_k = 1
    for k in range(2, _MAX_PARCELAS_CARTAO + 1):
        if t / k > _MIN_PARCELA_REAIS:
            max_k = k
        else:
            break
    return max_k


def _normalize_card_installments_for_db(
    payment_method: Optional[str],
    total: float,
    raw,
) -> Optional[int]:
    pm = (payment_method or "").strip().lower()
    if pm != "cartao":
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Número de parcelas inválido.") from None
    if n < 1:
        raise ValueError("Número de parcelas inválido.")
    max_allowed = _max_card_installments_allowed(total)
    if n > max_allowed:
        raise ValueError("Número de parcelas inválido para o valor do pedido.")
    return n


def create_transaction(
    items: Iterable[Dict],
    *,
    created_by: str = "totem",
    seller_id: Optional[int] = None,
    seller_name: Optional[str] = None,
    event_id: Optional[int] = None,
    client_name: Optional[str] = None,
    client_cpf: Optional[str] = None,
    client_zipcode: Optional[str] = None,
    client_address: Optional[str] = None,
    client_number: Optional[str] = None,
    client_complement: Optional[str] = None,
    client_city: Optional[str] = None,
    client_state: Optional[str] = None,
    payment_method: Optional[str] = None,
    card_installments: Optional[int] = None,
    client_cro_uf: Optional[str] = None,
    client_cro_numero: Optional[str] = None,
) -> Dict:
    """Registra uma venda, seus itens e **decrementa o estoque atomicamente**.

    Cada item deve conter ``id, nome, categoria, preco, quantidade``; ``sku`` é
    opcional (complementado pelo catálogo quando houver ``id``).
    Se qualquer produto não tiver estoque suficiente, **nada é gravado**.

    Parâmetros opcionais de ``client_*`` guardam dados do cliente na transação.
    ``client_cro_uf`` e ``client_cro_numero``: registro profissional informado no checkout.

    ``event_id``: Se fornecido, verifica/decrementa estoque de ``event_products`` 
    (venda em evento). Se None, usa estoque global de ``products`` (venda sem evento).

    Retorna ``{id, order_number, total, items_count, created_at}``.
    """
    normalized: List[Dict] = []
    for raw in items or []:
        try:
            qty = int(raw.get("quantidade", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        try:
            price = float(raw.get("preco", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0

        pid_raw = raw.get("id")
        try:
            product_id = int(pid_raw) if pid_raw is not None else None
        except (TypeError, ValueError):
            product_id = None

        sku_in = raw.get("sku")
        product_sku: Optional[str] = None
        if sku_in is not None and str(sku_in).strip():
            product_sku = str(sku_in).strip()

        name = str(raw.get("nome") or "Produto sem nome")
        normalized.append(
            {
                "product_id": product_id,
                "product_id_str": str(pid_raw) if pid_raw is not None else None,
                "product_name": name,
                "product_sku": product_sku,
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
    card_installments_store = _normalize_card_installments_for_db(
        payment_method, total, card_installments if card_installments is not None else 1,
    )

    with get_conn() as conn:
        pids = {i["product_id"] for i in normalized if i["product_id"] is not None}
        sku_by_id: Dict[int, str] = {}
        if pids:
            placeholders = ",".join("?" * len(pids))
            for r in conn.execute(
                f"SELECT id, sku FROM products WHERE id IN ({placeholders})",
                list(pids),
            ).fetchall():
                sku_by_id[int(r["id"])] = (r["sku"] or "").strip() or _default_sku_for_id(
                    int(r["id"])
                )
        for i in normalized:
            if i["product_id"] is not None and not (i.get("product_sku") or "").strip():
                i["product_sku"] = sku_by_id.get(i["product_id"])
            elif (i.get("product_sku") or "").strip():
                i["product_sku"] = str(i["product_sku"]).strip()

        # Agrupa quantidade por produto (caso venha duplicado) e checa estoque.
        demand: Dict[int, int] = {}
        for i in normalized:
            if i["product_id"] is None:
                # Itens sem id numérico não afetam estoque (não há vínculo
                # com a tabela products).
                continue
            demand[i["product_id"]] = demand.get(i["product_id"], 0) + i["quantity"]

        # Verifica estoque: se event_id presente, usa event_products; senão usa products
        if event_id is not None:
            # Venda em evento: verifica estoque do evento
            for pid, qty in demand.items():
                ep = conn.execute(
                    """
                    SELECT p.name, ep.stock
                      FROM event_products ep
                      JOIN products p ON p.id = ep.product_id
                     WHERE ep.event_id = ? AND ep.product_id = ?
                    """,
                    (int(event_id), pid),
                ).fetchone()
                if ep is None:
                    raise ValueError(
                        f"Produto {pid} não está disponível neste evento."
                    )
                if int(ep["stock"] or 0) < qty:
                    raise ValueError(
                        f"Estoque insuficiente para '{ep['name']}' no evento: "
                        f"disponível {int(ep['stock'] or 0)}, pedido {qty}."
                    )
        else:
            # Venda sem evento: verifica estoque global
            for pid, qty in demand.items():
                row = conn.execute(
                    "SELECT name, stock FROM products WHERE id = ?", (pid,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Produto {pid} não encontrado no catálogo.")
                if int(row["stock"] or 0) < qty:
                    raise ValueError(
                        f"Estoque insuficiente para '{row['name']}': "
                        f"disponível {int(row['stock'] or 0)}, pedido {qty}."
                    )

        order_number = generate_order_number(conn)
        created_at = _now_iso()

        cur = conn.execute(
            """
            INSERT INTO transactions
                (order_number, created_at, total, items_count, status,
                 client_name, client_cpf, client_zipcode, client_address,
                 client_number, client_complement, client_city, client_state,
                 seller_id, seller_name, payment_method, card_installments,
                 client_cro_uf, client_cro_numero, client_cro_categoria,
                 client_cro_validated, client_cro_validation_data, aut, event_id)
            VALUES (?, ?, ?, ?, 'pendente', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, NULL, ?)
            """,
            (
                order_number, created_at, total, items_count,
                client_name, client_cpf, client_zipcode, client_address,
                client_number, client_complement, client_city, client_state,
                seller_id, seller_name, payment_method, card_installments_store,
                client_cro_uf, client_cro_numero, event_id,
            ),
        )
        tx_id = cur.lastrowid

        conn.executemany(
            """
            INSERT INTO transaction_items
                (transaction_id, product_id, product_name, category,
                 unit_price, quantity, subtotal, product_sku)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    tx_id,
                    i["product_id_str"],
                    i["product_name"],
                    i["category"],
                    i["unit_price"],
                    i["quantity"],
                    i["subtotal"],
                    i.get("product_sku"),
                )
                for i in normalized
            ],
        )

        # Guarda normalized/demand/event_id no contexto de retorno para uso posterior.
        # O estoque só é baixado em confirm_transaction_with_aut().

    return {
        "id": tx_id,
        "order_number": order_number,
        "total": total,
        "items_count": items_count,
        "created_at": created_at,
        "seller_id": seller_id,
        "seller_name": seller_name,
        "payment_method": payment_method,
        "card_installments": card_installments_store,
        "status": "pendente",
        # Metadados internos necessários para confirm_transaction_with_aut().
        "_normalized": normalized,
        "_demand": demand,
        "_event_id": event_id,
        "_created_by": created_by,
    }


def _pending_tx_merge_client_field(
    incoming: Optional[str],
    existing: Optional[str],
) -> Optional[str]:
    """PATCH de pedido pendente: preserva texto já gravado quando o payload omite o campo.

    Evita apagar dados do cliente quando o front envia ``client: {}`` (ex.: ``sessionStorage``
    vazio na tela de AUT retomada ou em outra aba).
    """
    if incoming is None:
        return existing
    stripped = str(incoming).strip()
    if not stripped:
        return existing
    return stripped


def update_pending_transaction(
    tx_id: int,
    *,
    seller_id: int,
    items: Iterable[Dict],
    client_name: Optional[str] = None,
    client_cpf: Optional[str] = None,
    client_zipcode: Optional[str] = None,
    client_address: Optional[str] = None,
    client_number: Optional[str] = None,
    client_complement: Optional[str] = None,
    client_city: Optional[str] = None,
    client_state: Optional[str] = None,
    payment_method: Optional[str] = None,
    card_installments: Optional[int] = None,
    client_cro_uf: Optional[str] = None,
    client_cro_numero: Optional[str] = None,
) -> Dict:
    """Atualiza um pedido **pendente** (itens, totais, cliente e pagamento) sem baixar estoque.

    Mantém ``order_number``, ``created_at``, ``seller_*`` e ``event_id`` da transação original.
    Usado ao retomar o checkout após alterações no carrinho ou na forma de pagamento.

    Campos ``client_*`` e CRO: valores omitidos ou em branco no PATCH **não apagam** dados já
    gravados (merge com a linha atual), para suportar fluxos sem ``sessionStorage`` na tela de AUT.
    """
    normalized: List[Dict] = []
    for raw in items or []:
        try:
            qty = int(raw.get("quantidade", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        try:
            price = float(raw.get("preco", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0

        pid_raw = raw.get("id")
        try:
            product_id = int(pid_raw) if pid_raw is not None else None
        except (TypeError, ValueError):
            product_id = None

        sku_in = raw.get("sku")
        product_sku: Optional[str] = None
        if sku_in is not None and str(sku_in).strip():
            product_sku = str(sku_in).strip()

        name = str(raw.get("nome") or "Produto sem nome")
        normalized.append(
            {
                "product_id": product_id,
                "product_id_str": str(pid_raw) if pid_raw is not None else None,
                "product_name": name,
                "product_sku": product_sku,
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
    card_installments_store = _normalize_card_installments_for_db(
        payment_method,
        total,
        card_installments if card_installments is not None else 1,
    )

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (int(tx_id),)
        ).fetchone()
        if row is None:
            raise ValueError("Transação não encontrada.")
        tx_row = dict(row)
        if str(tx_row.get("status") or "").lower() != "pendente":
            raise ValueError("Somente pedidos pendentes podem ser atualizados.")
        if int(tx_row.get("seller_id") or 0) != int(seller_id):
            raise ValueError("Você não pode alterar esta transação.")

        ev_raw = tx_row.get("event_id")
        try:
            event_id = int(ev_raw) if ev_raw is not None else None
        except (TypeError, ValueError):
            event_id = None
        if event_id is not None and event_id <= 0:
            event_id = None

        pids = {i["product_id"] for i in normalized if i["product_id"] is not None}
        sku_by_id: Dict[int, str] = {}
        if pids:
            placeholders = ",".join("?" * len(pids))
            for r in conn.execute(
                f"SELECT id, sku FROM products WHERE id IN ({placeholders})",
                list(pids),
            ).fetchall():
                sku_by_id[int(r["id"])] = (r["sku"] or "").strip() or _default_sku_for_id(
                    int(r["id"])
                )
        for i in normalized:
            if i["product_id"] is not None and not (i.get("product_sku") or "").strip():
                i["product_sku"] = sku_by_id.get(i["product_id"])
            elif (i.get("product_sku") or "").strip():
                i["product_sku"] = str(i["product_sku"]).strip()

        demand: Dict[int, int] = {}
        for i in normalized:
            if i["product_id"] is None:
                continue
            demand[i["product_id"]] = demand.get(i["product_id"], 0) + i["quantity"]

        if event_id is not None:
            for pid, qty in demand.items():
                ep = conn.execute(
                    """
                    SELECT p.name, ep.stock
                      FROM event_products ep
                      JOIN products p ON p.id = ep.product_id
                     WHERE ep.event_id = ? AND ep.product_id = ?
                    """,
                    (int(event_id), pid),
                ).fetchone()
                if ep is None:
                    raise ValueError(
                        f"Produto {pid} não está disponível neste evento."
                    )
                if int(ep["stock"] or 0) < qty:
                    raise ValueError(
                        f"Estoque insuficiente para '{ep['name']}' no evento: "
                        f"disponível {int(ep['stock'] or 0)}, pedido {qty}."
                    )
        else:
            for pid, qty in demand.items():
                pr = conn.execute(
                    "SELECT name, stock FROM products WHERE id = ?", (pid,)
                ).fetchone()
                if pr is None:
                    raise ValueError(f"Produto {pid} não encontrado no catálogo.")
                if int(pr["stock"] or 0) < qty:
                    raise ValueError(
                        f"Estoque insuficiente para '{pr['name']}': "
                        f"disponível {int(pr['stock'] or 0)}, pedido {qty}."
                    )

        chk = conn.execute(
            """
            SELECT 1 FROM transactions
             WHERE id = ? AND status = 'pendente' AND seller_id = ?
            """,
            (int(tx_id), int(seller_id)),
        ).fetchone()
        if chk is None:
            raise ValueError("Não foi possível atualizar o pedido.")

        merged_name = _pending_tx_merge_client_field(client_name, tx_row.get("client_name"))
        merged_cpf = _pending_tx_merge_client_field(client_cpf, tx_row.get("client_cpf"))
        merged_zip = _pending_tx_merge_client_field(client_zipcode, tx_row.get("client_zipcode"))
        merged_addr = _pending_tx_merge_client_field(client_address, tx_row.get("client_address"))
        merged_num = _pending_tx_merge_client_field(client_number, tx_row.get("client_number"))
        merged_comp = _pending_tx_merge_client_field(client_complement, tx_row.get("client_complement"))
        merged_city = _pending_tx_merge_client_field(client_city, tx_row.get("client_city"))
        merged_state = _pending_tx_merge_client_field(client_state, tx_row.get("client_state"))
        merged_cro_n = _pending_tx_merge_client_field(
            client_cro_numero,
            tx_row.get("client_cro_numero"),
        )
        merged_cro_uf_raw = _pending_tx_merge_client_field(
            client_cro_uf,
            tx_row.get("client_cro_uf"),
        )
        merged_cro_uf = merged_cro_uf_raw.strip().upper() if merged_cro_uf_raw else None

        conn.execute(
            """
            UPDATE transactions
               SET total = ?, items_count = ?,
                   client_name = ?, client_cpf = ?, client_zipcode = ?, client_address = ?,
                   client_number = ?, client_complement = ?, client_city = ?, client_state = ?,
                   payment_method = ?, card_installments = ?,
                   client_cro_uf = ?, client_cro_numero = ?
             WHERE id = ? AND status = 'pendente' AND seller_id = ?
            """,
            (
                total,
                items_count,
                merged_name,
                merged_cpf,
                merged_zip,
                merged_addr,
                merged_num,
                merged_comp,
                merged_city,
                merged_state,
                payment_method,
                card_installments_store,
                merged_cro_uf,
                merged_cro_n,
                int(tx_id),
                int(seller_id),
            ),
        )

        conn.execute(
            "DELETE FROM transaction_items WHERE transaction_id = ?",
            (int(tx_id),),
        )
        conn.executemany(
            """
            INSERT INTO transaction_items
                (transaction_id, product_id, product_name, category,
                 unit_price, quantity, subtotal, product_sku)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(tx_id),
                    i["product_id_str"],
                    i["product_name"],
                    i["category"],
                    i["unit_price"],
                    i["quantity"],
                    i["subtotal"],
                    i.get("product_sku"),
                )
                for i in normalized
            ],
        )

    return {
        "id": int(tx_id),
        "order_number": tx_row["order_number"],
        "total": total,
        "items_count": items_count,
        "created_at": tx_row["created_at"],
        "seller_id": int(seller_id),
        "seller_name": tx_row.get("seller_name"),
        "payment_method": payment_method,
        "card_installments": card_installments_store,
        "status": "pendente",
    }


def _event_id_for_aut_confirmation(
    conn: sqlite3.Connection,
    tx_row: Dict,
    demand: Dict[int, int],
) -> Optional[int]:
    """Define qual saldo usar na confirmação (evento vs catálogo global).

    Usa ``transactions.event_id`` quando válido. Se estiver ausente/nulo mas o
    pedido só contém produtos ligados ao **evento ativo do vendedor**, infere o
    evento — cenário típico de linhas antigas ou falha pontual na persistência,
    que antes faziam o AUT validar ``products.stock`` enquanto o totem exibia
    ``event_products.stock``.
    """
    raw = tx_row.get("event_id")
    try:
        eid = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        eid = None
    if eid is not None and eid <= 0:
        eid = None
    if eid is not None or not demand:
        return eid

    sid_raw = tx_row.get("seller_id")
    if sid_raw is None:
        return None
    try:
        sid = int(sid_raw)
    except (TypeError, ValueError):
        return None

    ev_row = conn.execute(
        """
        SELECT e.id
          FROM event_sellers es
          JOIN events e ON e.id = es.event_id
         WHERE es.seller_id = ? AND e.active = 1
         ORDER BY es.added_at DESC
         LIMIT 1
        """,
        (sid,),
    ).fetchone()
    if ev_row is None:
        return None

    candidate = int(ev_row["id"])
    for pid in demand:
        hit = conn.execute(
            "SELECT 1 FROM event_products WHERE event_id = ? AND product_id = ?",
            (candidate, int(pid)),
        ).fetchone()
        if hit is None:
            return None
    return candidate


def confirm_transaction_with_aut(tx_id: int, aut: str, *, created_by: str = "totem") -> Dict:
    """Confirma uma transação pendente: salva o AUT, baixa o estoque e muda status.

    Deve ser chamada com o ``tx_id`` retornado por ``create_transaction``.
    Levanta ``ValueError`` se a transação não existir, já estiver confirmada/cancelada
    ou o AUT for inválido.
    """
    aut_clean = (aut or "").strip()
    if not aut_clean:
        raise ValueError("O código AUT não pode estar vazio.")

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Transação não encontrada.")
        if row["status"] != "pendente":
            raise ValueError("Esta transação já foi processada e não pode ser alterada.")

        tx_row = dict(row)

        # Reconstrói demand a partir dos itens gravados.
        items_rows = conn.execute(
            "SELECT product_id, quantity FROM transaction_items WHERE transaction_id = ?",
            (tx_id,),
        ).fetchall()
        demand: Dict[int, int] = {}
        for it in items_rows:
            pid_raw = it["product_id"]
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError):
                continue
            demand[pid] = demand.get(pid, 0) + int(it["quantity"] or 0)

        event_id = _event_id_for_aut_confirmation(conn, tx_row, demand)
        if event_id is not None and tx_row.get("event_id") is None:
            conn.execute(
                "UPDATE transactions SET event_id = ? WHERE id = ?",
                (int(event_id), int(tx_id)),
            )

        order_number = row["order_number"]

        # Verifica estoque antes de baixar (pode ter mudado desde o prepare).
        if event_id is not None:
            for pid, qty in demand.items():
                ep = conn.execute(
                    """
                    SELECT p.name, ep.stock
                      FROM event_products ep
                      JOIN products p ON p.id = ep.product_id
                     WHERE ep.event_id = ? AND ep.product_id = ?
                    """,
                    (int(event_id), pid),
                ).fetchone()
                if ep is None:
                    raise ValueError(f"Produto {pid} não está disponível neste evento.")
                if int(ep["stock"] or 0) < qty:
                    raise ValueError(
                        f"Estoque insuficiente para '{ep['name']}' no evento: "
                        f"disponível {int(ep['stock'] or 0)}, pedido {qty}."
                    )
        else:
            for pid, qty in demand.items():
                pr = conn.execute(
                    "SELECT name, stock FROM products WHERE id = ?", (pid,)
                ).fetchone()
                if pr is None:
                    raise ValueError(f"Produto {pid} não encontrado no catálogo.")
                if int(pr["stock"] or 0) < qty:
                    raise ValueError(
                        f"Estoque insuficiente para '{pr['name']}': "
                        f"disponível {int(pr['stock'] or 0)}, pedido {qty}."
                    )

        # Baixa estoque e registra movimentações.
        if event_id is not None:
            for pid, qty in demand.items():
                _apply_event_movement(
                    conn,
                    event_id=int(event_id),
                    product_id=pid,
                    movement_type="venda",
                    delta=-qty,
                    reason="Venda no totem",
                    reference=order_number,
                    transaction_id=tx_id,
                    created_by=created_by,
                )
        else:
            for pid, qty in demand.items():
                _apply_movement(
                    conn,
                    product_id=pid,
                    movement_type="venda",
                    delta=-qty,
                    reason="Venda no totem",
                    reference=order_number,
                    transaction_id=tx_id,
                    created_by=created_by,
                )

        conn.execute(
            "UPDATE transactions SET status = 'confirmado', aut = ? WHERE id = ?",
            (aut_clean, tx_id),
        )

    return {
        "id": tx_id,
        "order_number": order_number,
        "aut": aut_clean,
        "status": "confirmado",
    }


def get_pending_transaction_if_owned(transaction_id: int, seller_id: int) -> Optional[Dict]:
    """Retorna a linha mínima da transação **pendente** se pertencer ao vendedor."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, order_number, status, seller_id
              FROM transactions
             WHERE id = ? AND seller_id = ? AND status = 'pendente'
            """,
            (int(transaction_id), int(seller_id)),
        ).fetchone()
    return dict(row) if row else None


def get_pending_transaction_restore_payload(tx_id: int, seller_id: int) -> Optional[Dict]:
    """Monta carrinho + dados de cliente (sessionStorage) para retomar checkout de um pedido pendente."""
    tx = get_transaction(int(tx_id))
    if not tx:
        return None
    if int(tx.get("seller_id") or 0) != int(seller_id):
        return None
    if str(tx.get("status") or "").lower() != "pendente":
        return None

    event_raw = tx.get("event_id")
    event_id = int(event_raw) if event_raw is not None else None

    cart_items: List[Dict] = []
    with get_conn() as conn:
        for it in tx.get("items") or []:
            pid_raw = it.get("product_id")
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError):
                continue
            pr = conn.execute(
                """
                SELECT id, sku, name, category, price, image, stock, active
                  FROM products
                 WHERE id = ?
                """,
                (pid,),
            ).fetchone()
            if pr is None:
                continue
            qty = int(it.get("quantity") or 0)
            if qty <= 0:
                continue
            imagem = pr["image"] or ""
            if event_id is not None:
                ep = conn.execute(
                    """
                    SELECT stock FROM event_products
                     WHERE event_id = ? AND product_id = ?
                    """,
                    (int(event_id), pid),
                ).fetchone()
                estoque = int(ep["stock"] or 0) if ep else 0
            else:
                estoque = int(pr["stock"] or 0)

            cart_items.append(
                {
                    "id": pid,
                    "sku": (pr["sku"] or "").strip(),
                    "nome": pr["name"],
                    "categoria": pr["category"] or "",
                    "preco": float(pr["price"] or 0),
                    "imagem": imagem,
                    "estoque": estoque,
                    "quantidade": qty,
                }
            )

    pm = (tx.get("payment_method") or "cartao").strip().lower()
    if pm not in ("pix", "cartao"):
        pm = "cartao"
    installments_raw = tx.get("card_installments")
    try:
        installments = max(1, int(installments_raw))
    except (TypeError, ValueError):
        installments = 1

    client_payload = {
        "name": (tx.get("client_name") or "").strip(),
        "cpf": (tx.get("client_cpf") or "").strip(),
        "cro_uf": (tx.get("client_cro_uf") or "").strip(),
        "cro_numero": (tx.get("client_cro_numero") or "").strip(),
        "zipcode": (tx.get("client_zipcode") or "").strip(),
        "address": (tx.get("client_address") or "").strip(),
        "number": (tx.get("client_number") or "").strip(),
        "complement": (tx.get("client_complement") or "").strip(),
        "city": (tx.get("client_city") or "").strip(),
        "state": (tx.get("client_state") or "").strip(),
        "payment_method": pm,
        "installments": installments,
    }

    return {
        "transaction_id": int(tx["id"]),
        "order_number": tx.get("order_number"),
        "cart_items": cart_items,
        "client_data": client_payload,
    }


def cancel_pending_transaction_for_seller(tx_id: int, seller_id: int) -> Dict:
    """Marca uma transação **pendente** como ``cancelado`` (somente o vendedor dono)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, seller_id, status FROM transactions WHERE id = ?",
            (int(tx_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Transação não encontrada.")
        if int(row["seller_id"] or 0) != int(seller_id):
            raise ValueError("Você não pode alterar esta transação.")
        if str(row["status"] or "").lower() != "pendente":
            raise ValueError("Somente pedidos pendentes podem ser descartados.")
        conn.execute(
            "UPDATE transactions SET status = 'cancelado' WHERE id = ?",
            (int(tx_id),),
        )
    return {"id": int(tx_id), "status": "cancelado"}


def _items_for(conn: sqlite3.Connection, tx_id: int) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT id, product_id, product_name, category,
               unit_price, quantity, subtotal, product_sku
          FROM transaction_items
         WHERE transaction_id = ?
         ORDER BY id
        """,
        (tx_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_transactions(limit: int = 200, seller_id: Optional[int] = None) -> List[Dict]:
    """Retorna as transações mais recentes com seus itens agrupados."""
    with get_conn() as conn:
        params: List = []
        where = ""
        if seller_id is not None:
            where = "WHERE seller_id = ?"
            params.append(int(seller_id))
        params.append(int(limit))
        tx_rows = conn.execute(
            f"""
            SELECT id, order_number, created_at, total, items_count, status,
                   seller_id, seller_name, payment_method, card_installments, aut,
                   client_name, client_cpf, client_zipcode, client_address,
                   client_number, client_complement, client_city, client_state,
                   client_cro_uf, client_cro_numero
              FROM transactions
             {where}
             ORDER BY datetime(created_at) DESC, id DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
        results: List[Dict] = []
        for tx in tx_rows:
            tx_dict = dict(tx)
            tx_dict["items"] = _items_for(conn, tx["id"])
            results.append(tx_dict)
        return results


def _transactions_event_filter_sql_params(
    event_id: int,
    *,
    seller_id: Optional[int] = None,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
) -> Tuple[str, List]:
    """Trecho ``WHERE ...`` (sem a palavra-chave) + parâmetros para transações do evento."""
    parts = ["t.event_id = ?"]
    params: List = [int(event_id)]
    if seller_id is not None and int(seller_id) > 0:
        parts.append("COALESCE(t.seller_id, -1) = ?")
        params.append(int(seller_id))
    ref = _normalize_order_reference(order_search)
    if ref:
        parts.append(
            "(t.order_number IS NOT NULL AND INSTR(LOWER(t.order_number), LOWER(?)) > 0)"
        )
        params.append(ref)
    st = (status or "").strip().lower()
    if st and st != "todos" and st in ("confirmado", "pendente", "cancelado"):
        parts.append("LOWER(TRIM(COALESCE(t.status, ''))) = ?")
        params.append(st)
    return " AND ".join(parts), params


def count_transactions_for_event(
    event_id: int,
    *,
    seller_id: Optional[int] = None,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
) -> int:
    """Conta transações ligadas ao ``event_id`` (coluna ``transactions.event_id``)."""
    wh, params = _transactions_event_filter_sql_params(
        event_id,
        seller_id=seller_id,
        order_search=order_search,
        status=status,
    )
    sql = f"SELECT COUNT(*) AS c FROM transactions t WHERE {wh}"
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["c"] if row else 0)


def list_transactions_for_event(
    event_id: int,
    *,
    seller_id: Optional[int] = None,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> List[Dict]:
    """Lista transações do evento com itens (mesmo formato que ``list_transactions``)."""
    wh, params = _transactions_event_filter_sql_params(
        event_id,
        seller_id=seller_id,
        order_search=order_search,
        status=status,
    )
    lim = max(1, int(limit))
    off = max(0, int(offset))
    sql = f"""
        SELECT id, order_number, created_at, total, items_count, status,
               seller_id, seller_name, payment_method, card_installments, aut,
               client_name, client_cpf, client_zipcode, client_address,
               client_number, client_complement, client_city, client_state,
               client_cro_uf, client_cro_numero
          FROM transactions t
         WHERE {wh}
         ORDER BY datetime(t.created_at) DESC, t.id DESC
         LIMIT ? OFFSET ?
    """
    qparams = params + [lim, off]
    with get_conn() as conn:
        tx_rows = conn.execute(sql, qparams).fetchall()
        results: List[Dict] = []
        for tx in tx_rows:
            tx_dict = dict(tx)
            tx_dict["items"] = _items_for(conn, tx["id"])
            results.append(tx_dict)
        return results


def _transactions_seller_scope_filter_sql_params(
    seller_id: int,
    *,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
) -> Tuple[str, List]:
    """Trecho ``WHERE ...`` para transações de um único vendedor (qualquer ``event_id``)."""
    parts = ["t.seller_id = ?"]
    params: List = [int(seller_id)]
    ref = _normalize_order_reference(order_search)
    if ref:
        parts.append(
            "(t.order_number IS NOT NULL AND INSTR(LOWER(t.order_number), LOWER(?)) > 0)"
        )
        params.append(ref)
    st = (status or "").strip().lower()
    if st and st != "todos" and st in ("confirmado", "pendente", "cancelado"):
        parts.append("LOWER(TRIM(COALESCE(t.status, ''))) = ?")
        params.append(st)
    return " AND ".join(parts), params


def count_transactions_for_seller(
    seller_id: int,
    *,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
) -> int:
    """Conta transações em que ``seller_id`` coincide (catálogo global / sem filtro de evento)."""
    wh, params = _transactions_seller_scope_filter_sql_params(
        int(seller_id),
        order_search=order_search,
        status=status,
    )
    sql = f"SELECT COUNT(*) AS c FROM transactions t WHERE {wh}"
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["c"] if row else 0)


def list_transactions_for_seller(
    seller_id: int,
    *,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> List[Dict]:
    """Lista transações do vendedor com itens (mesmo formato que ``list_transactions_for_event``)."""
    wh, params = _transactions_seller_scope_filter_sql_params(
        int(seller_id),
        order_search=order_search,
        status=status,
    )
    lim = max(1, int(limit))
    off = max(0, int(offset))
    sql = f"""
        SELECT id, order_number, created_at, total, items_count, status,
               seller_id, seller_name, payment_method, card_installments, aut,
               client_name, client_cpf, client_zipcode, client_address,
               client_number, client_complement, client_city, client_state,
               client_cro_uf, client_cro_numero
          FROM transactions t
         WHERE {wh}
         ORDER BY datetime(t.created_at) DESC, t.id DESC
         LIMIT ? OFFSET ?
    """
    qparams = params + [lim, off]
    with get_conn() as conn:
        tx_rows = conn.execute(sql, qparams).fetchall()
        results: List[Dict] = []
        for tx in tx_rows:
            tx_dict = dict(tx)
            tx_dict["items"] = _items_for(conn, tx["id"])
            results.append(tx_dict)
        return results


def get_stats(seller_id: Optional[int] = None) -> Dict:
    """Total de vendas e montante arrecadado (apenas transações confirmadas)."""
    with get_conn() as conn:
        seller_clause = ""
        params: List = []
        today_params: List = []
        if seller_id is not None:
            seller_clause = " AND seller_id = ?"
            params.append(int(seller_id))
            today_params.append(int(seller_id))
        row = conn.execute(
            f"""
            SELECT
                COUNT(*)                      AS transactions_count,
                COALESCE(SUM(total), 0)       AS total_revenue,
                COALESCE(SUM(items_count), 0) AS items_sold
              FROM transactions
             WHERE status = 'confirmado'
               {seller_clause}
            """,
            params,
        ).fetchone()
        today = conn.execute(
            f"""
            SELECT
                COUNT(*)                AS transactions_today,
                COALESCE(SUM(total), 0) AS revenue_today
              FROM transactions
             WHERE status = 'confirmado'
               AND date(created_at) = date('now','localtime')
               {seller_clause}
            """,
            today_params,
        ).fetchone()

    return {
        "transactions_count": int(row["transactions_count"] or 0),
        "total_revenue": float(row["total_revenue"] or 0.0),
        "items_sold": int(row["items_sold"] or 0),
        "transactions_today": int(today["transactions_today"] or 0),
        "revenue_today": float(today["revenue_today"] or 0.0),
    }


def reset_totem_to_default_state() -> Dict[str, int]:
    """Reinicia o totem ao estado padrão: **estoque zerado** e histórico limpo.

    - Apaga **todas** as transações (itens inclusos por ``ON DELETE CASCADE``),
      removendo vendas e dados de cliente.
    - Apaga **todas** as movimentações de estoque.
    - Zera ``products.stock`` (cadastro) e ``event_products.stock`` (saldo por evento).
    - Registra linhas ``inicial`` com saldo **0**: uma por produto no catálogo global
      (``event_id`` nulo) e uma por par ``(evento, produto)`` em ``event_products``,
      para o histórico do painel permanecer coerente com a biblioteca e com cada evento.
    """
    with get_conn() as conn:
        n_tx_row = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()
        n_tx_before = int(n_tx_row["c"] or 0)

        conn.execute("DELETE FROM transactions")

        cur = conn.execute("DELETE FROM stock_movements")
        n_mov_deleted = int(cur.rowcount or 0)

        now = _now_iso()
        prod_rows = conn.execute("SELECT id FROM products").fetchall()
        reason = "Estado padrão (reinício — estoque zerado)"
        for r in prod_rows:
            pid = int(r["id"])
            conn.execute(
                "UPDATE products SET stock = 0, updated_at = ? WHERE id = ?",
                (now, pid),
            )
            conn.execute(
                """
                INSERT INTO stock_movements
                    (product_id, movement_type, quantity, delta,
                     balance_after, reason, created_by, created_at)
                VALUES (?, 'inicial', 0, 0, 0, ?, ?, ?)
                """,
                (pid, reason, "system", now),
            )

        conn.execute(
            "UPDATE event_products SET stock = 0, updated_at = ?",
            (now,),
        )
        ep_rows = conn.execute(
            "SELECT event_id, product_id FROM event_products"
        ).fetchall()
        for er in ep_rows:
            eid = int(er["event_id"])
            pid = int(er["product_id"])
            conn.execute(
                """
                INSERT INTO stock_movements
                    (product_id, event_id, movement_type, quantity, delta,
                     balance_after, reason, created_by, created_at)
                VALUES (?, ?, 'inicial', 0, 0, 0, ?, ?, ?)
                """,
                (pid, eid, reason, "system", now),
            )

        return {
            "transactions_deleted": n_tx_before,
            "movements_deleted": n_mov_deleted,
            "products_restored": len(prod_rows),
            "event_product_pairs_reset": len(ep_rows),
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


def get_transaction_by_order_number(order_number: str) -> Optional[Dict]:
    """Busca uma transação pelo número do pedido (ex: OM260424-1234), incluindo dados do cliente."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE order_number = ?",
            (order_number.strip(),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["items"] = _items_for(conn, int(row["id"]))
        return data


# ---------------------------------------------------------------------------
# Eventos de estoque
# ---------------------------------------------------------------------------

def create_event(
    name: str,
    description: str = "",
    *,
    badge_color: Optional[str] = None,
) -> int:
    """Cria um novo evento e retorna o id gerado."""
    now = _now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO events (name, description, badge_color, active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (name.strip(), (description or "").strip(), badge_color, now, now),
        )
        return int(cur.lastrowid)


def list_events(include_archived: bool = False) -> List[Dict]:
    """Lista eventos com contagem de produtos e de vendedores associados."""
    where = "" if include_archived else "WHERE e.active = 1"
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.id, e.name, e.description, e.badge_color, e.active,
                e.created_at, e.updated_at,
                (SELECT COUNT(*) FROM event_products ep WHERE ep.event_id = e.id) AS products_count,
                (SELECT COUNT(*) FROM event_sellers es WHERE es.event_id = e.id) AS sellers_count
            FROM events e
            {where}
            ORDER BY e.active DESC, e.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_event(event_id: int) -> Optional[Dict]:
    """Retorna dados de um evento pelo id, ou None se não existir."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None


def update_event(
    event_id: int,
    name: str,
    description: str = "",
    *,
    badge_color: Optional[str] = None,
) -> None:
    """Atualiza nome, descrição e cor opcional do badge."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE events SET name = ?, description = ?, badge_color = ?, updated_at = ?
             WHERE id = ?
            """,
            (name.strip(), (description or "").strip(), badge_color, now, event_id),
        )


def archive_event(event_id: int) -> None:
    """Arquiva (desativa) um evento. Não apaga os dados."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET active = 0, updated_at = ? WHERE id = ?",
            (now, event_id),
        )


def restore_event(event_id: int) -> None:
    """Reativa um evento arquivado."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET active = 1, updated_at = ? WHERE id = ?",
            (now, event_id),
        )


def find_product_by_sku_or_id(q: str) -> Optional[Dict]:
    """Busca produto por ID numérico ou SKU exato. Retorna dict ou None."""
    q = (q or "").strip()
    if not q:
        return None
    with get_conn() as conn:
        row = None
        try:
            pid = int(q)
            row = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        except ValueError:
            pass
        if row is None:
            row = conn.execute(
                "SELECT * FROM products WHERE sku = ?", (q,)
            ).fetchone()
        return dict(row) if row else None


def add_product_to_event(
    event_id: int,
    product_id: int,
    stock: int = 0,
    min_stock: int = 0,
    *,
    link_audit_reason: Optional[str] = None,
    link_audit_reference: Optional[str] = None,
    created_by: Optional[str] = None,
) -> None:
    """Adiciona um produto ao evento com estoque inicial.

    ``link_audit_reason`` quando informado ativa o fluxo da biblioteca geral: grava
    inclusão como movimentação tipo ``ajuste`` (``stock_movements`` com ``event_id``),
    visível nas movimentações globais e do evento. Estoque inicial > 0 vira o delta do
    ajuste; com estoque 0 grava-se linha de auditoria com delta 0.

    Sem ``link_audit_reason``, mantém o comportamento legado (apenas ``INSERT`` em
    ``event_products``).

    Lança ``ValueError`` se o produto já pertence ao evento ou motivo vazio no fluxo com auditoria.
    """
    now = _now_iso()
    stock_i = max(0, int(stock))
    min_i = max(0, int(min_stock))

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM event_products WHERE event_id = ? AND product_id = ?",
            (event_id, product_id),
        ).fetchone()
        if existing:
            raise ValueError("Produto já adicionado a este evento.")

        if link_audit_reason is None:
            conn.execute(
                """
                INSERT INTO event_products
                    (event_id, product_id, stock, min_stock, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, product_id, stock_i, min_i, now, now),
            )
            return

        note = (link_audit_reason or "").strip()
        if not note:
            raise ValueError("Informe Motivo / Ref.")
        ref_note = (link_audit_reference or "").strip() or None

        ev_ok = conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (int(event_id),)
        ).fetchone()
        if ev_ok is None:
            raise ValueError("Evento não encontrado.")

        prod_ok = conn.execute(
            "SELECT 1 FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if prod_ok is None:
            raise ValueError("Produto não encontrado.")

        conn.execute(
            """
            INSERT INTO event_products
                (event_id, product_id, stock, min_stock, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?, ?)
            """,
            (event_id, product_id, min_i, now, now),
        )

        if stock_i > 0:
            _apply_event_movement(
                conn,
                event_id=int(event_id),
                product_id=int(product_id),
                movement_type="ajuste",
                delta=stock_i,
                reason=note,
                reference=ref_note,
                created_by=created_by,
            )
        else:
            _insert_event_stock_movement_row(
                conn,
                event_id=int(event_id),
                product_id=int(product_id),
                movement_type="ajuste",
                quantity=0,
                delta=0,
                balance_after=0,
                reason=note,
                reference=ref_note,
                created_by=created_by,
            )


def remove_product_from_event(event_id: int, product_id: int) -> None:
    """Remove um produto do evento."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM event_products WHERE event_id = ? AND product_id = ?",
            (event_id, product_id),
        )


def update_event_product_stock(
    event_id: int,
    product_id: int,
    stock: int,
    min_stock: int = 0,
) -> None:
    """Atualiza o estoque e mínimo de um produto dentro de um evento."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE event_products
               SET stock = ?, min_stock = ?, updated_at = ?
             WHERE event_id = ? AND product_id = ?
            """,
            (max(0, int(stock)), max(0, int(min_stock)), now, event_id, product_id),
        )


def list_event_products(event_id: int) -> List[Dict]:
    """Lista produtos de um evento com dados do catálogo (JOIN com products)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                ep.id            AS ep_id,
                ep.event_id,
                ep.product_id,
                ep.stock,
                ep.min_stock,
                ep.created_at    AS ep_created_at,
                ep.updated_at    AS ep_updated_at,
                p.name,
                p.sku,
                p.category,
                p.image,
                p.price,
                p.active         AS product_active
            FROM event_products ep
            JOIN products p ON p.id = ep.product_id
            WHERE ep.event_id = ?
            ORDER BY p.name COLLATE NOCASE
            """,
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]


_EVENT_PRODUCTS_ADMIN_FROM = """
FROM event_products ep
JOIN products p ON p.id = ep.product_id
WHERE ep.event_id = ?
"""


def _event_products_admin_filter_clause(
    q: Optional[str],
    categoria: str,
    status: str,
) -> Tuple[str, List]:
    """Cláusula AND … para filtros da grade de estoque do evento (admin)."""
    parts: List[str] = []
    params: List = []
    if q:
        qs = q.strip()
        like = f"%{qs.lower()}%"
        or_parts = [
            "LOWER(p.name) LIKE ?",
            "LOWER(COALESCE(p.description, '')) LIKE ?",
            "LOWER(COALESCE(p.sku, '')) LIKE ?",
        ]
        or_params: List = [like, like, like]
        id_part = qs.lstrip("#").strip()
        if id_part.isdigit():
            or_parts.append("p.id = ?")
            or_params.append(int(id_part))
            or_parts.append("INSTR(CAST(p.id AS TEXT), ?) > 0")
            or_params.append(id_part)
        parts.append("(" + " OR ".join(or_parts) + ")")
        params.extend(or_params)
    cat = (categoria or "todos").strip().lower()
    if cat != "todos":
        parts.append("LOWER(p.category) = LOWER(?)")
        params.append(categoria)
    st = (status or "todos").strip().lower()
    if st == "ok":
        parts.append(
            "p.active = 1 AND ep.stock > 0 AND "
            "(ep.min_stock <= 0 OR ep.stock >= ep.min_stock)"
        )
    elif st == "baixo":
        parts.append(
            "ep.min_stock > 0 AND ep.stock > 0 AND ep.stock < ep.min_stock"
        )
    elif st == "sem_estoque":
        parts.append("ep.stock <= 0")
    elif st == "inativo":
        parts.append("p.active = 0")
    extra = f" AND {' AND '.join(parts)}" if parts else ""
    return extra, params


def count_event_products_filtered(
    event_id: int,
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
) -> int:
    """Quantidade de vínculos evento–produto após filtros (lista admin)."""
    extra, params = _event_products_admin_filter_clause(q, categoria, status)
    sql = f"SELECT COUNT(*) AS c {_EVENT_PRODUCTS_ADMIN_FROM}{extra}"
    with get_conn() as conn:
        row = conn.execute(sql, (event_id, *params)).fetchone()
    return int(row["c"] if row else 0)


def list_event_products_slice(
    event_id: int,
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
    *,
    limit: int,
    offset: int,
) -> List[Dict]:
    """Página da grade de estoque do evento com os mesmos filtros da biblioteca geral."""
    extra, params = _event_products_admin_filter_clause(q, categoria, status)
    sql = f"""
            SELECT
                ep.id            AS ep_id,
                ep.event_id,
                ep.product_id,
                ep.stock,
                ep.min_stock,
                ep.created_at    AS ep_created_at,
                ep.updated_at    AS ep_updated_at,
                p.name,
                p.sku,
                p.category,
                p.description,
                p.image,
                p.price,
                p.active         AS product_active
            {_EVENT_PRODUCTS_ADMIN_FROM}
            {extra}
            ORDER BY p.name COLLATE NOCASE
            LIMIT ? OFFSET ?
            """
    with get_conn() as conn:
        rows = conn.execute(
            sql,
            (event_id, *params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def _event_products_slice_row_to_client(row: Dict) -> Dict:
    """Converte linha de ``list_event_products_slice`` para o formato do catálogo/vendedor."""
    pid = int(row["product_id"])
    sku_val = row.get("sku")
    sku = (sku_val or "").strip() if sku_val is not None else ""
    if not sku:
        sku = _default_sku_for_id(pid)
    estoque = int(row["stock"] or 0)
    estoque_minimo = int(row["min_stock"] or 0)
    return {
        "id": pid,
        "sku": sku,
        "nome": row["name"],
        "categoria": row["category"],
        "descricao": (row.get("description") or ""),
        "preco": float(row["price"] or 0),
        "imagem": row["image"],
        "estoque": estoque,
        "estoque_minimo": estoque_minimo,
        "ativo": bool(row["product_active"]),
        "abaixo_minimo": estoque_minimo > 0 and estoque < estoque_minimo,
        "sem_estoque": estoque <= 0,
    }


def list_event_products_filtered_for_client(
    event_id: int,
    q: Optional[str],
    status: str,
    *,
    limit: int,
    offset: int,
) -> List[Dict]:
    """Página de produtos do evento no formato cliente (painel vendedor)."""
    rows = list_event_products_slice(
        event_id,
        q,
        "todos",
        status,
        limit=int(limit),
        offset=int(offset),
    )
    return [_event_products_slice_row_to_client(r) for r in rows]


def get_event_stats(event_id: int) -> Dict:
    """Retorna estatísticas de um evento: produtos, unidades e abaixo do mínimo."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                                                    AS products_count,
                COALESCE(SUM(ep.stock), 0)                                                 AS units_in_stock,
                COALESCE(SUM(CASE WHEN ep.stock = 0 THEN 1 ELSE 0 END), 0)                AS sem_estoque,
                COALESCE(SUM(CASE WHEN ep.stock > 0 AND ep.stock < ep.min_stock THEN 1 ELSE 0 END), 0) AS below_min
            FROM event_products ep
            WHERE ep.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return dict(row) if row else {"products_count": 0, "units_in_stock": 0, "sem_estoque": 0, "below_min": 0}


def get_event_stock_stats(event_id: int) -> Dict:
    """Estatísticas expandidas de estoque do evento, incluindo valor monetário."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(ep.id)                                                                           AS products_count,
                COALESCE(SUM(ep.stock), 0)                                                             AS units_in_stock,
                COALESCE(SUM(ep.stock * p.price), 0)                                                   AS stock_value,
                COALESCE(SUM(CASE WHEN ep.stock = 0 THEN 1 ELSE 0 END), 0)                            AS sem_estoque,
                COALESCE(SUM(CASE WHEN ep.stock > 0 AND ep.stock < ep.min_stock THEN 1 ELSE 0 END), 0) AS below_min
            FROM event_products ep
            JOIN products p ON p.id = ep.product_id
            WHERE ep.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return dict(row) if row else {
            "products_count": 0, "units_in_stock": 0, "stock_value": 0.0,
            "sem_estoque": 0, "below_min": 0,
        }


def get_event_sales_dashboard(event_id: int, *, sales_days_limit: int = 120) -> Dict:
    """Vendas do evento: pedidos confirmados com movimento ``venda`` neste ``event_id``.

    Retorna receita total, ticket médio, até ``sales_days_limit`` dias distintos com vendas
    (mais recentes primeiro) e os 5 produtos mais vendidos por quantidade de unidades.
    """
    eid = int(event_id)
    lim_days = max(1, min(int(sales_days_limit), 366))
    tx_filter = (
        "FROM transactions t "
        "WHERE t.status = 'confirmado' "
        "AND EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
    )

    with get_conn() as conn:
        agg = conn.execute(
            f"SELECT COUNT(*) AS orders_count, COALESCE(SUM(t.total), 0) AS revenue_total {tx_filter}",
            (eid,),
        ).fetchone()
        orders_count = int(agg["orders_count"] or 0)
        revenue_total = float(agg["revenue_total"] or 0.0)
        avg_ticket = (revenue_total / orders_count) if orders_count else 0.0

        day_rows = conn.execute(
            f"""
            SELECT date(t.created_at) AS day, COUNT(*) AS orders_count
            {tx_filter}
            GROUP BY date(t.created_at)
            ORDER BY day DESC
            LIMIT ?
            """,
            (eid, lim_days),
        ).fetchall()
        sales_by_day = [
            {"day": str(r["day"]), "orders_count": int(r["orders_count"] or 0)}
            for r in day_rows
        ]

        top_rows = conn.execute(
            """
            SELECT
                ti.product_id AS product_id_raw,
                MAX(ti.product_name) AS product_name,
                SUM(ti.quantity) AS units_sold,
                SUM(ti.subtotal) AS revenue_subtotal,
                COALESCE(
                    MAX(NULLIF(TRIM(ti.product_sku), '')),
                    MAX(p.sku),
                    ''
                ) AS sku_display
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            LEFT JOIN products p ON p.id = CAST(ti.product_id AS INTEGER)
            WHERE t.status = 'confirmado'
              AND EXISTS (
                SELECT 1 FROM stock_movements m
                WHERE m.transaction_id = t.id AND m.movement_type = 'venda'
                  AND m.event_id = ?
              )
            GROUP BY ti.product_id
            ORDER BY units_sold DESC
            LIMIT 5
            """,
            (eid,),
        ).fetchall()

    top_products: List[Dict] = []
    for r in top_rows:
        raw_pid = r["product_id_raw"]
        pid_int: Optional[int] = None
        if raw_pid is not None and str(raw_pid).strip().isdigit():
            pid_int = int(str(raw_pid).strip())
        top_products.append(
            {
                "product_id": pid_int,
                "sku": (r["sku_display"] or "").strip() or "—",
                "product_name": (r["product_name"] or "").strip() or "—",
                "units_sold": int(r["units_sold"] or 0),
                "revenue": float(r["revenue_subtotal"] or 0.0),
            }
        )

    return {
        "orders_count": orders_count,
        "revenue_total": revenue_total,
        "avg_ticket": avg_ticket,
        "sales_by_day": sales_by_day,
        "top_products": top_products,
        "sales_days_limit": lim_days,
    }


# ---------------------------------------------------------------------------
# Movimentações escopadas ao evento
# ---------------------------------------------------------------------------

def _insert_event_stock_movement_row(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    product_id: int,
    movement_type: str,
    quantity: int,
    delta: int,
    balance_after: int,
    reason: Optional[str] = None,
    reference: Optional[str] = None,
    created_by: Optional[str] = None,
    unit_cost: Optional[float] = None,
    transaction_id: Optional[int] = None,
) -> Dict:
    """Insere uma linha em ``stock_movements`` com ``event_id`` sem alterar ``event_products``.

    Usado para auditoria com delta 0 (ex.: produto associado ao evento sem estoque inicial).
    """
    if movement_type not in _VALID_TYPES:
        raise ValueError(f"Tipo de movimentação inválido: {movement_type}")
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, event_id, movement_type, quantity, delta, balance_after,
             unit_cost, reason, reference, transaction_id, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(product_id),
            int(event_id),
            movement_type,
            int(quantity),
            int(delta),
            int(balance_after),
            float(unit_cost) if unit_cost is not None else None,
            reason,
            reference,
            int(transaction_id) if transaction_id is not None else None,
            created_by,
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "event_id": int(event_id),
        "product_id": int(product_id),
        "movement_type": movement_type,
        "delta": int(delta),
        "balance_after": int(balance_after),
        "created_at": now,
    }


def _apply_event_movement(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    product_id: int,
    movement_type: str,
    delta: int,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
    unit_cost: Optional[float] = None,
    reference: Optional[str] = None,
    transaction_id: Optional[int] = None,
) -> Dict:
    """Atomicamente: atualiza event_products.stock e insere stock_movements com event_id."""
    if movement_type not in _VALID_TYPES:
        raise ValueError(f"Tipo de movimentação inválido: {movement_type}")
    if delta == 0:
        raise ValueError("Movimentação com quantidade zero.")

    ep = conn.execute(
        """
        SELECT ep.stock, p.name
          FROM event_products ep
          JOIN products p ON p.id = ep.product_id
         WHERE ep.event_id = ? AND ep.product_id = ?
        """,
        (int(event_id), int(product_id)),
    ).fetchone()
    if ep is None:
        raise ValueError(f"Produto {product_id} não pertence ao evento {event_id}.")

    current = int(ep["stock"] or 0)
    new_stock = current + int(delta)
    if new_stock < 0:
        raise ValueError(
            f"Estoque insuficiente para '{ep['name']}' no evento: "
            f"disponível {current}, necessário {abs(delta)}."
        )

    now = _now_iso()
    conn.execute(
        "UPDATE event_products SET stock = ?, updated_at = ? WHERE event_id = ? AND product_id = ?",
        (new_stock, now, int(event_id), int(product_id)),
    )
    cur = conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, event_id, movement_type, quantity, delta, balance_after,
             unit_cost, reason, reference, transaction_id, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(product_id),
            int(event_id),
            movement_type,
            abs(int(delta)),
            int(delta),
            new_stock,
            float(unit_cost) if unit_cost is not None else None,
            reason,
            reference,
            int(transaction_id) if transaction_id is not None else None,
            created_by,
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "event_id": int(event_id),
        "product_id": int(product_id),
        "movement_type": movement_type,
        "delta": int(delta),
        "balance_after": new_stock,
        "created_at": now,
    }


def register_event_stock_entry(
    event_id: int,
    product_id: int,
    quantity: int,
    *,
    unit_cost: Optional[float] = None,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict:
    """Entrada de estoque no evento."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da entrada deve ser maior que zero.")
    with get_conn() as conn:
        return _apply_event_movement(
            conn, event_id=event_id, product_id=product_id,
            movement_type="entrada", delta=qty,
            unit_cost=unit_cost,
            reason=reason or None, created_by=created_by,
        )


def register_event_stock_exit(
    event_id: int,
    product_id: int,
    quantity: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Saída manual de estoque no evento."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da saída deve ser maior que zero.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo da saída.")
    with get_conn() as conn:
        return _apply_event_movement(
            conn, event_id=event_id, product_id=product_id,
            movement_type="saida", delta=-qty,
            reason=reason.strip(), created_by=created_by,
        )


def register_event_stock_adjustment(
    event_id: int,
    product_id: int,
    new_stock: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Ajusta o estoque de um produto no evento para um valor absoluto."""
    target = int(new_stock)
    if target < 0:
        raise ValueError("O estoque final não pode ser negativo.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo do ajuste.")
    with get_conn() as conn:
        ep = conn.execute(
            "SELECT stock FROM event_products WHERE event_id = ? AND product_id = ?",
            (int(event_id), int(product_id)),
        ).fetchone()
        if ep is None:
            raise ValueError(f"Produto {product_id} não pertence ao evento {event_id}.")
        delta = target - int(ep["stock"] or 0)
        if delta == 0:
            raise ValueError("O estoque informado é igual ao atual.")
        return _apply_event_movement(
            conn, event_id=event_id, product_id=product_id,
            movement_type="ajuste", delta=delta,
            reason=reason.strip(), created_by=created_by,
        )


def list_event_stock_movements(
    event_id: int,
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    limit: int = 300,
) -> List[Dict]:
    """Lista movimentações de estoque de um evento específico.

    ``product_search`` filtra por nome, descrição, SKU ou ID do produto (mesma
    semântica que em ``list_stock_movements``).

    ``reference`` filtra pelo código do pedido (campo ``m.reference``, vendas no totem),
    como em ``list_stock_movements``.

    ``seller_id`` (quando > 0): apenas **vendas** do vendedor indicado
    (``movement_type = 'venda'`` e ``transactions.seller_id`` coincidente).

    """
    sql = (
        "SELECT m.*, p.name AS product_name, p.category AS product_category, "
        "p.sku AS product_sku, "
        "evt.name AS event_name, "
        "evt.badge_color AS event_badge_color, "
        "t.client_name, t.client_cpf, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_city, t.client_state, "
        "t.payment_method, t.card_installments, t.aut, "
        "t.client_cro_uf, t.client_cro_numero "
        "FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN events evt ON evt.id = m.event_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE m.event_id = ?"
    )
    params: List = [int(event_id)]
    if product_id is not None:
        sql += " AND m.product_id = ?"
        params.append(int(product_id))
    if movement_type and movement_type in _VALID_TYPES:
        sql += " AND m.movement_type = ?"
        params.append(movement_type)
    frag, extra = _stock_movements_product_search_sql(product_search)
    sql += frag
    params.extend(extra)
    ref_norm = _normalize_order_reference(reference)
    if ref_norm:
        sql += (
            " AND m.reference IS NOT NULL "
            "AND INSTR(LOWER(m.reference), LOWER(?)) > 0"
        )
        params.append(ref_norm)
    if seller_id is not None and int(seller_id) > 0:
        sql += (
            " AND m.movement_type = 'venda' "
            "AND COALESCE(t.seller_id, -1) = ?"
        )
        params.append(int(seller_id))
    sql += " ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ?"
    params.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# Limites para exportações CSV (painel admin).
EXPORT_MOVEMENTS_CSV_CAP = 100_000
EXPORT_SALES_SUMMARY_CSV_CAP = 50_000
EXPORT_SALES_ITEMS_CSV_CAP = 200_000


def list_transactions_summary_for_event_period(
    event_id: int,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = EXPORT_SALES_SUMMARY_CSV_CAP,
) -> List[Dict]:
    """Pedidos com venda registrada em ``stock_movements`` para o evento (``movement_type='venda'``).

    ``date_from`` / ``date_to``: ``YYYY-MM-DD``, comparados com ``date(transactions.created_at)`` (inclusive).
    """
    cap = max(1, min(int(limit), EXPORT_SALES_SUMMARY_CSV_CAP))
    sql = (
        "SELECT t.id, t.order_number, t.created_at, t.total, t.items_count, t.status, "
        "t.client_name, t.client_cpf, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_city, t.client_state, "
        "t.seller_id, t.seller_name, t.payment_method, t.card_installments, t.aut, "
        "t.client_cro_uf, t.client_cro_numero "
        "FROM transactions t "
        "WHERE EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
    )
    params: List = [int(event_id)]
    if date_from:
        sql += " AND date(t.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(t.created_at) <= date(?)"
        params.append(date_to)
    sql += " ORDER BY datetime(t.created_at) DESC, t.id DESC LIMIT ?"
    params.append(cap)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_transaction_items_for_event_period(
    event_id: int,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = EXPORT_SALES_ITEMS_CSV_CAP,
) -> List[Dict]:
    """Itens de pedidos cuja venda está ligada ao evento (via ``stock_movements``)."""
    cap = max(1, min(int(limit), EXPORT_SALES_ITEMS_CSV_CAP))
    sql = (
        "SELECT ti.id AS item_id, t.id AS transaction_id, t.order_number, t.created_at, "
        "t.seller_id, t.seller_name, t.payment_method, t.card_installments, t.aut, "
        "ti.product_id, ti.product_name, ti.category, ti.product_sku, "
        "ti.quantity, ti.unit_price, ti.subtotal "
        "FROM transaction_items ti "
        "JOIN transactions t ON t.id = ti.transaction_id "
        "WHERE EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
    )
    params: List = [int(event_id)]
    if date_from:
        sql += " AND date(t.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(t.created_at) <= date(?)"
        params.append(date_to)
    sql += " ORDER BY datetime(t.created_at) DESC, ti.id ASC LIMIT ?"
    params.append(cap)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Vendedores do evento
# ---------------------------------------------------------------------------

def list_event_sellers(event_id: int) -> List[Dict]:
    """Vendedores associados ao evento, com métricas de vendas (mesmo critério que ``list_sellers``)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.name,
                s.email,
                s.active,
                s.pin_hash,
                s.last_login_at,
                es.added_at,
                COALESCE(COUNT(t.id), 0) AS transactions_count,
                COALESCE(SUM(t.total), 0) AS total_revenue
              FROM event_sellers es
              JOIN sellers s ON s.id = es.seller_id
              LEFT JOIN transactions t
                ON t.seller_id = s.id AND t.status = 'confirmado'
             WHERE es.event_id = ?
             GROUP BY s.id, es.added_at
             ORDER BY s.active DESC, LOWER(s.name), LOWER(s.email)
            """,
            (int(event_id),),
        ).fetchall()
        return [dict(r) for r in rows]


def add_seller_to_event(event_id: int, seller_id: int) -> None:
    """Associa um vendedor ao evento. Falha se o vendedor já estiver em outro evento."""
    now = _now_iso()
    eid = int(event_id)
    sid = int(seller_id)
    with get_conn() as conn:
        existing_same = conn.execute(
            "SELECT 1 FROM event_sellers WHERE event_id = ? AND seller_id = ?",
            (eid, sid),
        ).fetchone()
        if existing_same:
            return
        other = conn.execute(
            "SELECT event_id FROM event_sellers WHERE seller_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
        if other is not None and int(other["event_id"]) != eid:
            raise ValueError(
                "Este vendedor já está associado a outro evento. "
                "Remova-o desse evento ou altere a designação na ficha do vendedor."
            )
        try:
            conn.execute(
                "INSERT INTO event_sellers (event_id, seller_id, added_at) VALUES (?, ?, ?)",
                (eid, sid, now),
            )
        except sqlite3.IntegrityError as exc:
            err = str(exc).lower()
            if "unique" in err and "seller_id" in err:
                raise ValueError(
                    "Este vendedor já está associado a outro evento. "
                    "Remova-o desse evento ou altere a designação na ficha do vendedor."
                ) from exc
            raise


def get_seller_admin_event_selection_id(seller_id: int) -> Optional[int]:
    """Id do evento para pré-selecionar no admin: ativo mais recente; senão qualquer vínculo mais recente."""
    sid = int(seller_id)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT es.event_id
              FROM event_sellers es
              JOIN events e ON e.id = es.event_id
             WHERE es.seller_id = ? AND e.active = 1
             ORDER BY es.added_at DESC
             LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if row:
            return int(row["event_id"])
        row = conn.execute(
            """
            SELECT es.event_id
              FROM event_sellers es
             WHERE es.seller_id = ?
             ORDER BY es.added_at DESC
             LIMIT 1
            """,
            (sid,),
        ).fetchone()
        return int(row["event_id"]) if row else None


def replace_seller_event_assignment(seller_id: int, event_id: Optional[int]) -> None:
    """Remove todos os vínculos evento×vendedor e associa no máximo a um evento (substituição exclusiva)."""
    sid = int(seller_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM event_sellers WHERE seller_id = ?", (sid,))
        if event_id is None or int(event_id) <= 0:
            return
        eid = int(event_id)
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (eid,)).fetchone()
        if ev is None:
            raise ValueError("Evento não encontrado.")
        now = _now_iso()
        conn.execute(
            "INSERT INTO event_sellers (event_id, seller_id, added_at) VALUES (?, ?, ?)",
            (eid, sid, now),
        )


def get_active_event_for_seller(seller_id: int) -> Optional[Dict]:
    """Retorna o evento ativo mais recente ao qual o vendedor está associado, ou None."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT e.*
              FROM event_sellers es
              JOIN events e ON e.id = es.event_id
             WHERE es.seller_id = ? AND e.active = 1
             ORDER BY es.added_at DESC
             LIMIT 1
            """,
            (int(seller_id),),
        ).fetchone()
        return dict(row) if row else None


def list_event_products_for_client(event_id: int) -> List[Dict]:
    """Produtos do evento no formato cliente/catálogo, com estoque do evento."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.*, ep.stock AS event_stock, ep.min_stock AS event_min_stock
              FROM event_products ep
              JOIN products p ON p.id = ep.product_id
             WHERE ep.event_id = ? AND p.active = 1
             ORDER BY p.category, p.name
            """,
            (int(event_id),),
        ).fetchall()
    result = []
    for r in rows:
        d = _product_row_to_client(r)
        d["estoque"] = int(r["event_stock"] or 0)
        d["estoque_minimo"] = int(r["event_min_stock"] or 0)
        d["abaixo_minimo"] = d["estoque_minimo"] > 0 and d["estoque"] < d["estoque_minimo"]
        d["sem_estoque"] = d["estoque"] <= 0
        result.append(d)
    return result


def list_active_event_product_stocks(event_id: int) -> List[Dict]:
    """Id e estoque dos produtos ativos no evento (polling do catálogo)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ep.product_id AS id, ep.stock AS estoque
              FROM event_products ep
              JOIN products p ON p.id = ep.product_id
             WHERE ep.event_id = ? AND p.active = 1
            """,
            (int(event_id),),
        ).fetchall()
    return [{"id": int(r["id"]), "estoque": int(r["estoque"] or 0)} for r in rows]
