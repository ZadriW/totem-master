"""Cliente de integração com a API GraphQL Storefront da Wake Commerce.

Endpoint: https://storefront-api.fbits.net/graphql
Header:   TCS-Access-Token  (identifica a loja)
Param:    partnerAccessToken (filtro de parceiro, opcional)

O token é lido da variável de ambiente ``WAKE_TOKEN`` ou do fallback
hard-coded (token de integração gerado na plataforma Wake Commerce).

Se a API estiver indisponível ou o token for inválido, todas as funções
levantam exceção — o chamador decide se usa fallback local.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://storefront-api.fbits.net/graphql"

# Token Storefront (header TCS-Access-Token): criado no painel Wake para a
# Storefront API — formato típico ``tcs_<conta>_<hex>``. Sobrescreva em
# produção com a variável de ambiente ``WAKE_TOKEN``.
WAKE_TOKEN = os.environ.get(
    "WAKE_TOKEN",
    "tcs_odont_b84ce06ac1bf4e40b7ade378d7ffa53b",
)


# ---------------------------------------------------------------------------
# Queries GraphQL
# ---------------------------------------------------------------------------

_PRODUCTS_QUERY = """
query FetchProducts($first: Int!, $after: String) {
  products(
    first: $first
    after: $after
    filters: {}
    sortKey: NAME
    sortDirection: ASC
  ) {
    nodes {
      productId
      productName
      sku
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
        "TCS-Access-Token": WAKE_TOKEN,
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


def fetch_products(page_size: int = 50, max_pages: int = 20) -> List[Dict]:
    """Busca todos os produtos da loja Wake com paginação automática.

    Retorna lista normalizada com campos compatíveis ao schema local.
    A Wake é usada como biblioteca de produtos; estoque, estoque mínimo e
    ativação comercial são controlados localmente pelo administrador.
    """
    all_products: List[Dict] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        variables: Dict[str, Any] = {"first": page_size}
        if cursor:
            variables["after"] = cursor

        data = _graphql(_PRODUCTS_QUERY, variables)
        products_data = data.get("products") or {}
        nodes = products_data.get("nodes") or []

        for node in nodes:
            prices = node.get("prices") or {}
            price = prices.get("price") or prices.get("listPrice") or 0
            images = node.get("images") or []
            image_url = images[0].get("url", "") if images else ""
            cats = node.get("productCategories") or []
            category = _primary_category(cats)

            all_products.append({
                "id": int(node["productId"]),
                "sku": (node.get("sku") or "").strip(),
                "nome": (node.get("productName") or "Produto").strip(),
                "categoria": category,
                "descricao": f"{node.get('productName', '')} — {category}",
                "preco": float(price),
                "imagem": image_url,
            })

        page_info = products_data.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    log.info("Wake API: %d produtos obtidos.", len(all_products))
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
