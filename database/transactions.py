"""Orders / transactions."""
from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .connection import _now_iso, get_conn
from .event_stock import _apply_event_movement
from .promotions import (
    apply_list_prices_to_normalized_items,
    apply_promotions_to_items_in_conn,
    build_promo_display_map,
    enrich_product_with_promo,
    get_active_promotions_for_event,
)
from .sku_helpers import _default_sku_for_id
from .stock import _apply_movement, _normalize_order_reference

TX_FILTER_STATUSES = frozenset({"confirmado", "pendente", "cancelado", "estornado"})

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


def _promo_names_for_normalized(conn: sqlite3.Connection, normalized: List[Dict]) -> Dict[int, str]:
    promo_ids = {int(i["promotion_id"]) for i in normalized if i.get("promotion_id") is not None}
    if not promo_ids:
        return {}
    placeholders = ",".join("?" * len(promo_ids))
    rows = conn.execute(
        f"SELECT id, name FROM promotions WHERE id IN ({placeholders})",
        list(promo_ids),
    ).fetchall()
    return {int(r["id"]): str(r["name"] or "") for r in rows}


def _public_items_from_normalized(
    normalized: List[Dict],
    promo_names: Optional[Dict[int, str]] = None,
) -> List[Dict]:
    """Itens formatados para resposta JSON (checkout / cotação)."""
    names = promo_names or {}
    out: List[Dict] = []
    for i in normalized:
        pid = i.get("product_id")
        if pid is None:
            continue
        qty = int(i.get("quantity") or 0)
        list_p = float(i.get("original_price") or i.get("unit_price") or 0)
        subtotal = float(i.get("subtotal") or 0)
        promo_id = i.get("promotion_id")
        has_promo = promo_id is not None and subtotal < round(list_p * qty, 2) - 0.001
        out.append(
            {
                "id": int(pid),
                "quantidade": qty,
                "preco_lista": list_p,
                "preco": float(i.get("unit_price") or 0),
                "subtotal": subtotal,
                "promotion_id": promo_id,
                "em_promocao": has_promo,
                "promo_nome": names.get(int(promo_id), "") if promo_id is not None else "",
                "economia": round(max(0.0, list_p * qty - subtotal), 2),
            }
        )
    return out


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

        # Preço de lista do catálogo + promoções ativas do evento.
        if event_id is not None:
            apply_list_prices_to_normalized_items(conn, normalized)
            normalized = apply_promotions_to_items_in_conn(conn, event_id, normalized)

        # Recalcula total e items_count após promoções.
        total = round(sum(i["subtotal"] for i in normalized), 2)
        items_count = sum(i["quantity"] for i in normalized)
        card_installments_store = _normalize_card_installments_for_db(
            payment_method, total, card_installments if card_installments is not None else 1,
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
                 unit_price, quantity, subtotal, product_sku, original_price, promotion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    i.get("original_price"),
                    i.get("promotion_id"),
                )
                for i in normalized
            ],
        )

        # Guarda normalized/demand/event_id no contexto de retorno para uso posterior.
        # O estoque só é baixado em confirm_transaction_with_aut().
        promo_names = _promo_names_for_normalized(conn, normalized)

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
        "items": _public_items_from_normalized(normalized, promo_names),
        "subtotal_lista": round(
            sum(
                float(i.get("original_price") or i.get("unit_price") or 0)
                * int(i.get("quantity") or 0)
                for i in normalized
            ),
            2,
        ),
        "economia_total": round(
            max(
                0.0,
                sum(
                    float(i.get("original_price") or i.get("unit_price") or 0)
                    * int(i.get("quantity") or 0)
                    for i in normalized
                )
                - total,
            ),
            2,
        ),
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

        # Preço de lista do catálogo + promoções ativas do evento.
        if event_id is not None:
            apply_list_prices_to_normalized_items(conn, normalized)
            normalized = apply_promotions_to_items_in_conn(conn, event_id, normalized)

        total = round(sum(i["subtotal"] for i in normalized), 2)
        items_count = sum(i["quantity"] for i in normalized)
        card_installments_store = _normalize_card_installments_for_db(
            payment_method, total,
            card_installments if card_installments is not None else 1,
        )

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
                 unit_price, quantity, subtotal, product_sku, original_price, promotion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    i.get("original_price"),
                    i.get("promotion_id"),
                )
                for i in normalized
            ],
        )

        promo_names = _promo_names_for_normalized(conn, normalized)

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
        "items": _public_items_from_normalized(normalized, promo_names),
        "subtotal_lista": round(
            sum(
                float(i.get("original_price") or i.get("unit_price") or 0)
                * int(i.get("quantity") or 0)
                for i in normalized
            ),
            2,
        ),
        "economia_total": round(
            max(
                0.0,
                sum(
                    float(i.get("original_price") or i.get("unit_price") or 0)
                    * int(i.get("quantity") or 0)
                    for i in normalized
                )
                - total,
            ),
            2,
        ),
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


def refund_transaction(
    tx_id: int,
    *,
    created_by: str = "admin",
    expected_event_id: Optional[int] = None,
) -> Dict:
    """Estorna transação confirmada: repõe estoque e marca status ``estornado``.

    Levanta ``ValueError`` se a transação não existir, não estiver confirmada,
    já tiver sido estornada ou não pertencer ao ``expected_event_id`` informado.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if row is None:
            raise ValueError("Transação não encontrada.")
        if row["status"] != "confirmado":
            raise ValueError("Somente transações confirmadas podem ser estornadas.")

        tx_row = dict(row)
        order_number = (tx_row.get("order_number") or "").strip() or f"#{tx_id}"
        reason = f"Estorno"

        dup = conn.execute(
            """
            SELECT 1 FROM stock_movements
             WHERE transaction_id = ? AND delta > 0 AND reason LIKE 'Estorno%'
             LIMIT 1
            """,
            (tx_id,),
        ).fetchone()
        if dup:
            raise ValueError("Esta transação já foi estornada.")

        event_id_raw = tx_row.get("event_id")
        event_id: Optional[int] = (
            int(event_id_raw) if event_id_raw is not None else None
        )
        if event_id is None:
            mov = conn.execute(
                """
                SELECT event_id FROM stock_movements
                 WHERE transaction_id = ? AND movement_type = 'venda' AND event_id IS NOT NULL
                 LIMIT 1
                """,
                (tx_id,),
            ).fetchone()
            if mov and mov["event_id"] is not None:
                event_id = int(mov["event_id"])

        if expected_event_id is not None:
            tx_ev = tx_row.get("event_id")
            resolved = event_id
            if resolved is None and tx_ev is not None:
                resolved = int(tx_ev)
            if resolved is None or int(resolved) != int(expected_event_id):
                raise ValueError("Esta transação não pertence a este evento.")

        items_rows = conn.execute(
            "SELECT product_id, quantity FROM transaction_items WHERE transaction_id = ?",
            (tx_id,),
        ).fetchall()
        demand: Dict[int, int] = {}
        for it in items_rows:
            try:
                pid = int(it["product_id"])
            except (TypeError, ValueError):
                continue
            demand[pid] = demand.get(pid, 0) + int(it["quantity"] or 0)

        if not demand:
            raise ValueError("Transação sem itens válidos para estorno.")

        ref = order_number if order_number.startswith("OM") else None
        if event_id is not None:
            for pid, qty in demand.items():
                _apply_event_movement(
                    conn,
                    event_id=int(event_id),
                    product_id=pid,
                    movement_type="entrada",
                    delta=qty,
                    reason=reason,
                    reference=ref,
                    transaction_id=tx_id,
                    created_by=created_by,
                )
        else:
            for pid, qty in demand.items():
                _apply_movement(
                    conn,
                    product_id=pid,
                    movement_type="entrada",
                    delta=qty,
                    reason=reason,
                    reference=ref,
                    transaction_id=tx_id,
                    created_by=created_by,
                )

        conn.execute(
            "UPDATE transactions SET status = 'estornado' WHERE id = ?",
            (tx_id,),
        )

    return {
        "id": tx_id,
        "order_number": tx_row.get("order_number"),
        "status": "estornado",
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

    promo_map: Dict[int, Dict] = {}
    if event_id is not None:
        promos = get_active_promotions_for_event(event_id)
        promo_map = build_promo_display_map(promos)

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

            list_p = float(it.get("original_price") or pr["price"] or 0)
            unit_p = float(it.get("unit_price") or list_p)
            subtotal = float(it.get("subtotal") or round(unit_p * qty, 2))
            promo_nome = (it.get("promotion_name") or "").strip()
            has_promo = it.get("promotion_id") is not None and subtotal < round(list_p * qty, 2) - 0.001

            entry: Dict = {
                "id": pid,
                "sku": (pr["sku"] or "").strip(),
                "nome": it.get("product_name") or pr["name"],
                "categoria": it.get("category") or pr["category"] or "",
                "preco_lista": list_p,
                "preco": unit_p,
                "subtotal": subtotal,
                "imagem": imagem,
                "estoque": estoque,
                "quantidade": qty,
                "em_promocao": has_promo or bool(promo_nome),
                "promo_nome": promo_nome,
                "promo_aplicada": has_promo,
                "economia": round(max(0.0, list_p * qty - subtotal), 2),
            }

            if promo_map:
                stub = {
                    "id": pid,
                    "preco": list_p,
                    "preco_original": list_p,
                    "em_promocao": False,
                }
                enriched = enrich_product_with_promo(stub, promo_map)
                if enriched.get("em_promocao"):
                    entry["em_promocao"] = True
                    entry["promo_tipo"] = enriched.get("promo_tipo") or ""
                    entry["promo_rule_value"] = enriched.get("promo_rule_value") or 0
                    entry["promo_min_qty"] = enriched.get("promo_min_qty") or 1
                    entry["promo_free_qty"] = enriched.get("promo_free_qty") or 0
                    entry["promo_badge"] = enriched.get("promo_badge") or ""
                    if not entry["promo_nome"]:
                        entry["promo_nome"] = enriched.get("promo_nome") or ""

            cart_items.append(entry)

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
        SELECT ti.id, ti.product_id, ti.product_name, ti.category,
               ti.unit_price, ti.quantity, ti.subtotal, ti.product_sku,
               ti.original_price, ti.promotion_id,
               pr.name AS promotion_name
          FROM transaction_items ti
          LEFT JOIN promotions pr ON pr.id = ti.promotion_id
         WHERE ti.transaction_id = ?
         ORDER BY ti.id
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
    on_date: Optional[str] = None,
) -> Tuple[str, List]:
    """Trecho ``WHERE ...`` (sem a palavra-chave) + parâmetros para transações do evento."""
    parts = ["t.event_id = ?"]
    params: List = [int(event_id)]
    if seller_id is not None:
        parts.append("t.seller_id = ?")
        params.append(int(seller_id))
    ref = _normalize_order_reference(order_search)
    if ref:
        parts.append(
            "(t.order_number IS NOT NULL AND INSTR(LOWER(t.order_number), LOWER(?)) > 0)"
        )
        params.append(ref)
    st = (status or "").strip().lower()
    if st and st != "todos" and st in TX_FILTER_STATUSES:
        parts.append("LOWER(TRIM(COALESCE(t.status, ''))) = ?")
        params.append(st)
    if on_date:
        parts.append("DATE(t.created_at) = DATE(?)")
        params.append(on_date)
    return " AND ".join(parts), params


def count_transactions_for_event(
    event_id: int,
    *,
    seller_id: Optional[int] = None,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
    on_date: Optional[str] = None,
) -> int:
    """Conta transações ligadas ao ``event_id`` (coluna ``transactions.event_id``)."""
    wh, params = _transactions_event_filter_sql_params(
        event_id,
        seller_id=seller_id,
        order_search=order_search,
        status=status,
        on_date=on_date,
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
    on_date: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> List[Dict]:
    """Lista transações do evento com itens (mesmo formato que ``list_transactions``)."""
    wh, params = _transactions_event_filter_sql_params(
        event_id,
        seller_id=seller_id,
        order_search=order_search,
        status=status,
        on_date=on_date,
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
    on_date: Optional[str] = None,
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
    if st and st != "todos" and st in TX_FILTER_STATUSES:
        parts.append("LOWER(TRIM(COALESCE(t.status, ''))) = ?")
        params.append(st)
    if on_date:
        parts.append("DATE(t.created_at) = DATE(?)")
        params.append(on_date)
    return " AND ".join(parts), params


def count_transactions_for_seller(
    seller_id: int,
    *,
    order_search: Optional[str] = None,
    status: Optional[str] = None,
    on_date: Optional[str] = None,
) -> int:
    """Conta transações em que ``seller_id`` coincide (catálogo global / sem filtro de evento)."""
    wh, params = _transactions_seller_scope_filter_sql_params(
        int(seller_id),
        order_search=order_search,
        status=status,
        on_date=on_date,
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
    on_date: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> List[Dict]:
    """Lista transações do vendedor com itens (mesmo formato que ``list_transactions_for_event``)."""
    wh, params = _transactions_seller_scope_filter_sql_params(
        int(seller_id),
        order_search=order_search,
        status=status,
        on_date=on_date,
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
