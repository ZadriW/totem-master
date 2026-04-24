"""Totem Odonto Master - aplicação Flask (dashboard do cliente + painel admin).

- ``/``                Tela de boas-vindas (reseta a sessão do cliente).
- ``/catalogo``        Catálogo para o cliente.
- ``/pagamento``       Resumo do pedido.
- ``/pagamento/aguardando``  Aguardo da maquininha + confirmação da venda.
- ``/api/...``         Endpoints JSON consumidos pelo front.
- ``/admin/...``       Painel administrativo autenticado (dashboard, estoque,
                       movimentações).
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

from data.products import CATEGORIES
from database import (
    create_transaction,
    get_product,
    get_stats,
    get_stock_stats,
    get_transaction_by_order_number,
    init_db,
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

# Inicializa o schema e popula o catálogo inicial (se vazio).
init_db()

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
        result = create_transaction(
            items,
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
            session.clear()
            session["is_admin"] = True
            session["admin_user"] = username
            next_url = request.args.get("next") or url_for("admin_dashboard")
            if not next_url.startswith("/"):
                next_url = url_for("admin_dashboard")
            return redirect(next_url)
        error = "Usuário ou senha inválidos."
    return render_template("admin/login.html", error=error)


@app.route("/admin/logout", methods=["POST", "GET"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


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
        admin_user=session.get("admin_user", "admin"),
        now=datetime.now(),
        active_section="dashboard",
    )


@app.route("/admin/sincronizar-wake", methods=["POST"])
@admin_required
def admin_sync_wake():
    """Sincroniza produtos e estoque com a Wake Commerce."""
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
            f"Total de {len(products)} produto(s) da Wake Commerce.",
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
    """Apaga vendas, dados de clientes e movimentações após o estoque inicial."""
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
            f"{result['movements_deleted']} movimentação(ões) apagadas (mantidas apenas as de estoque inicial); "
            f"estoque de {result['products_restored']} produto(s) restaurado ao padrão.",
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
        "admin_user": session.get("admin_user", "admin"),
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
            created_by=session.get("admin_user", "admin"),
        )
        flash(f"Entrada de {quantity} un. registrada.", "success")
    except ValueError as exc:
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
            created_by=session.get("admin_user", "admin"),
        )
        flash(f"Saída de {quantity} un. registrada.", "success")
    except ValueError as exc:
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
            created_by=session.get("admin_user", "admin"),
        )
        flash(f"Estoque ajustado para {new_stock} un.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/estoque/<int:product_id>/minimo", methods=["POST"])
@admin_required
def admin_stock_min(product_id: int):
    min_stock = _parse_int(request.form.get("min_stock"), 0)
    if update_product_min_stock(product_id, min_stock):
        flash(f"Estoque mínimo atualizado para {max(0, min_stock)} un.", "success")
    else:
        flash("Não foi possível atualizar o estoque mínimo.", "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


@app.route("/admin/estoque/<int:product_id>/ativar", methods=["POST"])
@admin_required
def admin_stock_toggle_active(product_id: int):
    active = request.form.get("active") == "1"
    if set_product_active(product_id, active):
        flash(
            "Produto ativado e disponível no totem." if active
            else "Produto desativado — não aparecerá no totem.",
            "success",
        )
    else:
        flash("Não foi possível atualizar o produto.", "error")
    return redirect(request.referrer or url_for("admin_stock_product", product_id=product_id))


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
