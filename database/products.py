"""Product catalog, Wake sync and admin library listings."""
from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple

from .connection import _now_iso, get_conn
from .sku_helpers import (
    _default_sku_for_id,
    _ensure_distinct_sku,
    _is_generated_fallback_sku,
    _is_placeholder_product_name,
)

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

