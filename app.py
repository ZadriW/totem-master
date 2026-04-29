"""Totem Odonto Master - aplicação Flask (dashboard do cliente + painel admin).

- ``/``                Tela de boas-vindas (reseta a sessão do cliente).
- ``/catalogo``        Catálogo para o cliente.
- ``/pagamento``       Resumo do pedido.
- ``/pagamento/aguardando``  Aguardo da maquininha + confirmação da venda.
- ``/api/...``         Endpoints JSON consumidos pelo front.
- ``/admin/...``       Painel administrativo autenticado (dashboard, estoque,
                       movimentações).
- ``/vendedor/...``    Painel dos vendedores (somente leitura).
"""

from __future__ import annotations

import os
import secrets
from functools import wraps
from datetime import datetime

from flask import (
    Flask,
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
    create_seller_account,
    create_transaction,
    delete_seller,
    ensure_seller_account,
    get_seller,
    get_seller_by_email,
    get_product,
    get_stats,
    get_stock_stats,
    get_transaction_by_order_number,
    init_db,
    list_seller_pin_hashes,
    list_sellers,
    list_products_admin,
    list_products_for_client,
    list_stock_movements,
    list_transactions,
    register_stock_adjustment,
    register_stock_entry,
    register_stock_exit,
    reset_totem_to_default_state,
    set_product_active,
    sync_products_from_wake,
    update_seller_account,
    update_seller_last_login,
    update_product_min_stock,
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


def _display_created_by(value) -> str:
    """Texto exibível do campo created_by (remove prefixo interno ``vendedor:``)."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower().startswith("vendedor:"):
        s = s[9:].strip()
    return s


# Registro imediato: garante o filtro Jinja mesmo com importações parciais / reload.
app.add_template_filter(_display_created_by, "display_created_by")

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


def _seller_for_pin(pin: str) -> dict | None:
    value = _normalize_seller_pin(pin)
    for seller in list_seller_pin_hashes():
        if seller.get("active") and check_password_hash(seller["pin_hash"], value):
            return seller
    return None


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
    return render_template(
        "catalog.html",
        categories=CATEGORIES,
        products=list_products_for_client(),
    )


@app.route("/pagamento")
def payment():
    """Resumo do pedido antes de ir à maquininha."""
    return render_template("payment.html")


@app.route("/pagamento/aguardando")
def payment_waiting():
    """Tela de aguardo enquanto o cliente usa a maquininha física."""
    return render_template("payment_waiting.html")


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
    try:
        seller_pin = _normalize_seller_pin(str(payload.get("seller_pin") or ""))
        seller = _seller_for_pin(seller_pin)
        if seller is None:
            raise ValueError("PIN do vendedor inválido ou inativo.")
        result = create_transaction(
            items,
            created_by=f"vendedor:{seller['name']}",
            seller_id=int(seller["id"]),
            seller_name=seller["name"],
            client_name=client.get("name"),
            client_cpf=client.get("cpf"),
            client_zipcode=client.get("zipcode"),
            client_address=client.get("address"),
            client_number=client.get("number"),
            client_complement=client.get("complement"),
            client_city=client.get("city"),
            client_state=client.get("state"),
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
    return {
        "seller_name": auth.get("seller_name", "Vendedor"),
        "seller_email": auth.get("seller_email", ""),
        "now": datetime.now(),
        **extra,
    }


def _filtered_admin_products():
    q = (request.args.get("q") or "").strip().lower()
    category = (request.args.get("categoria") or "").strip()
    status = (request.args.get("status") or "").strip()

    products = list_products_admin()
    if q:
        products = [
            p for p in products
            if q in p["nome"].lower()
            or q in (p["descricao"] or "").lower()
            or q in (p.get("sku") or "").lower()
        ]
    if category and category.lower() != "todos":
        products = [p for p in products if p["categoria"].lower() == category.lower()]
    if status == "baixo":
        products = [p for p in products if p["abaixo_minimo"] and not p["sem_estoque"]]
    elif status == "sem_estoque":
        products = [p for p in products if p["sem_estoque"]]
    elif status == "inativo":
        products = [p for p in products if not p["ativo"]]

    return products, {
        "q": q,
        "categoria": category or "todos",
        "status": status or "todos",
    }


@app.route("/vendedor/dashboard")
@seller_required
def seller_dashboard():
    seller_id = _current_seller_id()
    stats = get_stats(seller_id=seller_id)
    stock = get_stock_stats()
    transactions = list_transactions(limit=100, seller_id=seller_id)
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
    products, filters = _filtered_admin_products()
    return render_template(
        "seller/stock.html",
        products=products,
        stock=get_stock_stats(),
        categories=CATEGORIES,
        filters=filters,
        **_seller_shell_context(active_section="estoque"),
    )


@app.route("/vendedor/movimentacoes")
@seller_required
def seller_movements():
    movement_type = (request.args.get("tipo") or "").strip()
    product_id_raw = request.args.get("produto") or ""
    product_id = _parse_int(product_id_raw, 0) or None
    pedido = (request.args.get("pedido") or "").strip()

    movements = list_stock_movements(
        product_id=product_id,
        movement_type=movement_type or None,
        reference=pedido or None,
        limit=500,
    )
    return render_template(
        "seller/movements.html",
        movements=movements,
        products=list_products_admin(),
        filters={
            "tipo": movement_type or "todos",
            "produto": product_id or 0,
            "pedido": pedido,
        },
        **_seller_shell_context(active_section="movimentacoes"),
    )


def _seller_transaction_api(tx: dict) -> dict:
    """Serializa uma transação para `/vendedor/api/dashboard` (somente leitura)."""
    items = tx.get("items") or []
    return {
        "id": int(tx["id"]),
        "order_number": tx["order_number"],
        "created_at_display": datahora_filter(tx["created_at"]),
        "items_count": int(tx["items_count"] or 0),
        "total_display": brl_filter(tx["total"]),
        "status": tx["status"],
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
    products = list_products_admin()
    return jsonify({
        "stock": get_stock_stats(),
        "products": [
            {**p, "status": _product_status(p)}
            for p in products
        ],
    })


@app.route("/vendedor/api/dashboard")
@seller_required
def seller_api_dashboard():
    seller_id = _current_seller_id()
    transactions = list_transactions(limit=100, seller_id=seller_id)
    latest_tx_id = max((int(t["id"]) for t in transactions), default=0)
    return jsonify({
        "stats": get_stats(seller_id=seller_id),
        "stock": get_stock_stats(),
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
    stock = get_stock_stats()
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
            f"estoque de {result['products_restored']} produto(s) zerado "
            "(novo registro de estoque inicial com saldo 0).",
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


@app.route("/admin/estoque")
@admin_required
def admin_stock():
    q = (request.args.get("q") or "").strip().lower()
    category = (request.args.get("categoria") or "").strip()
    status = (request.args.get("status") or "").strip()  # todos|baixo|sem_estoque|inativo

    products = list_products_admin()

    if q:
        products = [
            p for p in products
            if q in p["nome"].lower()
            or q in (p["descricao"] or "").lower()
            or q in (p.get("sku") or "").lower()
        ]
    if category and category.lower() != "todos":
        products = [p for p in products if p["categoria"].lower() == category.lower()]
    if status == "baixo":
        products = [p for p in products if p["abaixo_minimo"] and not p["sem_estoque"]]
    elif status == "sem_estoque":
        products = [p for p in products if p["sem_estoque"]]
    elif status == "inativo":
        products = [p for p in products if not p["ativo"]]

    return render_template(
        "admin/stock.html",
        products=products,
        stock=get_stock_stats(),
        categories=CATEGORIES,
        filters={"q": q, "categoria": category or "todos", "status": status or "todos"},
        **_admin_shell_context(active_section="estoque"),
    )


@app.route("/admin/estoque/<int:product_id>")
@admin_required
def admin_stock_product(product_id: int):
    product = get_product(product_id)
    if product is None:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("admin_stock"))
    movements = list_stock_movements(product_id=product_id, limit=100)
    return render_template(
        "admin/stock_product.html",
        product=product,
        movements=movements,
        **_admin_shell_context(active_section="estoque"),
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


def _movement_payload(movement: dict) -> dict:
    reference = movement.get("reference")
    movement_type = movement.get("movement_type")
    tx_id = movement.get("transaction_id")
    return {
        **movement,
        "created_by_display": _display_created_by(movement.get("created_by")),
        "created_at_display": datahora_filter(movement.get("created_at")),
        "movement_label": mov_label_filter(movement_type),
        "delta_display": signed_filter(movement.get("delta")),
        "delta_kind": "positive" if int(movement.get("delta") or 0) > 0 else "negative",
        "product_url": url_for("admin_stock_product", product_id=movement["product_id"]),
        "receipt_url": (
            url_for("receipt", order_number=reference)
            if movement_type == "venda" and reference
            else None
        ),
        "has_customer_details": movement_type == "venda" and bool(tx_id),
    }


def _stock_product_payload(product_id: int, *, limit: int = 100) -> dict:
    product = get_product(product_id)
    if product is None:
        raise ValueError("Produto não encontrado.")
    movements = list_stock_movements(product_id=product_id, limit=limit)
    return {
        "product": {
            **product,
            "status": _product_status(product),
            "stock_value_display": brl_filter(product["estoque"] * product["preco"]),
        },
        "movements": [_movement_payload(m) for m in movements],
    }


def _json_stock_success(message: str, product_id: int, status_code: int = 200):
    payload = _stock_product_payload(product_id)
    payload["message"] = message
    return jsonify(payload), status_code


@app.route("/admin/estoque/<int:product_id>/entrada", methods=["POST"])
@admin_required
def admin_stock_entry(product_id: int):
    quantity = _parse_int(request.form.get("quantity"), 0)
    unit_cost = _parse_float(request.form.get("unit_cost"))
    reason = (request.form.get("reason") or "").strip() or None
    try:
        register_stock_entry(
            product_id,
            quantity,
            unit_cost=unit_cost,
            reason=reason,
            created_by=_current_admin_user(),
        )
        message = f"Entrada de {quantity} un. registrada."
        if _wants_json_response():
            return _json_stock_success(message, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/estoque/<int:product_id>/saida", methods=["POST"])
@admin_required
def admin_stock_exit(product_id: int):
    quantity = _parse_int(request.form.get("quantity"), 0)
    reason = (request.form.get("reason") or "").strip()
    try:
        register_stock_exit(
            product_id,
            quantity,
            reason=reason,
            created_by=_current_admin_user(),
        )
        message = f"Saída de {quantity} un. registrada."
        if _wants_json_response():
            return _json_stock_success(message, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/estoque/<int:product_id>/ajuste", methods=["POST"])
@admin_required
def admin_stock_adjust(product_id: int):
    new_stock = _parse_int(request.form.get("new_stock"), -1)
    reason = (request.form.get("reason") or "").strip()
    try:
        register_stock_adjustment(
            product_id,
            new_stock,
            reason=reason,
            created_by=_current_admin_user(),
        )
        message = f"Estoque ajustado para {new_stock} un."
        if _wants_json_response():
            return _json_stock_success(message, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/estoque/<int:product_id>/minimo", methods=["POST"])
@admin_required
def admin_stock_min(product_id: int):
    min_stock = _parse_int(request.form.get("min_stock"), 0)
    if update_product_min_stock(product_id, min_stock):
        message = f"Estoque mínimo atualizado para {max(0, min_stock)} un."
        if _wants_json_response():
            return _json_stock_success(message, product_id)
        flash(message, "success")
    else:
        if _wants_json_response():
            return jsonify({"error": "Não foi possível atualizar o estoque mínimo."}), 400
        flash("Não foi possível atualizar o estoque mínimo.", "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/estoque/<int:product_id>/ativar", methods=["POST"])
@admin_required
def admin_stock_toggle_active(product_id: int):
    active = request.form.get("active") == "1"
    if set_product_active(product_id, active):
        message = (
            "Produto ativado e disponível no totem." if active
            else "Produto desativado — não aparecerá no totem."
        )
        if _wants_json_response():
            return _json_stock_success(message, product_id)
        flash(message, "success")
    else:
        if _wants_json_response():
            return jsonify({"error": "Não foi possível atualizar o produto."}), 400
        flash("Não foi possível atualizar o produto.", "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/api/estoque/<int:product_id>")
@admin_required
def admin_api_stock_product(product_id: int):
    try:
        return jsonify(_stock_product_payload(product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/admin/api/estoque")
@admin_required
def admin_api_stock():
    products = list_products_admin()
    return jsonify({
        "stock": get_stock_stats(),
        "products": [
            {
                **p,
                "status": _product_status(p),
            }
            for p in products
        ],
    })


@app.route("/admin/api/movimentacoes")
@admin_required
def admin_api_movements():
    movement_type = (request.args.get("tipo") or "").strip()
    product_id_raw = request.args.get("produto") or ""
    product_id = _parse_int(product_id_raw, 0) or None
    pedido = (request.args.get("pedido") or "").strip()

    movements = list_stock_movements(
        product_id=product_id,
        movement_type=movement_type or None,
        reference=pedido or None,
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
    product_id_raw = request.args.get("produto") or ""
    product_id = _parse_int(product_id_raw, 0) or None
    # Código do pedido (``reference`` nas movimentações de venda do totem, ex.: OM260422-1234)
    pedido = (request.args.get("pedido") or "").strip()

    movements = list_stock_movements(
        product_id=product_id,
        movement_type=movement_type or None,
        reference=pedido or None,
        limit=500,
    )
    products = list_products_admin()
    return render_template(
        "admin/stock_movements.html",
        movements=movements,
        products=products,
        filters={
            "tipo": movement_type or "todos",
            "produto": product_id or 0,
            "pedido": pedido,
        },
        **_admin_shell_context(active_section="movimentacoes"),
    )


# ---------------------------------------------------------------------------
# Nota não fiscal
# ---------------------------------------------------------------------------

@app.route("/nota/<path:order_number>")
def receipt(order_number: str):
    """Exibe a nota não fiscal de uma transação (identificada pelo número do pedido)."""
    tx = get_transaction_by_order_number(order_number)
    if tx is None:
        return render_template("nota.html", tx=None, order_number=order_number), 404
    return render_template("nota.html", tx=tx, order_number=order_number)


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
