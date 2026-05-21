"""Cliente Wake Commerce — consulta on-demand por SKU (Storefront GraphQL).

Usado ao adicionar produtos por SKU ou importar planilha quando o item
ainda não existe no catálogo local. Não há sincronização em massa do catálogo.

Endpoint: https://storefront-api.fbits.net/graphql
Header:   TCS-Access-Token (variável de ambiente ``WAKE_TOKEN``)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

ENDPOINT = "https://storefront-api.fbits.net/graphql"


def wake_token_configured() -> bool:
    """True se ``WAKE_TOKEN`` estiver definido e não-vazio no ambiente."""
    return bool((os.environ.get("WAKE_TOKEN") or "").strip())


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
# Helpers e consulta por SKU
# ---------------------------------------------------------------------------

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


def _normalize_wake_node(node: Dict[str, Any]) -> Dict:
    """Normaliza um nó bruto da Storefront API para o formato interno do Totem.

    Combina ``productName`` + ``variantName`` no nome quando a variante não é
    principal, para que entradas como "Lima Hedstroem — Nº45/80 25mm" sejam
    distintas de "Lima Hedstroem" (variante principal).
    """
    prices = node.get("prices") or {}
    price = prices.get("price") or prices.get("listPrice") or 0
    images = node.get("images") or []
    image_url = images[0].get("url", "") if images else ""
    cats = node.get("productCategories") or []
    category = _primary_category(cats)

    product_name = (node.get("productName") or "").strip()
    variant_name = (node.get("variantName") or "").strip()
    is_main = bool(node.get("mainVariant"))

    if not is_main and variant_name and variant_name.lower() not in product_name.lower():
        full_name = f"{product_name} {variant_name}".strip()
    else:
        full_name = product_name or variant_name or "Produto"

    sku_wake = _display_product_sku(node)

    return {
        "id": int(node.get("productId") or 0),
        "variant_id": int(node.get("productVariantId") or 0),
        "is_variant": not is_main,
        "sku": sku_wake,
        "nome": full_name,
        "categoria": category,
        "descricao": f"{full_name} — {category}",
        "preco": float(price),
        "imagem": image_url,
    }


def _escape_graphql_str(value: str) -> str:
    """Escapa string para uso literal em query GraphQL inline."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def fetch_product_by_sku(sku: str) -> Optional[Dict]:
    """Busca uma variante/produto na Wake pelo SKU exato (on-demand).

    A Storefront API da Wake não aceita variável GraphQL no filtro ``sku``
    (retorna HTTP 400); por isso o SKU é embutido na query com escape seguro.

    Usa ``ignoreDisplayRules: true`` para localizar variantes secundárias que
    não aparecem na vitrine pública. Retorna None se não encontrado ou se a
    API estiver indisponível — nunca lança exceção ao chamador.
    """
    q = (sku or "").strip()
    if not q:
        return None
    if not wake_token_configured():
        log.warning("fetch_product_by_sku(%s): WAKE_TOKEN não configurado.", q)
        return None
    if not re.fullmatch(r"[A-Za-z0-9._\-/#]+", q):
        log.warning("fetch_product_by_sku(%s): SKU com caracteres inválidos.", q)
        return None

    safe_sku = _escape_graphql_str(q)
    query = f"""
    query {{
      products(first: 5, filters: {{ ignoreDisplayRules: true, sku: "{safe_sku}" }}) {{
        nodes {{
          productId
          productVariantId
          mainVariant
          productName
          variantName
          sku
          ean
          prices {{
            price
            listPrice
          }}
          images {{
            url
            fileName
          }}
          productCategories {{
            name
            hierarchy
          }}
        }}
      }}
    }}
    """
    try:
        data = _graphql(query)
    except PermissionError:
        log.warning("fetch_product_by_sku(%s): token Wake inválido ou ausente.", q)
        return None
    except Exception as exc:
        log.warning("fetch_product_by_sku(%s): falha na API Wake: %s", q, exc)
        return None
    nodes = (data.get("products") or {}).get("nodes") or []
    if not nodes:
        return None
    exact = next(
        (n for n in nodes if _display_product_sku(n).strip() == q),
        None,
    )
    node = exact if exact is not None else nodes[0]
    return _normalize_wake_node(node)
