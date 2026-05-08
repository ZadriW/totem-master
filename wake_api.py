"""Cliente de integração com a API GraphQL Storefront da Wake Commerce.

Endpoint: https://storefront-api.fbits.net/graphql
Header:   TCS-Access-Token  (identifica a loja)
Param:    partnerAccessToken (filtro de parceiro, opcional)

O token **Storefront** (header ``TCS-Access-Token``) deve estar apenas na
variável de ambiente ``WAKE_TOKEN`` — não versionar o valor no repositório.

Se ``WAKE_TOKEN`` não estiver definida, as chamadas à API falham com erro
explícito. Se a API estiver indisponível ou o token for inválido, o chamador
recebe exceção e pode usar fallback local (ex.: catálogo já no SQLite).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://storefront-api.fbits.net/graphql"


def _require_wake_token() -> str:
    """Retorna o TCS-Access-Token da Storefront API (Wake Commerce).

    O valor vem **somente** de ``WAKE_TOKEN`` no ambiente — sem fallback no código.
    """
    raw = (os.environ.get("WAKE_TOKEN") or "").strip()
    if not raw:
        raise PermissionError(
            "Configure a variável de ambiente WAKE_TOKEN com o TCS-Access-Token "
            "da Storefront API (painel Wake Commerce). O token não deve ficar no código-fonte."
        )
    return raw


# ---------------------------------------------------------------------------
# Queries GraphQL
# ---------------------------------------------------------------------------

_PRODUCTS_QUERY = """
query FetchProducts($first: Int!, $after: String) {
  products(
    first: $first
    after: $after
    filters: { mainVariant: true }
    sortKey: NAME
    sortDirection: ASC
  ) {
    nodes {
      productId
      productVariantId
      mainVariant
      productName
      variantName
      alias
      sku
      ean
      prices {
        price
        listPrice
      }
      images {
        url
        fileName
      }
      productCategories {
        name
        hierarchy
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

_CATEGORIES_QUERY = """
query {
  categories(first: 200, sortKey: NAME, sortDirection: ASC) {
    nodes {
      id
      name
    }
  }
}
"""

_SHOP_QUERY = """
query {
  shop {
    name
  }
}
"""


# ---------------------------------------------------------------------------
# Transporte HTTP
# ---------------------------------------------------------------------------

def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "TCS-Access-Token": _require_wake_token(),
    }


def _graphql(
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict:
    """Executa uma query GraphQL contra a Storefront API da Wake.

    Levanta ``ConnectionError`` se a API não responder ou retornar erro HTTP.
    Levanta ``RuntimeError`` se o payload contiver erros GraphQL.
    """
    payload: Dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        resp = requests.post(
            ENDPOINT, json=payload, headers=_headers(), timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ConnectionError(f"Falha de rede ao acessar Wake API: {exc}") from exc

    if resp.status_code >= 500:
        raise ConnectionError(
            f"Wake API retornou HTTP {resp.status_code}. "
            "Verifique se o token TCS-Access-Token esta correto e ativo "
            "na plataforma Wake Commerce."
        )

    resp.raise_for_status()
    data = resp.json()

    errors = data.get("errors")
    if errors:
        auth_errors = [e for e in errors if "AUTH_NOT_AUTHENTICATED" in str(e)]
        if auth_errors:
            raise PermissionError(
                "Token nao autorizado pela Wake API. "
                "Verifique se o TCS-Access-Token esta correto e ativo."
            )
        msgs = "; ".join(e.get("message", str(e)) for e in errors)
        raise RuntimeError(f"Wake GraphQL: {msgs}")

    return data.get("data") or {}


# ---------------------------------------------------------------------------
# Funções públicas
# ---------------------------------------------------------------------------

def fetch_categories() -> List[Dict]:
    """Retorna a lista de categorias da loja Wake."""
    data = _graphql(_CATEGORIES_QUERY)
    nodes = (data.get("categories") or {}).get("nodes") or []
    return [{"id": n.get("id"), "name": n.get("name", "")} for n in nodes]


def _primary_category(categories: list) -> str:
    """Extrai o nome da categoria mais específica (último nível da hierarquia)."""
    if not categories:
        return "Geral"
    best = categories[0]
    for c in categories:
        hier = c.get("hierarchy") or c.get("name") or ""
        best_hier = best.get("hierarchy") or best.get("name") or ""
        if hier.count("/") > best_hier.count("/"):
            best = c
    return (best.get("name") or "Geral").strip()


def _display_product_name(node: Dict[str, Any]) -> str:
    """Nome exibível: API às vezes preenche só ``variantName`` ou ``alias``."""
    for key in ("productName", "variantName"):
        v = (node.get(key) or "").strip()
        if v:
            return v
    alias = (node.get("alias") or "").strip()
    if alias:
        return alias
    return ""


def _display_product_sku(node: Dict[str, Any]) -> str:
    """SKU da linha Storefront; EAN como fallback quando ``sku`` vem vazio."""
    s = (node.get("sku") or "").strip()
    if s:
        return s
    ean = (node.get("ean") or "").strip()
    if ean:
        return ean
    return ""


def _wake_row_rank(node: Dict[str, Any]) -> tuple:
    """Ordenação usada ao escolher uma variante canônica por ``productId``."""
    name = _display_product_name(node)
    sku = _display_product_sku(node)
    main = 1 if node.get("mainVariant") else 0
    pvid = int(node.get("productVariantId") or 0)
    return (main, len(name), len(sku), pvid)


def _dedupe_wake_nodes_by_product_id(nodes: List[dict]) -> List[dict]:
    """Evita múltiplas variantes do mesmo ``productId`` sobrescreverem o SQLite.

    Sem ``mainVariant: true`` (ou em cenários extremos), a Storefront pode
    devolver várias linhas por produto; a última da página podia gravar nome
    vazio e gerar placeholders ``Produto`` / ``OM-xxxxx`` / ``Geral``.
    """
    best: Dict[int, dict] = {}
    for node in nodes:
        raw = node.get("productId")
        if raw is None:
            continue
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        if pid not in best or _wake_row_rank(node) > _wake_row_rank(best[pid]):
            best[pid] = node
    return list(best.values())


def fetch_products(page_size: int = 50, max_pages: int = 500) -> List[Dict]:
    """Busca todos os produtos da loja Wake com paginação automática.

    Retorna lista normalizada com campos compatíveis ao schema local.
    Usa apenas a **variante principal** (filtro ``mainVariant``) e remove
    duplicados por ``productId``. Registros com ``productId`` inválido
    (``<= 0``), p.ex. listas especiais da loja, são ignorados.

    A Wake é usada como biblioteca de produtos; estoque, estoque mínimo e
    ativação comercial são controlados localmente pelo administrador.
    """
    raw_nodes: List[dict] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        variables: Dict[str, Any] = {"first": page_size}
        if cursor:
            variables["after"] = cursor

        data = _graphql(_PRODUCTS_QUERY, variables)
        products_data = data.get("products") or {}
        batch = products_data.get("nodes") or []
        raw_nodes.extend(batch)

        page_info = products_data.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    canonical = _dedupe_wake_nodes_by_product_id(raw_nodes)
    all_products: List[Dict] = []

    for node in canonical:
        prices = node.get("prices") or {}
        price = prices.get("price") or prices.get("listPrice") or 0
        images = node.get("images") or []
        image_url = images[0].get("url", "") if images else ""
        cats = node.get("productCategories") or []
        category = _primary_category(cats)
        nome = _display_product_name(node) or "Produto"
        sku_wake = _display_product_sku(node)

        all_products.append({
            "id": int(node["productId"]),
            "sku": sku_wake,
            "nome": nome,
            "categoria": category,
            "descricao": f"{nome} — {category}",
            "preco": float(price),
            "imagem": image_url,
        })

    log.info(
        "Wake API: %d linhas recebidas, %d produtos canônicos.",
        len(raw_nodes),
        len(all_products),
    )
    return all_products


def test_connection() -> Dict:
    """Testa a conexão com a API e retorna um resumo.

    ``ok=True`` se a API respondeu com dados; ``ok=False`` com mensagem
    de erro detalhada caso contrário.
    """
    try:
        products = fetch_products(page_size=5, max_pages=1)
        return {
            "ok": True,
            "products_sample": len(products),
            "sample_names": [p["nome"] for p in products[:5]],
        }
    except (ConnectionError, PermissionError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"Erro inesperado: {exc}"}
