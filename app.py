"""Totem Odonto Master - aplicação Flask (boas-vindas + painéis admin/vendedor).

- ``/``                Tela de boas-vindas; o catálogo público não é autoatendimento.
- ``/catalogo`` etc. Rotas antigas redirecionam para ``/`` (venda só com vendedor logado).
- ``/api/...``         Endpoints JSON (vendas exigem sessão do vendedor).
- ``/admin/...``       Painel administrativo.
- ``/vendedor/...``    Painel do vendedor (dashboard, venda/catálogo, estoque, movimentações).
"""

from __future__ import annotations

import csv
import io
import os
import re
import secrets
import unicodedata
from collections import defaultdict
from functools import wraps
from datetime import datetime

from flask import (
    Flask,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from data.products import CATEGORIES
from database import (
    EXPORT_MOVEMENTS_CSV_CAP,
    add_product_to_event,
    add_seller_to_event,
    archive_event,
    count_products_admin_filtered,
    create_event,
    create_seller_account,
    create_transaction,
    delete_seller,
    ensure_seller_account,
    event_badge_style_pairs,
    find_product_by_sku_or_id,
    get_active_event_for_seller,
    get_event,
    get_event_sales_dashboard,
    get_event_stats,
    get_event_stock_stats,
    get_product,
    get_product_events_stock_total,
    get_product_in_event,
    get_products_library_stats,
    get_seller,
    get_seller_by_email,
    get_stats,
    get_transaction_by_order_number,
    init_db,
    list_active_event_product_stocks,
    list_active_product_stocks,
    list_event_products,
    list_event_products_for_client,
    list_event_sellers,
    list_event_stock_movements,
    list_events,
    list_products_admin,
    list_products_admin_slice,
    list_products_for_client,
    list_seller_pin_hashes,
    list_sellers,
    list_sellers_not_in_event,
    list_stock_movements,
    list_transaction_items_for_event_period,
    list_transactions,
    list_transactions_summary_for_event_period,
    normalize_event_badge_color,
    register_event_stock_adjustment,
    register_event_stock_entry,
    register_event_stock_exit,
    remove_product_from_event,
    remove_seller_from_event,
    reset_totem_to_default_state,
    restore_event,
    set_product_active,
    sync_products_from_wake,
    update_event,
    update_event_product_stock,
    update_seller_account,
    update_seller_last_login,
)
import wake_api


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

app = Flask(__name__)

# SECRET_KEY: usar variável de ambiente em produção.
# Fallback para chave aleatória por processo (sessões não persistem entre restarts).
app.secret_key = os.environ.get("TOTEM_SECRET_KEY") or secrets.token_hex(32)

# Credenciais do admin — sobrescreva em produção via variável de ambiente.
ADMIN_USERNAME = os.environ.get("TOTEM_ADMIN_USER", "adminmaster")
ADMIN_PASSWORD = os.environ.get("TOTEM_ADMIN_PASS", "adminmaster430@")

# Conta inicial do painel de vendedores. Em produção, sobrescreva por ambiente.
SELLER_DEFAULT_NAME = os.environ.get("TOTEM_SELLER_NAME", "Vendedor")
SELLER_DEFAULT_EMAIL = os.environ.get("TOTEM_SELLER_EMAIL", "vendedor@odontomaster.local")
SELLER_DEFAULT_PASSWORD = os.environ.get("TOTEM_SELLER_PASS", "vendedor123")
SELLER_DEFAULT_PIN = os.environ.get("TOTEM_SELLER_PIN", "0000")

ADMIN_AUTH_COOKIE = "totem_admin_auth"
SELLER_AUTH_COOKIE = "totem_seller_auth"
ADMIN_AUTH_SALT = "totem-admin-auth"
SELLER_AUTH_SALT = "totem-seller-auth"
AUTH_COOKIE_OPTIONS = {
    "httponly": True,
    "samesite": "Lax",
}


def _category_sort_key(name: str) -> str:
    """Ordenação sem acentos (alinha ao normalize() do catálogo no JS)."""
    if not name:
        return ""
    nfd = unicodedata.normalize("NFD", str(name))
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return stripped.lower()


def _category_initial_letter(name: str) -> str:
    """Inicial A-Z ou '#' para dígitos / símbolos / vazio."""
    key = _category_sort_key(name)
    if not key:
        return "#"
    ch = key[0]
    if ch.isdigit():
        return "#"
    if "a" <= ch <= "z":
        return ch.upper()
    return "#"


def _categories_by_initial(names: list[str]) -> list[tuple[str, list[str]]]:
    """Agrupa nomes de categoria por letra inicial; '#' (último) = 0-9 e outros."""
    buckets: dict[str, list[str]] = defaultdict(list)
    for raw in sorted(names, key=_category_sort_key):
        buckets[_category_initial_letter(raw)].append(raw)
    letters = sorted(k for k in buckets if k != "#")
    if "#" in buckets:
        letters.append("#")
    return [(letter, buckets[letter]) for letter in letters]


def _url_if_registered(endpoint: str, *, fallback: str) -> str:
    """Evita BuildError se o endpoint ainda não existir no mapa de rotas."""
    try:
        views = current_app.view_functions
    except RuntimeError:
        return fallback
    if endpoint in views:
        return url_for(endpoint)
    return fallback


def _normalize_payment_method_for_db(value) -> str:
    v = (value or "").strip().lower()
    if v in ("pix", "cartao"):
        return v
    return "cartao"


def _payment_method_label(value) -> str:
    v = (value or "").strip().lower()
    if v == "pix":
        return "PIX"
    if v == "cartao":
        return "Cartão"
    return "—"


def _display_created_by(value) -> str:
    """Texto exibível do campo created_by (remove prefixo interno ``vendedor:``)."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower().startswith("vendedor:"):
        s = s[9:].strip()
    return s


def _event_badge_style_filter(raw) -> str:
    """CSS inline para badge de evento (fundo + texto legível)."""
    bg, fg = event_badge_style_pairs(raw)
    if not bg or not fg:
        return ""
    return f"background-color:{bg};color:{fg};"


# Registro imediato: garante o filtro Jinja mesmo com importações parciais / reload.
app.add_template_filter(_display_created_by, "display_created_by")
app.add_template_filter(_event_badge_style_filter, "event_badge_style")

# Inicializa o schema e popula o catálogo inicial (se vazio).
init_db()

# Garante uma conta inicial para o painel de vendedores.
try:
    ensure_seller_account(
        SELLER_DEFAULT_NAME,
        SELLER_DEFAULT_EMAIL,
        generate_password_hash(SELLER_DEFAULT_PASSWORD),
        generate_password_hash(SELLER_DEFAULT_PIN),
    )
except Exception as exc:
    app.logger.warning("Conta inicial de vendedor indisponivel: %s", exc)

# Tenta sincronizar com a Wake Commerce no startup (silencioso se falhar).
try:
    wake_products = wake_api.fetch_products()
    if wake_products:
        result = sync_products_from_wake(wake_products)
        app.logger.info(
            "Sync Wake Commerce (startup): %d inseridos, %d atualizados.",
            result["inserted"], result["updated"],
        )
        _wake_categories = sorted({p["categoria"] for p in wake_products})
        if _wake_categories:
            CATEGORIES.clear()
            CATEGORIES.extend(_wake_categories)
except Exception as exc:
    app.logger.warning("Sync Wake Commerce indisponivel no startup: %s", exc)


def _auth_serializer(salt: str) -> URLSafeSerializer:
    return URLSafeSerializer(app.secret_key, salt=salt)


def _load_auth_cookie(cookie_name: str, salt: str) -> dict | None:
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    try:
        data = _auth_serializer(salt).loads(token)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


def _set_auth_cookie(response, cookie_name: str, salt: str, payload: dict):
    response.set_cookie(
        cookie_name,
        _auth_serializer(salt).dumps(payload),
        **AUTH_COOKIE_OPTIONS,
    )
    return response


def _delete_auth_cookie(response, cookie_name: str):
    response.delete_cookie(cookie_name, samesite=AUTH_COOKIE_OPTIONS["samesite"])
    return response


def _admin_auth() -> dict | None:
    data = _load_auth_cookie(ADMIN_AUTH_COOKIE, ADMIN_AUTH_SALT)
    if data and data.get("is_admin"):
        return data
    if session.get("is_admin"):
        return {
            "is_admin": True,
            "admin_user": session.get("admin_user", "admin"),
        }
    return None


def _seller_auth() -> dict | None:
    data = _load_auth_cookie(SELLER_AUTH_COOKIE, SELLER_AUTH_SALT)
    if data and data.get("is_seller") and data.get("seller_id"):
        return data
    if session.get("is_seller") and session.get("seller_id"):
        return {
            "is_seller": True,
            "seller_id": int(session["seller_id"]),
            "seller_name": session.get("seller_name", "Vendedor"),
            "seller_email": session.get("seller_email", ""),
        }
    return None


def _is_admin_logged_in() -> bool:
    return bool(_admin_auth())


def _is_seller_logged_in() -> bool:
    return bool(_seller_auth())


def _clear_admin_session() -> None:
    """Remove apenas credenciais do painel admin (preserva vendedor na mesma sessão)."""
    for key in ("is_admin", "admin_user"):
        session.pop(key, None)


def _clear_seller_session() -> None:
    """Remove apenas credenciais do painel do vendedor (preserva admin na mesma sessão)."""
    for key in ("is_seller", "seller_id", "seller_name", "seller_email"):
        session.pop(key, None)


def _current_admin_user() -> str:
    auth = _admin_auth() or {}
    return auth.get("admin_user") or "admin"


def _current_seller_id() -> int:
    auth = _seller_auth() or {}
    return int(auth["seller_id"])


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_admin_logged_in():
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def seller_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = _seller_auth()
        if not auth:
            return redirect(url_for("seller_login", next=request.path))
        seller = get_seller(int(auth["seller_id"]))
        if seller is None or not seller.get("active"):
            response = redirect(url_for("seller_login"))
            _clear_seller_session()
            return _delete_auth_cookie(response, SELLER_AUTH_COOKIE)
        return view(*args, **kwargs)

    return wrapped


def _normalize_seller_pin(pin: str) -> str:
    value = (pin or "").strip()
    if not (value.isdigit() and len(value) == 4):
        raise ValueError("O PIN deve conter exatamente 4 dígitos.")
    return value


def _pin_is_available(pin: str, *, ignore_seller_id: int | None = None) -> bool:
    value = _normalize_seller_pin(pin)
    for seller in list_seller_pin_hashes():
        if ignore_seller_id is not None and int(seller["id"]) == int(ignore_seller_id):
            continue
        if check_password_hash(seller["pin_hash"], value):
            return False
    return True


# ---------------------------------------------------------------------------
# Rotas do cliente
# ---------------------------------------------------------------------------

@app.route("/")
def welcome():
    return render_template("welcome.html")


@app.route("/catalogo")
def catalog():
    return redirect(url_for("welcome"))


@app.route("/pagamento")
def payment():
    return redirect(url_for("welcome"))


@app.route("/pagamento/aguardando")
def payment_waiting():
    return redirect(url_for("welcome"))


# ---------------------------------------------------------------------------
# API pública (consumida pelo front do totem)
# ---------------------------------------------------------------------------

@app.route("/api/produtos")
def api_products():
    category = request.args.get("categoria", "").strip()
    query = request.args.get("q", "").strip().lower()
    products = list_products_for_client(category=category or None, query=query or None)
    return jsonify(products)


@app.route("/api/transacoes", methods=["POST"])
def api_create_transaction():
    """Registra uma venda concluída e baixa o estoque atomicamente."""
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or payload.get("itens") or []
    client = payload.get("client") or {}
    payment_method = _normalize_payment_method_for_db(
        payload.get("payment_method") or client.get("payment_method"),
    )
    
    # CRO: UF e número informados no checkout (sem API externa)
    cro_uf = (client.get("cro_uf") or "").strip().upper()
    cro_numero = (client.get("cro_numero") or "").strip()

    try:
        auth = _seller_auth()
        if not auth:
            raise ValueError(
                "É necessário estar logado como vendedor para registrar a venda."
            )
        row = get_seller(int(auth["seller_id"]))
        if row is None or not row.get("active"):
            raise ValueError("Sessão de vendedor inválida ou inativa.")
        seller = {"id": int(row["id"]), "name": row["name"]}
        
        # Busca o evento ativo do vendedor (se houver)
        active_event = get_active_event_for_seller(int(seller["id"]))
        event_id = int(active_event["id"]) if active_event else None
        
        result = create_transaction(
            items,
            created_by=f"vendedor:{seller['name']}",
            seller_id=int(seller["id"]),
            seller_name=seller["name"],
            event_id=event_id,
            client_name=client.get("name"),
            client_cpf=client.get("cpf"),
            client_zipcode=client.get("zipcode"),
            client_address=client.get("address"),
            client_number=client.get("number"),
            client_complement=client.get("complement"),
            client_city=client.get("city"),
            client_state=client.get("state"),
            payment_method=payment_method,
            client_cro_uf=cro_uf or None,
            client_cro_numero=cro_numero or None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Falha ao registrar transação")
        return jsonify({"error": "Não foi possível registrar a transação."}), 500
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Painel administrativo — autenticação
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_index():
    if not _is_admin_logged_in():
        return redirect(url_for("admin_login"))
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if _is_admin_logged_in():
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            next_url = request.args.get("next") or url_for("admin_dashboard")
            if not next_url.startswith("/"):
                next_url = url_for("admin_dashboard")
            response = redirect(next_url)
            return _set_auth_cookie(
                response,
                ADMIN_AUTH_COOKIE,
                ADMIN_AUTH_SALT,
                {"is_admin": True, "admin_user": username},
            )
        error = "Usuário ou senha inválidos."
    return render_template("admin/login.html", error=error)


@app.route("/admin/logout", methods=["POST", "GET"])
def admin_logout():
    _clear_admin_session()
    response = redirect(url_for("admin_login"))
    return _delete_auth_cookie(response, ADMIN_AUTH_COOKIE)


# ---------------------------------------------------------------------------
# Painel do vendedor — autenticação e leitura
# ---------------------------------------------------------------------------

@app.route("/vendedor")
def seller_index():
    if not _is_seller_logged_in():
        return redirect(url_for("seller_login"))
    return redirect(url_for("seller_dashboard"))


@app.route("/vendedor/login", methods=["GET", "POST"])
def seller_login():
    if _is_seller_logged_in():
        return redirect(url_for("seller_dashboard"))

    error = None
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        seller = get_seller_by_email(email)
        if seller and seller.get("active") and check_password_hash(
            seller["password_hash"], password
        ):
            update_seller_last_login(int(seller["id"]))
            next_url = request.args.get("next") or url_for("seller_dashboard")
            if not next_url.startswith("/vendedor"):
                next_url = url_for("seller_dashboard")
            response = redirect(next_url)
            return _set_auth_cookie(
                response,
                SELLER_AUTH_COOKIE,
                SELLER_AUTH_SALT,
                {
                    "is_seller": True,
                    "seller_id": int(seller["id"]),
                    "seller_name": seller["name"],
                    "seller_email": seller["email"],
                },
            )
        error = "E-mail ou senha inválidos."

    return render_template("seller/login.html", error=error, email=email)


@app.route("/vendedor/logout", methods=["POST", "GET"])
def seller_logout():
    _clear_seller_session()
    response = redirect(url_for("seller_login"))
    return _delete_auth_cookie(response, SELLER_AUTH_COOKIE)


def _seller_shell_context(**extra):
    auth = _seller_auth() or {}
    seller_id_raw = auth.get("seller_id")
    seller_event = (
        get_active_event_for_seller(int(seller_id_raw))
        if seller_id_raw
        else None
    )
    ctx = {
        "seller_name": auth.get("seller_name", "Vendedor"),
        "seller_email": auth.get("seller_email", ""),
        "now": datetime.now(),
        "seller_event": seller_event,
    }
    ctx.update(extra)
    venda_url = _url_if_registered("seller_sale", fallback="/vendedor/venda")
    ctx["seller_venda_url"] = (venda_url or "").strip() or "/vendedor/venda"
    return ctx


def _get_seller_event():
    """Retorna o evento ativo do vendedor logado, ou None."""
    try:
        seller_id = _current_seller_id()
    except (KeyError, TypeError):
        return None
    return get_active_event_for_seller(seller_id)


def _seller_totem_flow() -> dict:
    """URLs do fluxo de venda no painel (consumido pelo JS)."""
    return {
        "payment": _url_if_registered(
            "seller_payment", fallback="/vendedor/pagamento"
        ),
        "paymentWaiting": _url_if_registered(
            "seller_payment_waiting", fallback="/vendedor/pagamento/aguardando"
        ),
        "catalog": _url_if_registered("seller_sale", fallback="/vendedor/venda"),
        "home": url_for("seller_dashboard"),
    }


def _seller_payment_page_context() -> dict:
    flow = _seller_totem_flow()
    return {
        "totem_flow": flow,
        "payment_catalog_url": _url_if_registered(
            "seller_sale", fallback="/vendedor/venda"),
        "payment_home_url": url_for("seller_dashboard"),
    }


@app.route("/vendedor/venda", endpoint="seller_sale")
@seller_required
def seller_sale():
    seller_ev = _get_seller_event()
    if seller_ev:
        products = list_event_products_for_client(seller_ev["id"])
        catalog_stock_api_url = url_for("seller_api_event_catalog_stock")
    else:
        products = list_products_for_client()
        catalog_stock_api_url = _url_if_registered(
            "seller_api_catalog_stock",
            fallback="/vendedor/api/catalogo/estoque",
        )
    category_names_set = {
        str(p["categoria"]).strip()
        for p in products
        if (p.get("categoria") or "").strip()
    }
    if not seller_ev:
        category_names_set.update(
            str(c).strip() for c in CATEGORIES if c and str(c).strip()
        )
    categories_by_letter = _categories_by_initial(sorted(category_names_set))
    categories = sorted(category_names_set) if seller_ev else CATEGORIES
    return render_template(
        "seller/catalog.html",
        categories=categories,
        categories_by_letter=categories_by_letter,
        products=products,
        totem_flow=_seller_totem_flow(),
        catalog_stock_api_url=catalog_stock_api_url,
        **_seller_shell_context(active_section="venda"),
    )


@app.route("/vendedor/pagamento", endpoint="seller_payment")
@seller_required
def seller_payment():
    return render_template("payment.html", **_seller_payment_page_context())


@app.route("/vendedor/pagamento/aguardando", endpoint="seller_payment_waiting")
@seller_required
def seller_payment_waiting():
    return render_template("payment_waiting.html", **_seller_payment_page_context())


@app.route("/vendedor/dashboard")
@seller_required
def seller_dashboard():
    seller_id = _current_seller_id()
    seller_ev = _get_seller_event()
    stats = get_stats(seller_id=seller_id)
    transactions = list_transactions(limit=100, seller_id=seller_id)
    if seller_ev:
        ev_stats = get_event_stock_stats(seller_ev["id"])
        stock = {
            "products_count": ev_stats["products_count"],
            "products_active": ev_stats["products_count"],
            "units_in_stock": ev_stats["units_in_stock"],
            "stock_value": ev_stats["stock_value"],
            "below_min": ev_stats["below_min"],
            "out_of_stock": ev_stats["sem_estoque"],
        }
        movements = list_event_stock_movements(seller_ev["id"], limit=80)
    else:
        stock = get_products_library_stats()
        movements = list_stock_movements(limit=80)
    return render_template(
        "seller/dashboard.html",
        stats=stats,
        stock=stock,
        transactions=transactions,
        movements=movements,
        **_seller_shell_context(active_section="dashboard"),
    )


@app.route("/vendedor/estoque")
@seller_required
def seller_stock():
    seller_ev = _get_seller_event()
    if seller_ev:
        ev_id = seller_ev["id"]
        products = list_event_products_for_client(ev_id)
        ev_stats = get_event_stock_stats(ev_id)
        stock = {
            "products_count": ev_stats["products_count"],
            "products_active": ev_stats["products_count"],
            "units_in_stock": ev_stats["units_in_stock"],
            "stock_value": ev_stats["stock_value"],
            "below_min": ev_stats["below_min"],
            "out_of_stock": ev_stats["sem_estoque"],
        }
        total = len(products)
        pagination = {
            "page": 1, "per_page": total or 1, "total": total,
            "total_pages": 1, "has_prev": False, "has_next": False,
            "showing_from": 1 if total else 0,
            "showing_to": total,
        }
        categories = sorted({p["categoria"] for p in products if p.get("categoria")})
        filters = {"q": "", "categoria": "todos", "status": "todos", "per_page": total or 1}
        stock_api_url = url_for("seller_api_event_stock")
        return render_template(
            "seller/stock.html",
            products=products,
            stock=stock,
            categories=categories,
            filters=filters,
            pagination=pagination,
            allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
            stock_api_url=stock_api_url,
            **_seller_shell_context(active_section="estoque"),
        )
    products, filters, pagination = _admin_stock_page_view()
    return render_template(
        "seller/stock.html",
        products=products,
        stock=get_products_library_stats(),
        categories=CATEGORIES,
        filters=filters,
        pagination=pagination,
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        stock_api_url=url_for("seller_api_stock",
                              q=filters["q"], categoria=filters["categoria"],
                              status=filters["status"],
                              per_page=filters["per_page"], page=pagination["page"]),
        **_seller_shell_context(active_section="estoque"),
    )


@app.route("/vendedor/movimentacoes")
@seller_required
def seller_movements():
    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    if not q:
        legacy_pid = _parse_int(request.args.get("produto") or "", 0)
        if legacy_pid:
            q = str(legacy_pid)
    pedido = (request.args.get("pedido") or "").strip()
    q_search = q or None

    seller_ev = _get_seller_event()
    if seller_ev:
        ev_id = seller_ev["id"]
        movements = list_event_stock_movements(
            ev_id,
            product_id=None,
            product_search=q_search,
            movement_type=movement_type or None,
            limit=500,
        )
        return render_template(
            "seller/movements.html",
            movements=movements,
            filters={
                "tipo": movement_type or "todos",
                "q": q,
                "pedido": pedido,
            },
            **_seller_shell_context(active_section="movimentacoes"),
        )

    movements = list_stock_movements(
        product_search=q_search,
        movement_type=movement_type or None,
        reference=pedido or None,
        limit=500,
    )
    return render_template(
        "seller/movements.html",
        movements=movements,
        filters={
            "tipo": movement_type or "todos",
            "q": q,
            "pedido": pedido,
        },
        **_seller_shell_context(active_section="movimentacoes"),
    )


def _seller_transaction_api(tx: dict) -> dict:
    """Serializa transação para o endpoint JSON do vendedor (somente leitura)."""
    items = tx.get("items") or []
    return {
        "id": int(tx["id"]),
        "order_number": tx["order_number"],
        "created_at_display": datahora_filter(tx["created_at"]),
        "items_count": int(tx["items_count"] or 0),
        "total_display": brl_filter(tx["total"]),
        "status": tx["status"],
        "payment_method": tx.get("payment_method"),
        "payment_method_label": _payment_method_label(tx.get("payment_method")),
        "items": [
            {
                "product_name": it.get("product_name") or "",
                "product_sku": it.get("product_sku"),
                "category": it.get("category") or "-",
                "quantity": int(it.get("quantity") or 0),
                "unit_price_display": brl_filter(it.get("unit_price")),
                "subtotal_display": brl_filter(it.get("subtotal")),
            }
            for it in items
        ],
    }


@app.route("/vendedor/api/estoque")
@seller_required
def seller_api_stock():
    seller_ev = _get_seller_event()
    if seller_ev:
        ev_id = seller_ev["id"]
        products = list_event_products_for_client(ev_id)
        ev_stats = get_event_stock_stats(ev_id)
        stock = {
            "products_count": ev_stats["products_count"],
            "products_active": ev_stats["products_count"],
            "units_in_stock": ev_stats["units_in_stock"],
            "stock_value": ev_stats["stock_value"],
            "below_min": ev_stats["below_min"],
            "out_of_stock": ev_stats["sem_estoque"],
        }
        return jsonify({
            "stock": stock,
            "pagination": {"page": 1, "per_page": len(products), "total": len(products), "total_pages": 1},
            "products": [{**p, "status": _event_product_status({
                "stock": p["estoque"], "min_stock": p["estoque_minimo"], "product_active": p["ativo"],
            })} for p in products],
        })
    products, _filters, pagination = _admin_stock_page_view()
    return jsonify({
        "stock": get_products_library_stats(),
        "pagination": {
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total": pagination["total"],
            "total_pages": pagination["total_pages"],
        },
        "products": [
            {**p, "status": _product_status(p)}
            for p in products
        ],
    })


@app.route("/vendedor/api/evento/estoque")
@seller_required
def seller_api_event_stock():
    """API de polling para a listagem de estoque do evento no painel do vendedor."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"error": "Não associado a nenhum evento ativo."}), 404
    ev_id = seller_ev["id"]
    products = list_event_products_for_client(ev_id)
    ev_stats = get_event_stock_stats(ev_id)
    stock = {
        "products_count": ev_stats["products_count"],
        "products_active": ev_stats["products_count"],
        "units_in_stock": ev_stats["units_in_stock"],
        "stock_value": ev_stats["stock_value"],
        "below_min": ev_stats["below_min"],
        "out_of_stock": ev_stats["sem_estoque"],
    }
    return jsonify({
        "stock": stock,
        "pagination": {"page": 1, "per_page": len(products), "total": len(products), "total_pages": 1},
        "products": [{**p, "status": _event_product_status({
            "stock": p["estoque"], "min_stock": p["estoque_minimo"], "product_active": p["ativo"],
        })} for p in products],
    })


@app.route(
    "/vendedor/api/catalogo/estoque",
    endpoint="seller_api_catalog_stock",
)
@seller_required
def seller_api_catalog_stock():
    """Payload mínimo para atualizar estoque nos cards do catálogo (polling)."""
    return jsonify({"products": list_active_product_stocks()})


@app.route("/vendedor/api/evento/catalogo/estoque", endpoint="seller_api_event_catalog_stock")
@seller_required
def seller_api_event_catalog_stock():
    """Polling de estoque do catálogo em modo evento."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"products": list_active_product_stocks()})
    return jsonify({"products": list_active_event_product_stocks(seller_ev["id"])})


@app.route("/vendedor/api/dashboard")
@seller_required
def seller_api_dashboard():
    """JSON com os mesmos dados do dashboard."""
    seller_id = _current_seller_id()
    seller_ev = _get_seller_event()
    transactions = list_transactions(limit=100, seller_id=seller_id)
    latest_tx_id = max((int(t["id"]) for t in transactions), default=0)
    if seller_ev:
        ev_stats = get_event_stock_stats(seller_ev["id"])
        stock = {
            "products_count": ev_stats["products_count"],
            "products_active": ev_stats["products_count"],
            "units_in_stock": ev_stats["units_in_stock"],
            "stock_value": ev_stats["stock_value"],
            "below_min": ev_stats["below_min"],
            "out_of_stock": ev_stats["sem_estoque"],
        }
    else:
        stock = get_products_library_stats()
    return jsonify({
        "stats": get_stats(seller_id=seller_id),
        "stock": stock,
        "latest_tx_id": latest_tx_id,
        "transactions": [_seller_transaction_api(t) for t in transactions],
    })


# ---------------------------------------------------------------------------
# Painel administrativo — dashboard
# ---------------------------------------------------------------------------

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    stats = get_stats()
    stock = get_products_library_stats()
    transactions = list_transactions(limit=300)
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        stock=stock,
        transactions=transactions,
        admin_user=_current_admin_user(),
        now=datetime.now(),
        active_section="dashboard",
    )


# ---------------------------------------------------------------------------
# Painel administrativo — vendedores
# ---------------------------------------------------------------------------

_SELLER_FORM_FIELD_ORDER = ("name", "email", "password", "pin")


def _first_seller_form_error_message(errors: dict[str, str]) -> str:
    for key in _SELLER_FORM_FIELD_ORDER:
        if key in errors:
            return errors[key]
    return next(iter(errors.values()), "Verifique os campos destacados.")


def _parse_new_seller_post(form) -> tuple[dict[str, str], dict[str, str]]:
    """Validação do cadastro de vendedor. Retorna (erros_por_campo, valores_para_reexibir)."""
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    pin_raw = form.get("pin") or ""

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Nome do vendedor é obrigatório."
    if not email:
        errors["email"] = "E-mail do vendedor é obrigatório."
    elif "@" not in email:
        errors["email"] = "Informe um e-mail válido."

    if len(password) < 6:
        errors["password"] = "A senha deve ter pelo menos 6 caracteres."

    if not (pin_raw or "").strip():
        errors["pin"] = "PIN do vendedor é obrigatório."
    else:
        try:
            pin_norm = _normalize_seller_pin(pin_raw)
        except ValueError as exc:
            errors["pin"] = str(exc)
            pin_norm = None
        if "pin" not in errors and not _pin_is_available(pin_norm):
            errors["pin"] = "Este PIN já está em uso por outro vendedor."

    if "email" not in errors and email and get_seller_by_email(email):
        errors["email"] = "Já existe um vendedor com este e-mail."

    def keep(field: str, value: str) -> str:
        return "" if field in errors else value

    repop = {
        "name": keep("name", name),
        "email": keep("email", email),
        "password": keep("password", password),
        "pin": keep("pin", pin_raw),
    }
    return errors, repop


def _parse_edit_seller_post(form, seller_id: int) -> tuple[dict[str, str], dict]:
    """Validação da edição de vendedor. Retorna (erros_por_campo, valores_para_reexibir)."""
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    active = form.get("active") == "1"
    password = form.get("password") or ""
    pin_raw = (form.get("pin") or "").strip()

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Nome do vendedor é obrigatório."
    if not email:
        errors["email"] = "E-mail do vendedor é obrigatório."
    elif "@" not in email:
        errors["email"] = "Informe um e-mail válido."

    if password and len(password) < 6:
        errors["password"] = "A nova senha deve ter pelo menos 6 caracteres."

    if pin_raw:
        try:
            pin_norm = _normalize_seller_pin(pin_raw)
        except ValueError as exc:
            errors["pin"] = str(exc)
            pin_norm = None
        if "pin" not in errors and not _pin_is_available(
            pin_norm, ignore_seller_id=seller_id
        ):
            errors["pin"] = "Este PIN já está em uso por outro vendedor."

    repop = {
        "name": "" if "name" in errors else name,
        "email": "" if "email" in errors else email,
        "active": active,
        "password": "" if "password" in errors else password,
        "pin": "" if "pin" in errors else pin_raw,
    }
    return errors, repop


@app.route("/admin/vendedores", methods=["GET", "POST"])
@admin_required
def admin_sellers():
    seller_form = None
    seller_form_errors: dict[str, str] = {}
    if request.method == "POST":
        seller_form_errors, seller_form = _parse_new_seller_post(request.form)
        if not seller_form_errors:
            try:
                seller = create_seller_account(
                    (request.form.get("name") or "").strip(),
                    (request.form.get("email") or "").strip().lower(),
                    generate_password_hash(request.form.get("password") or ""),
                    generate_password_hash(
                        _normalize_seller_pin(request.form.get("pin") or "")
                    ),
                )
                flash(f"Vendedor {seller['name']} criado com sucesso.", "success")
                return redirect(url_for("admin_seller_detail", seller_id=seller["id"]))
            except ValueError as exc:
                msg = str(exc)
                if "e-mail" in msg.lower() and ("já" in msg.lower() or "existente" in msg.lower()):
                    seller_form_errors = {"email": msg}
                    seller_form = _parse_new_seller_post(request.form)[1]
                    seller_form["email"] = ""
                else:
                    flash(msg, "error")
                    seller_form = _parse_new_seller_post(request.form)[1]
                    seller_form_errors = {}
        if seller_form_errors:
            flash(_first_seller_form_error_message(seller_form_errors), "error")

    sellers = list_sellers()
    return render_template(
        "admin/sellers.html",
        sellers=sellers,
        seller_form=seller_form,
        seller_form_errors=seller_form_errors,
        **_admin_shell_context(active_section="vendedores"),
    )


@app.route("/admin/vendedores/<int:seller_id>")
@admin_required
def admin_seller_detail(seller_id: int):
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))
    stats = get_stats(seller_id=seller_id)
    transactions = list_transactions(limit=200, seller_id=seller_id)
    return render_template(
        "admin/seller_detail.html",
        seller=seller,
        stats=stats,
        transactions=transactions,
        seller_form=None,
        seller_form_errors={},
        **_admin_shell_context(active_section="vendedores"),
    )


@app.route("/admin/vendedores/<int:seller_id>/editar", methods=["POST"])
@admin_required
def admin_seller_update(seller_id: int):
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))

    seller_form_errors, seller_form = _parse_edit_seller_post(request.form, seller_id)
    if seller_form_errors:
        flash(_first_seller_form_error_message(seller_form_errors), "error")
        stats = get_stats(seller_id=seller_id)
        transactions = list_transactions(limit=200, seller_id=seller_id)
        return render_template(
            "admin/seller_detail.html",
            seller=seller,
            stats=stats,
            transactions=transactions,
            seller_form=seller_form,
            seller_form_errors=seller_form_errors,
            **_admin_shell_context(active_section="vendedores"),
        )

    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    active = request.form.get("active") == "1"
    password = request.form.get("password") or ""
    pin = (request.form.get("pin") or "").strip()
    password_hash = None
    if password:
        password_hash = generate_password_hash(password)
    pin_hash = None
    if pin:
        pin_hash = generate_password_hash(_normalize_seller_pin(pin))
    try:
        seller = update_seller_account(
            seller_id,
            name=name,
            email=email,
            active=active,
            password_hash=password_hash,
            pin_hash=pin_hash,
        )
        flash(f"Dados de {seller['name']} atualizados.", "success")
    except ValueError as exc:
        msg = str(exc)
        seller_form_errors: dict[str, str] = {}
        if "e-mail" in msg.lower() or "email" in msg.lower():
            seller_form_errors["email"] = msg
        else:
            flash(msg, "error")
            return redirect(url_for("admin_seller_detail", seller_id=seller_id))
        seller_form = _parse_edit_seller_post(request.form, seller_id)[1]
        seller_form["email"] = ""
        flash(_first_seller_form_error_message(seller_form_errors), "error")
        stats = get_stats(seller_id=seller_id)
        transactions = list_transactions(limit=200, seller_id=seller_id)
        return render_template(
            "admin/seller_detail.html",
            seller=seller,
            stats=stats,
            transactions=transactions,
            seller_form=seller_form,
            seller_form_errors=seller_form_errors,
            **_admin_shell_context(active_section="vendedores"),
        )
    return redirect(url_for("admin_seller_detail", seller_id=seller_id))


@app.route(
    "/admin/vendedores/<int:seller_id>/excluir",
    methods=["POST"],
    endpoint="admin_seller_delete",
)
@admin_required
def admin_seller_delete(seller_id: int):
    try:
        deleted = delete_seller(seller_id)
        flash(
            f"Cadastro de {deleted['name']} excluído. "
            "Vendas antigas permanecem no histórico, sem vínculo a este vendedor.",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_sellers"))


@app.route("/admin/sincronizar-wake", methods=["POST"])
@admin_required
def admin_sync_wake():
    """Sincroniza a biblioteca de produtos com a Wake Commerce."""
    try:
        products = wake_api.fetch_products()
        if not products:
            flash("A API Wake Commerce respondeu, mas nenhum produto foi retornado.", "error")
            return redirect(url_for("admin_dashboard"))
        result = sync_products_from_wake(products)
        wake_categories = sorted({p["categoria"] for p in products})
        if wake_categories:
            CATEGORIES.clear()
            CATEGORIES.extend(wake_categories)
        flash(
            f"Sincronizacao concluida: {result['inserted']} produto(s) novo(s), "
            f"{result['updated']} atualizado(s). "
            f"Total de {len(products)} produto(s) da Wake Commerce. "
            "O estoque do totem foi preservado e segue sendo controlado pelo administrador.",
            "success",
        )
    except (ConnectionError, PermissionError) as exc:
        flash(
            f"Nao foi possivel conectar a Wake Commerce: {exc}",
            "error",
        )
    except Exception as exc:
        app.logger.exception("Falha ao sincronizar com Wake Commerce")
        flash(f"Erro ao sincronizar: {exc}", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reiniciar-sistema", methods=["POST"])
@admin_required
def admin_reset_system():
    """Apaga vendas, dados de clientes, histórico de estoque e zera todos os produtos."""
    if request.form.get("confirm_reset") != "1":
        flash(
            "Para reiniciar o sistema, marque a caixa de confirmação e tente novamente.",
            "error",
        )
        return redirect(url_for("admin_dashboard"))
    try:
        result = reset_totem_to_default_state()
        flash(
            "Sistema reiniciado com sucesso. "
            f"{result['transactions_deleted']} venda(s) e dados de cliente removidos; "
            f"{result['movements_deleted']} movimentação(ões) apagadas; "
            f"estoque de {result['products_restored']} produto(s) zerado no cadastro; "
            f"{result['event_product_pairs_reset']} vínculo(s) produto×evento com saldo zerado "
            "(novos registros de estoque inicial com saldo 0).",
            "success",
        )
    except Exception:
        app.logger.exception("Falha ao reiniciar o sistema")
        flash(
            "Não foi possível reiniciar o sistema. Verifique os logs ou tente novamente.",
            "error",
        )
    return redirect(url_for("admin_dashboard"))


# ---------------------------------------------------------------------------
# Painel administrativo — estoque
# ---------------------------------------------------------------------------

def _admin_shell_context(**extra):
    """Contexto comum para todas as telas do painel (topbar/nav)."""
    return {
        "admin_user": _current_admin_user(),
        "now": datetime.now(),
        **extra,
    }


ALLOWED_ADMIN_STOCK_PER_PAGE = (10, 25, 50, 100)
DEFAULT_ADMIN_STOCK_PER_PAGE = 25


def _admin_stock_list_query_params():
    """Parâmetros GET compartilhados entre biblioteca admin/vendedor e suas APIs JSON."""
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("categoria") or "").strip()
    status = (request.args.get("status") or "").strip()
    per_page = _parse_int(request.args.get("per_page"), DEFAULT_ADMIN_STOCK_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_STOCK_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))
    return q, category, status, per_page, page


def _admin_stock_page_view():
    q_display, category, status, per_page, page = _admin_stock_list_query_params()
    q_lower = q_display.lower() if q_display else ""
    q_filter = q_lower or None
    cat_norm = category or "todos"
    stat_norm = status or "todos"

    total = count_products_admin_filtered(q_filter, cat_norm, stat_norm)
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    products = list_products_admin_slice(
        q_filter,
        cat_norm,
        stat_norm,
        limit=per_page,
        offset=offset,
    )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(products), total) if total > 0 else 0

    filters = {
        "q": q_display,
        "categoria": cat_norm,
        "status": stat_norm,
        "per_page": per_page,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return products, filters, pagination


@app.route("/admin/estoque")
@admin_required
def admin_stock_legacy_redirect():
    qs = request.query_string.decode()
    target = url_for("admin_products")
    return redirect(f"{target}?{qs}" if qs else target, code=302)


@app.route("/admin/produtos")
@admin_required
def admin_products():
    products, filters, pagination = _admin_stock_page_view()
    return render_template(
        "admin/products.html",
        products=products,
        stock=get_products_library_stats(),
        categories=CATEGORIES,
        filters=filters,
        pagination=pagination,
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        **_admin_shell_context(active_section="produtos"),
    )


@app.route("/admin/estoque/<int:product_id>")
@admin_required
def admin_stock_product_legacy_redirect(product_id: int):
    return redirect(url_for("admin_product_detail", product_id=product_id), code=302)


@app.route("/admin/produtos/<int:product_id>")
@admin_required
def admin_product_detail(product_id: int):
    product_base = get_product(product_id)
    if product_base is None:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("admin_products"))
    ev_total = get_product_events_stock_total(product_id)
    product = dict(product_base)
    product["estoque"] = ev_total
    product["abaixo_minimo"] = product["estoque_minimo"] > 0 and ev_total < product["estoque_minimo"]
    product["sem_estoque"] = ev_total <= 0
    movements = list_stock_movements(product_id=product_id, limit=100)
    return render_template(
        "admin/product_detail.html",
        product=product,
        movements=movements,
        **_admin_shell_context(active_section="produtos"),
    )


def _parse_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _wants_json_response() -> bool:
    """Detecta chamadas AJAX/API sem mudar o fluxo HTML existente."""
    return (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "fetch"
        or request.args.get("format") == "json"
    )


def _product_status(product: dict) -> dict:
    if not product["ativo"]:
        return {"label": "Inativo", "kind": "neutral"}
    if product["sem_estoque"]:
        return {"label": "Sem estoque", "kind": "danger"}
    if product["abaixo_minimo"]:
        return {"label": "Abaixo do mínimo", "kind": "warn"}
    return {"label": "OK", "kind": "success"}


def _cro_pedido_fields(row: dict | None) -> dict[str, str] | None:
    """UF e número do registro CRO salvos na transação (formulário de pagamento)."""
    if not row:
        return None

    def _strip_val(v):
        if v is None:
            return ""
        return str(v).strip()

    uf_s = _strip_val(row.get("client_cro_uf"))
    num_s = _strip_val(row.get("client_cro_numero"))
    if not uf_s and not num_s:
        return None
    out: dict[str, str] = {}
    if uf_s:
        out["uf"] = uf_s
    if num_s:
        out["numero_registro"] = num_s
    return out if out else None


def _movement_payload(movement: dict) -> dict:
    reference = movement.get("reference")
    movement_type = movement.get("movement_type")
    tx_id = movement.get("transaction_id")
    ev_bg, ev_fg = event_badge_style_pairs(movement.get("event_badge_color"))
    return {
        **movement,
        "event_badge_bg": ev_bg,
        "event_badge_fg": ev_fg,
        "created_by_display": _display_created_by(movement.get("created_by")),
        "created_at_display": datahora_filter(movement.get("created_at")),
        "movement_label": mov_label_filter(movement_type),
        "delta_display": signed_filter(movement.get("delta")),
        "delta_kind": "positive" if int(movement.get("delta") or 0) > 0 else "negative",
        "product_url": url_for("admin_product_detail", product_id=movement["product_id"]),
        "receipt_url": (
            url_for("receipt", order_number=reference)
            if movement_type == "venda" and reference
            else None
        ),
        "has_customer_details": movement_type == "venda" and bool(tx_id),
        "cro_pedido": _cro_pedido_fields(movement),
    }


def _products_library_detail_payload(product_id: int, *, limit: int = 100) -> dict:
    product_base = get_product(product_id)
    if product_base is None:
        raise ValueError("Produto não encontrado.")
    ev_total = get_product_events_stock_total(product_id)
    product = dict(product_base)
    product["estoque"] = ev_total
    product["abaixo_minimo"] = product["estoque_minimo"] > 0 and ev_total < product["estoque_minimo"]
    product["sem_estoque"] = ev_total <= 0
    movements = list_stock_movements(product_id=product_id, limit=limit)
    return {
        "product": {
            **product,
            "status": _product_status(product),
            "stock_value_display": brl_filter(ev_total * product["preco"]),
        },
        "movements": [_movement_payload(m) for m in movements],
    }


def _json_products_library_success(message: str, product_id: int, status_code: int = 200):
    payload = _products_library_detail_payload(product_id)
    payload["message"] = message
    return jsonify(payload), status_code


def _event_stock_product_payload(event_id: int, product_id: int, *, limit: int = 100) -> dict:
    product = get_product_in_event(event_id, product_id)
    if product is None:
        raise ValueError("Produto não encontrado neste evento.")
    movements = list_event_stock_movements(event_id, product_id=product_id, limit=limit)
    return {
        "product": {
            **product,
            "status": _event_product_status({
                "stock": product["estoque"],
                "min_stock": product["estoque_minimo"],
                "product_active": product["ativo"],
            }),
            "stock_value_display": brl_filter(product["estoque"] * product["preco"]),
        },
        "movements": [_event_movement_payload(m, event_id=event_id) for m in movements],
    }


def _json_event_stock_success(message: str, event_id: int, product_id: int, status_code: int = 200):
    payload = _event_stock_product_payload(event_id, product_id)
    payload["message"] = message
    return jsonify(payload), status_code


@app.route("/admin/produtos/<int:product_id>/ativar", methods=["POST"])
@app.route("/admin/estoque/<int:product_id>/ativar", methods=["POST"])
@admin_required
def admin_product_toggle_active(product_id: int):
    active = request.form.get("active") == "1"
    if set_product_active(product_id, active):
        message = (
            "Produto ativado e disponível no totem." if active
            else "Produto desativado — não aparecerá no totem."
        )
        if _wants_json_response():
            return _json_products_library_success(message, product_id)
        flash(message, "success")
    else:
        if _wants_json_response():
            return jsonify({"error": "Não foi possível atualizar o produto."}), 400
        flash("Não foi possível atualizar o produto.", "error")
    return redirect(request.referrer or url_for("admin_product_detail", product_id=product_id))


def _admin_api_products_list_payload():
    products, _filters, pagination = _admin_stock_page_view()
    return jsonify({
        "stock": get_products_library_stats(),
        "pagination": {
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total": pagination["total"],
            "total_pages": pagination["total_pages"],
        },
        "products": [
            {**p, "status": _product_status(p)}
            for p in products
        ],
    })


@app.route("/admin/api/produtos")
@admin_required
def admin_api_products():
    return _admin_api_products_list_payload()


@app.route("/admin/api/estoque")
@admin_required
def admin_api_stock_legacy():
    return _admin_api_products_list_payload()


@app.route("/admin/api/produtos/<int:product_id>")
@admin_required
def admin_api_products_detail(product_id: int):
    try:
        return jsonify(_products_library_detail_payload(product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/admin/api/estoque/<int:product_id>")
@admin_required
def admin_api_stock_product_legacy(product_id: int):
    try:
        return jsonify(_products_library_detail_payload(product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/admin/api/movimentacoes")
@admin_required
def admin_api_movements():
    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    pedido = (request.args.get("pedido") or "").strip()
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 else None
    if seller_filter is not None:
        known = {int(s["id"]) for s in list_sellers()}
        if seller_filter not in known:
            seller_filter = None

    movements = list_stock_movements(
        product_search=q or None,
        movement_type=movement_type or None,
        reference=pedido or None,
        seller_id=seller_filter,
        limit=500,
    )
    return jsonify({
        "movements": [_movement_payload(m) for m in movements],
        "latest_id": max([int(m["id"]) for m in movements], default=0),
    })


@app.route("/admin/movimentacoes")
@admin_required
def admin_movements():
    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    # Código do pedido (``reference`` nas movimentações de venda do totem, ex.: OM260422-1234)
    pedido = (request.args.get("pedido") or "").strip()
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 else None
    if seller_filter is not None:
        known = {int(s["id"]) for s in list_sellers()}
        if seller_filter not in known:
            seller_filter = None
            seller_raw = 0

    movements = list_stock_movements(
        product_search=q or None,
        movement_type=movement_type or None,
        reference=pedido or None,
        seller_id=seller_filter,
        limit=500,
    )
    movement_filter_sellers = list_sellers()
    return render_template(
        "admin/stock_movements.html",
        movements=movements,
        movement_filter_sellers=movement_filter_sellers,
        filters={
            "tipo": movement_type or "todos",
            "q": q,
            "pedido": pedido,
            "vendedor": seller_raw,
        },
        **_admin_shell_context(active_section="movimentacoes"),
    )


_EXPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _csv_cell(value):
    if value is None:
        return ""
    return value


def _csv_attachment_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    body = buf.getvalue().encode("utf-8-sig")
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _movement_export_table(movements: list[dict]) -> tuple[list[str], list[list]]:
    header = [
        "id_movimento",
        "data_hora",
        "tipo",
        "tipo_descricao",
        "produto_id",
        "produto_nome",
        "categoria",
        "quantidade",
        "delta",
        "saldo_apos",
        "evento_id",
        "evento_nome",
        "referencia",
        "motivo",
        "transacao_id",
        "registrado_por",
        "custo_unitario",
    ]
    rows: list[list] = []
    for m in movements:
        mt = m.get("movement_type")
        rows.append(
            [
                _csv_cell(m.get("id")),
                _csv_cell(m.get("created_at")),
                _csv_cell(mt),
                mov_label_filter(mt),
                _csv_cell(m.get("product_id")),
                _csv_cell(m.get("product_name")),
                _csv_cell(m.get("product_category")),
                _csv_cell(m.get("quantity")),
                _csv_cell(m.get("delta")),
                _csv_cell(m.get("balance_after")),
                _csv_cell(m.get("event_id")),
                _csv_cell(m.get("event_name")),
                _csv_cell(m.get("reference")),
                _csv_cell(m.get("reason")),
                _csv_cell(m.get("transaction_id")),
                _csv_cell(m.get("created_by")),
                _csv_cell(m.get("unit_cost")),
            ]
        )
    return header, rows


@app.route("/admin/movimentacoes/export.csv")
@admin_required
def admin_movements_export_csv():
    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    pedido = (request.args.get("pedido") or "").strip()
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 else None
    if seller_filter is not None:
        known = {int(s["id"]) for s in list_sellers()}
        if seller_filter not in known:
            seller_filter = None

    movements = list_stock_movements(
        product_search=q or None,
        movement_type=movement_type or None,
        reference=pedido or None,
        seller_id=seller_filter,
        limit=EXPORT_MOVEMENTS_CSV_CAP,
    )
    header, rows = _movement_export_table(movements)
    fname = f"movimentacoes_admin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_attachment_response(fname, header, rows)


@app.route("/admin/eventos/<int:event_id>/movimentacoes/export.csv")
@admin_required
def admin_event_movements_export_csv(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    event_sellers_rows = list_event_sellers(event_id)
    event_seller_ids = {int(s["id"]) for s in event_sellers_rows}
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else None

    movements = list_event_stock_movements(
        event_id,
        product_id=None,
        product_search=q or None,
        movement_type=movement_type or None,
        seller_id=seller_filter,
        limit=EXPORT_MOVEMENTS_CSV_CAP,
    )
    header, rows = _movement_export_table(movements)
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)
    fname = f"movimentacoes_evento_{event_id}_{safe_ev}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_attachment_response(fname, header, rows)


@app.route("/admin/eventos/<int:event_id>/vendas/export.csv")
@admin_required
def admin_event_sales_export_csv(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    raw_from = (request.args.get("data_inicio") or "").strip()
    raw_to = (request.args.get("data_fim") or "").strip()
    for label, raw in (("Data inicial", raw_from), ("Data final", raw_to)):
        if raw and not _EXPORT_DATE_RE.fullmatch(raw):
            flash(f"{label}: use o formato AAAA-MM-DD.", "error")
            return redirect(url_for("admin_event_detail", event_id=event_id))

    nivel = (request.args.get("nivel") or "pedidos").strip().lower()
    if nivel not in ("pedidos", "itens"):
        nivel = "pedidos"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)

    if nivel == "pedidos":
        rows_data = list_transactions_summary_for_event_period(
            event_id,
            date_from=raw_from or None,
            date_to=raw_to or None,
        )
        header = [
            "pedido_id",
            "codigo_pedido",
            "data_hora",
            "total",
            "qtd_itens",
            "status",
            "cliente_nome",
            "cliente_cpf",
            "cliente_cep",
            "cliente_endereco",
            "cliente_numero",
            "cliente_complemento",
            "cliente_cidade",
            "cliente_estado",
            "vendedor_id",
            "vendedor_nome",
            "forma_pagamento",
            "cro_uf",
            "cro_numero_registro",
        ]
        rows = []
        for t in rows_data:
            rows.append(
                [
                    _csv_cell(t.get("id")),
                    _csv_cell(t.get("order_number")),
                    _csv_cell(t.get("created_at")),
                    _csv_cell(t.get("total")),
                    _csv_cell(t.get("items_count")),
                    _csv_cell(t.get("status")),
                    _csv_cell(t.get("client_name")),
                    _csv_cell(t.get("client_cpf")),
                    _csv_cell(t.get("client_zipcode")),
                    _csv_cell(t.get("client_address")),
                    _csv_cell(t.get("client_number")),
                    _csv_cell(t.get("client_complement")),
                    _csv_cell(t.get("client_city")),
                    _csv_cell(t.get("client_state")),
                    _csv_cell(t.get("seller_id")),
                    _csv_cell(t.get("seller_name")),
                    _csv_cell(t.get("payment_method")),
                    _csv_cell(t.get("client_cro_uf")),
                    _csv_cell(t.get("client_cro_numero")),
                ]
            )
        fname = f"vendas_evento_{event_id}_{safe_ev}_pedidos_{ts}.csv"
    else:
        rows_data = list_transaction_items_for_event_period(
            event_id,
            date_from=raw_from or None,
            date_to=raw_to or None,
        )
        header = [
            "item_id",
            "pedido_id",
            "codigo_pedido",
            "data_hora_pedido",
            "vendedor_id",
            "vendedor_nome",
            "forma_pagamento",
            "produto_id",
            "produto_nome",
            "categoria",
            "sku",
            "quantidade",
            "preco_unitario",
            "subtotal",
        ]
        rows = []
        for ti in rows_data:
            rows.append(
                [
                    _csv_cell(ti.get("item_id")),
                    _csv_cell(ti.get("transaction_id")),
                    _csv_cell(ti.get("order_number")),
                    _csv_cell(ti.get("created_at")),
                    _csv_cell(ti.get("seller_id")),
                    _csv_cell(ti.get("seller_name")),
                    _csv_cell(ti.get("payment_method")),
                    _csv_cell(ti.get("product_id")),
                    _csv_cell(ti.get("product_name")),
                    _csv_cell(ti.get("category")),
                    _csv_cell(ti.get("product_sku")),
                    _csv_cell(ti.get("quantity")),
                    _csv_cell(ti.get("unit_price")),
                    _csv_cell(ti.get("subtotal")),
                ]
            )
        fname = f"vendas_evento_{event_id}_{safe_ev}_itens_{ts}.csv"

    return _csv_attachment_response(fname, header, rows)


# ---------------------------------------------------------------------------
# Eventos de estoque — helpers
# ---------------------------------------------------------------------------

def _event_or_404(event_id: int):
    """Retorna o evento ou faz flash+redirect para a lista."""
    ev = get_event(event_id)
    if ev is None:
        flash("Evento não encontrado.", "error")
    return ev


def _event_badge_color_from_form(form) -> str | None:
    """Interpreta checkbox \"cor padrão\" + color picker do formulário de evento."""
    use_default = (form.get("badge_use_default") or "").strip().lower() in ("1", "on", "true", "yes")
    if use_default:
        return None
    return normalize_event_badge_color((form.get("badge_color") or "").strip())


def _event_subnav_context(event_id: int, active_tab: str) -> dict:
    """Contexto compartilhado para todas as sub-páginas do evento."""
    ev = get_event(event_id)
    stats = get_event_stock_stats(event_id) if ev else {}
    return {
        "event": ev,
        "stats": stats,
        "event_id": event_id,
        "active_event_tab": active_tab,
    }


def _event_movement_payload(movement: dict, *, event_id: int) -> dict:
    """Serializa uma movimentação de evento para JSON (polling / live forms)."""
    reference = movement.get("reference")
    movement_type = movement.get("movement_type")
    tx_id = movement.get("transaction_id")
    ev_bg, ev_fg = event_badge_style_pairs(movement.get("event_badge_color"))
    return {
        **movement,
        "event_badge_bg": ev_bg,
        "event_badge_fg": ev_fg,
        "created_by_display": _display_created_by(movement.get("created_by")),
        "created_at_display": datahora_filter(movement.get("created_at")),
        "movement_label": mov_label_filter(movement_type),
        "delta_display": signed_filter(movement.get("delta")),
        "delta_kind": "positive" if int(movement.get("delta") or 0) > 0 else "negative",
        "product_url": url_for("admin_product_detail", product_id=int(movement["product_id"])),
        "receipt_url": (
            url_for("receipt", order_number=reference)
            if movement_type == "venda" and reference
            else None
        ),
        "has_customer_details": movement_type == "venda" and bool(tx_id),
        "cro_pedido": _cro_pedido_fields(movement),
    }


def _event_product_status(ep: dict) -> dict:
    """Calcula situação de um produto dentro do evento."""
    if not ep.get("product_active", True):
        return {"label": "Inativo no catálogo", "kind": "neutral"}
    if int(ep.get("stock") or 0) == 0:
        return {"label": "Sem estoque", "kind": "danger"}
    if int(ep.get("min_stock") or 0) > 0 and int(ep["stock"]) < int(ep["min_stock"]):
        return {"label": "Abaixo do mínimo", "kind": "warn"}
    return {"label": "OK", "kind": "success"}


# ---------------------------------------------------------------------------
# Eventos de estoque — lista e CRUD principal
# ---------------------------------------------------------------------------

@app.route("/admin/eventos")
@admin_required
def admin_events():
    active = list_events(include_archived=False)
    archived = list_events(include_archived=True)
    archived = [e for e in archived if not e["active"]]
    return render_template(
        "admin/events.html",
        events=active,
        events_archived=archived,
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/novo", methods=["POST"])
@admin_required
def admin_event_create():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("O nome do evento é obrigatório.", "error")
        return redirect(url_for("admin_events"))
    badge_color = _event_badge_color_from_form(request.form)
    event_id = create_event(name, description, badge_color=badge_color)
    flash(f"Evento \"{name}\" criado com sucesso.", "success")
    return redirect(url_for("admin_event_detail", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>")
@admin_required
def admin_event_detail(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    stats = get_event_stock_stats(event_id)
    sales_dashboard = get_event_sales_dashboard(event_id)
    recent_movements = list_event_stock_movements(event_id, limit=5)
    sellers = list_event_sellers(event_id)
    return render_template(
        "admin/event_detail.html",
        event=event,
        stats=stats,
        sales_dashboard=sales_dashboard,
        recent_movements=recent_movements,
        sellers=sellers,
        active_event_tab="dashboard",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/editar", methods=["POST"])
@admin_required
def admin_event_edit(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("O nome do evento é obrigatório.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    badge_color = _event_badge_color_from_form(request.form)
    update_event(event_id, name, description, badge_color=badge_color)
    flash("Evento atualizado.", "success")
    return redirect(url_for("admin_event_detail", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/arquivar", methods=["POST"])
@admin_required
def admin_event_archive(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    archive_event(event_id)
    flash(f"Evento \"{event['name']}\" arquivado.", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/eventos/<int:event_id>/restaurar", methods=["POST"])
@admin_required
def admin_event_restore(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    restore_event(event_id)
    flash(f"Evento \"{event['name']}\" reativado.", "success")
    return redirect(url_for("admin_events"))


# ---------------------------------------------------------------------------
# Eventos — sub-página Estoque
# ---------------------------------------------------------------------------

@app.route("/admin/eventos/<int:event_id>/estoque")
@admin_required
def admin_event_stock(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    products = list_event_products(event_id)
    stats = get_event_stock_stats(event_id)
    return render_template(
        "admin/event_stock.html",
        event=event,
        products=products,
        stats=stats,
        active_event_tab="estoque",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/estoque/<int:product_id>")
@admin_required
def admin_event_stock_product(event_id: int, product_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    product = get_product_in_event(event_id, product_id)
    if product is None:
        flash("Produto não encontrado neste evento.", "error")
        return redirect(url_for("admin_event_stock", event_id=event_id))
    movements = list_event_stock_movements(event_id, product_id=product_id, limit=100)
    return render_template(
        "admin/event_stock_product.html",
        event=event,
        product=product,
        movements=movements,
        active_event_tab="estoque",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/produtos/adicionar", methods=["POST"])
@admin_required
def admin_event_add_product(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    q = (request.form.get("sku_or_id") or "").strip()
    if not q:
        flash("Informe o SKU ou ID do produto.", "error")
        return redirect(url_for("admin_event_stock", event_id=event_id))
    product = find_product_by_sku_or_id(q)
    if product is None:
        flash(f"Produto \"{q}\" não encontrado.", "error")
        return redirect(url_for("admin_event_stock", event_id=event_id))
    try:
        add_product_to_event(event_id, int(product["id"]), 0, 0)
        flash(f"Produto \"{product['name']}\" adicionado ao evento.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_event_stock", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/remover", methods=["POST"])
@admin_required
def admin_event_remove_product(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    remove_product_from_event(event_id, product_id)
    flash("Produto removido do evento.", "success")
    return redirect(url_for("admin_event_stock", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/entrada", methods=["POST"])
@admin_required
def admin_event_stock_entry(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    qty = _parse_int(request.form.get("quantity"), 0)
    unit_cost = _parse_float(request.form.get("unit_cost"))
    reason = (request.form.get("reason") or "").strip() or None
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    try:
        register_event_stock_entry(
            event_id,
            product_id,
            qty,
            unit_cost=unit_cost,
            reason=reason,
            created_by=_current_admin_user(),
        )
        message = f"Entrada de {qty} un. registrada."
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return redirect(request.referrer or fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/saida", methods=["POST"])
@admin_required
def admin_event_stock_exit(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    qty = _parse_int(request.form.get("quantity"), 0)
    reason = (request.form.get("reason") or "").strip()
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    try:
        register_event_stock_exit(
            event_id,
            product_id,
            qty,
            reason=reason or "Saída manual",
            created_by=_current_admin_user(),
        )
        message = f"Saída de {qty} un. registrada."
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return redirect(request.referrer or fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/ajuste", methods=["POST"])
@admin_required
def admin_event_stock_adjust(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    new_qty = _parse_int(request.form.get("new_stock"), 0)
    reason = (request.form.get("reason") or "").strip()
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    try:
        register_event_stock_adjustment(
            event_id,
            product_id,
            new_qty,
            reason=reason or "Ajuste manual",
            created_by=_current_admin_user(),
        )
        message = f"Estoque ajustado para {new_qty} un."
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return redirect(request.referrer or fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/minimo", methods=["POST"])
@admin_required
def admin_event_stock_min(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    min_val = _parse_int(request.form.get("min_stock"), 0)
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    update_event_product_stock(
        event_id,
        product_id,
        _get_event_product_stock(event_id, product_id),
        min_val,
    )
    message = f"Estoque mínimo atualizado para {max(0, min_val)} un."
    if _wants_json_response():
        return _json_event_stock_success(message, event_id, product_id)
    flash(message, "success")
    return redirect(request.referrer or fallback)


def _get_event_product_stock(event_id: int, product_id: int) -> int:
    """Lê o saldo atual de um produto no evento (helper interno)."""
    for ep in list_event_products(event_id):
        if int(ep["product_id"]) == int(product_id):
            return int(ep["stock"] or 0)
    return 0


# ---------------------------------------------------------------------------
# Eventos — sub-página Movimentações
# ---------------------------------------------------------------------------

@app.route("/admin/eventos/<int:event_id>/movimentacoes")
@admin_required
def admin_event_movements(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    event_sellers_rows = list_event_sellers(event_id)
    event_seller_ids = {int(s["id"]) for s in event_sellers_rows}
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else None
    if seller_raw > 0 and seller_filter is None:
        seller_raw = 0

    movements = list_event_stock_movements(
        event_id,
        product_id=None,
        product_search=q or None,
        movement_type=movement_type or None,
        seller_id=seller_filter,
        limit=300,
    )
    stats = get_event_stock_stats(event_id)
    return render_template(
        "admin/event_movements.html",
        event=event,
        movements=movements,
        stats=stats,
        movement_filter_sellers=event_sellers_rows,
        filters={
            "tipo": movement_type or "todos",
            "q": q,
            "vendedor": seller_raw,
        },
        active_event_tab="movimentacoes",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/api/eventos/<int:event_id>/movimentacoes")
@admin_required
def admin_api_event_movements(event_id: int):
    movement_type = (request.args.get("tipo") or "").strip()
    q = (request.args.get("q") or "").strip()
    event_seller_ids = {int(s["id"]) for s in list_event_sellers(event_id)}
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else None

    movements = list_event_stock_movements(
        event_id,
        product_id=None,
        product_search=q or None,
        movement_type=movement_type or None,
        seller_id=seller_filter,
        limit=300,
    )
    return jsonify({
        "movements": [_event_movement_payload(m, event_id=event_id) for m in movements],
        "latest_id": max([int(m["id"]) for m in movements], default=0),
    })


# ---------------------------------------------------------------------------
# Eventos — sub-página Vendedores
# ---------------------------------------------------------------------------

@app.route("/admin/eventos/<int:event_id>/vendedores")
@admin_required
def admin_event_sellers(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    sellers = list_event_sellers(event_id)
    available = list_sellers_not_in_event(event_id)
    stats = get_event_stock_stats(event_id)
    return render_template(
        "admin/event_sellers.html",
        event=event,
        sellers=sellers,
        available_sellers=available,
        stats=stats,
        active_event_tab="vendedores",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/vendedores/adicionar", methods=["POST"])
@admin_required
def admin_event_add_seller(event_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    seller_id = _parse_int(request.form.get("seller_id"), 0)
    if not seller_id:
        flash("Selecione um vendedor.", "error")
        return redirect(url_for("admin_event_sellers", event_id=event_id))
    add_seller_to_event(event_id, seller_id)
    flash("Vendedor adicionado ao evento.", "success")
    return redirect(url_for("admin_event_sellers", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/vendedores/<int:seller_id>/remover", methods=["POST"])
@admin_required
def admin_event_remove_seller(event_id: int, seller_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    remove_seller_from_event(event_id, seller_id)
    flash("Vendedor removido do evento.", "success")
    return redirect(url_for("admin_event_sellers", event_id=event_id))


# ---------------------------------------------------------------------------
# Eventos — API de polling (estoque)
# ---------------------------------------------------------------------------

@app.route("/admin/api/eventos/<int:event_id>/estoque")
@admin_required
def admin_api_event_stock(event_id: int):
    products = list_event_products(event_id)
    return jsonify({
        "stats": get_event_stock_stats(event_id),
        "products": [
            {
                **p,
                "id": p["product_id"],
                "estoque": p["stock"],
                "estoque_minimo": p["min_stock"],
                "status": _event_product_status(p),
            }
            for p in products
        ],
    })


@app.route("/admin/api/eventos/<int:event_id>/estoque/<int:product_id>")
@admin_required
def admin_api_event_stock_product(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return jsonify({"error": "Evento não encontrado."}), 404
    try:
        return jsonify(_event_stock_product_payload(event_id, product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


# ---------------------------------------------------------------------------
# Nota não fiscal
# ---------------------------------------------------------------------------

@app.route("/nota/<path:order_number>")
def receipt(order_number: str):
    """Exibe a nota não fiscal de uma transação (identificada pelo número do pedido)."""
    tx = get_transaction_by_order_number(order_number)
    autoprint = request.args.get("print", "").lower() in ("1", "true", "yes")
    if tx is None:
        return render_template(
            "nota.html",
            tx=None,
            order_number=order_number,
            autoprint=False,
        ), 404
    return render_template(
        "nota.html",
        tx=tx,
        order_number=order_number,
        autoprint=autoprint,
    )


# ---------------------------------------------------------------------------
# Filtros Jinja
# ---------------------------------------------------------------------------

@app.template_filter("brl")
def brl_filter(value):
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    formatted = f"{n:,.2f}"
    formatted = formatted.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {formatted}"


@app.template_filter("datahora")
def datahora_filter(value):
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return dt.strftime("%d/%m/%Y %H:%M")


@app.template_filter("paymethod")
def paymethod_filter(value):
    return _payment_method_label(value)


@app.template_filter("cro_pedido")
def cro_pedido_filter(row):
    """Extrai UF e número do registro CRO da linha de movimentação / transação."""
    return _cro_pedido_fields(row if isinstance(row, dict) else None)


@app.template_filter("signed")
def signed_filter(value):
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return f"+{n}"
    return str(n)


@app.template_filter("movlabel")
def mov_label_filter(value):
    return {
        "entrada": "Entrada",
        "saida": "Saída",
        "venda": "Venda",
        "ajuste": "Ajuste",
        "inicial": "Estoque inicial",
    }.get(value, value or "-")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
