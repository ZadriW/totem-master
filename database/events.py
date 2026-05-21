"""Events, event–product links, CSV export helpers and event sellers."""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

from .connection import _now_iso, get_conn
from .event_stock import _apply_event_movement, _insert_event_stock_movement_row
from .products import _product_row_to_client
from .sku_helpers import _default_sku_for_id

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
        " AND t.status = 'confirmado'"
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
        " AND t.status = 'confirmado'"
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
