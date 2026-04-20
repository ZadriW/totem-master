"""Totem Odonto Master - aplicação Flask (dashboard do cliente + painel admin).

- ``/``           Tela de boas-vindas (reseta a sessão do cliente).
- ``/catalogo``   Catálogo para o cliente.
- ``/pagamento``  Resumo do pedido.
- ``/pagamento/aguardando``  Aguardo da maquininha + confirmação da venda.
- ``/api/...``    Endpoints JSON consumidos pelo front.
- ``/admin/...``  Painel administrativo autenticado.
"""

from __future__ import annotations

import os
import secrets
from functools import wraps
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from data.products import CATEGORIES, get_products
from database import (
    create_transaction,
    get_stats,
    init_db,
    list_transactions,
)


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

# Inicializa o schema quando o app sobe.
init_db()


def _is_admin_logged_in() -> bool:
    return bool(session.get("is_admin"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_admin_logged_in():
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


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
        products=get_products(),
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

    products = get_products()
    if category and category.lower() != "todos":
        products = [p for p in products if p["categoria"].lower() == category.lower()]
    if query:
        products = [
            p for p in products
            if query in p["nome"].lower() or query in p["descricao"].lower()
        ]
    return jsonify(products)


@app.route("/api/transacoes", methods=["POST"])
def api_create_transaction():
    """Registra uma venda concluída."""
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or payload.get("itens") or []
    try:
        result = create_transaction(items)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Falha ao registrar transação")
        return jsonify({"error": "Não foi possível registrar a transação."}), 500
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Painel administrativo
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
            session.clear()
            session["is_admin"] = True
            session["admin_user"] = username
            next_url = request.args.get("next") or url_for("admin_dashboard")
            # previne open-redirect (só aceita caminhos internos)
            if not next_url.startswith("/"):
                next_url = url_for("admin_dashboard")
            return redirect(next_url)
        error = "Usuário ou senha inválidos."
    return render_template("admin/login.html", error=error)


@app.route("/admin/logout", methods=["POST", "GET"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    stats = get_stats()
    transactions = list_transactions(limit=300)
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        transactions=transactions,
        admin_user=session.get("admin_user", "admin"),
        now=datetime.now(),
    )


# Filtros Jinja ------------------------------------------------------------

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
