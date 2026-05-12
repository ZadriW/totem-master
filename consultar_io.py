"""Integração com a API Consultar.io para validação de CRO.

API Docs: https://docs.consultar.io/api/cro/
"""
import os
from typing import Dict, List, Optional
import requests


# Token de autenticação (idealmente via variável de ambiente em produção)
CONSULTAR_IO_TOKEN = os.environ.get(
    "CONSULTAR_IO_TOKEN",
    "da3791449055fe80c705cb489dcb9516d50df58b"
)

API_BASE_URL = "https://consultar.io/api/v1"
TIMEOUT_SECONDS = 30


class ConsultarIOError(Exception):
    """Exceção base para erros da API Consultar.io."""
    pass


class RegistroNaoEncontrado(ConsultarIOError):
    """Registro CRO não encontrado (404)."""
    pass


class CredenciaisInvalidas(ConsultarIOError):
    """Token inválido ou plano inativo/sem créditos (403)."""
    pass


class RequisicaoInvalida(ConsultarIOError):
    """Parâmetros inválidos (400)."""
    pass


def _get_headers() -> Dict[str, str]:
    """Retorna headers HTTP padrão com token de autenticação."""
    return {
        "Authorization": f"Token {CONSULTAR_IO_TOKEN}",
        "Accept": "application/json",
    }


def consultar_cro(
    uf: str,
    numero_registro: str,
    categoria: str,
) -> Dict:
    """Consulta CRO por UF, número de registro e categoria.
    
    Args:
        uf: Sigla do estado (ex.: "SP", "RJ")
        numero_registro: Número do registro (até 7 dígitos, zeros à esquerda são removidos pela API)
        categoria: Código da categoria (ex.: "cd", "tsb", "tpd", etc.)
    
    Returns:
        Dict com: uf, numero_registro, categoria (expandida), nome_razao_social, situacao
    
    Raises:
        RegistroNaoEncontrado: Quando CRO não é encontrado (404)
        CredenciaisInvalidas: Token inválido ou sem créditos (403)
        RequisicaoInvalida: Parâmetros inválidos (400)
        ConsultarIOError: Outros erros HTTP ou de rede
    """
    url = f"{API_BASE_URL}/cro/consultar"
    params = {
        "uf": uf.upper().strip(),
        "numero_registro": numero_registro.strip().lstrip("0") or "0",
        "categoria": categoria.lower().strip(),
    }
    
    try:
        response = requests.get(
            url,
            params=params,
            headers=_get_headers(),
            timeout=TIMEOUT_SECONDS,
        )
        
        # Tratamento de erros HTTP conforme documentação
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            data = response.json()
            msg = data.get("message", "Registro CRO não encontrado")
            raise RegistroNaoEncontrado(msg)
        elif response.status_code == 403:
            data = response.json()
            error_code = data.get("error", "")
            msg = data.get("message", "Credenciais inválidas ou sem créditos")
            if "CREDITOS_INSUFICIENTES" in error_code:
                raise CredenciaisInvalidas(f"Créditos insuficientes: {msg}")
            elif "PLANO_INATIVO" in error_code:
                raise CredenciaisInvalidas(f"Plano inativo: {msg}")
            raise CredenciaisInvalidas(msg)
        elif response.status_code == 400:
            data = response.json()
            msg = data.get("message", "Requisição inválida")
            raise RequisicaoInvalida(msg)
        elif response.status_code >= 500:
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            msg = data.get("message", f"Erro no servidor da API (HTTP {response.status_code})")
            raise ConsultarIOError(msg)
        else:
            raise ConsultarIOError(f"Erro HTTP {response.status_code}: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        raise ConsultarIOError(
            f"Timeout ao consultar API (>{TIMEOUT_SECONDS}s). Tente novamente."
        )
    except requests.exceptions.RequestException as exc:
        raise ConsultarIOError(f"Erro de rede ao consultar API: {exc}")


def buscar_cro(
    nome_razao_social: str,
    categoria: str,
) -> List[Dict]:
    """Busca CRO por nome/razão social e categoria.
    
    Args:
        nome_razao_social: Nome do profissional ou razão social
        categoria: Código da categoria (ex.: "cd", "tsb", etc.)
    
    Returns:
        Lista de dicts com: uf, numero_registro, categoria, nome_razao_social
        (máximo 100 resultados conforme documentação)
    
    Raises:
        RegistroNaoEncontrado: Quando nenhum resultado é encontrado (404)
        CredenciaisInvalidas: Token inválido ou sem créditos (403)
        RequisicaoInvalida: Parâmetros inválidos (400)
        ConsultarIOError: Outros erros HTTP ou de rede
    """
    url = f"{API_BASE_URL}/cro/buscar"
    params = {
        "nome_razao_social": nome_razao_social.strip(),
        "categoria": categoria.lower().strip(),
    }
    
    try:
        response = requests.get(
            url,
            params=params,
            headers=_get_headers(),
            timeout=TIMEOUT_SECONDS,
        )
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            data = response.json()
            msg = data.get("message", "Nenhum registro encontrado")
            raise RegistroNaoEncontrado(msg)
        elif response.status_code == 403:
            data = response.json()
            msg = data.get("message", "Credenciais inválidas ou sem créditos")
            raise CredenciaisInvalidas(msg)
        elif response.status_code == 400:
            data = response.json()
            msg = data.get("message", "Requisição inválida")
            raise RequisicaoInvalida(msg)
        elif response.status_code >= 500:
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            msg = data.get("message", f"Erro no servidor da API (HTTP {response.status_code})")
            raise ConsultarIOError(msg)
        else:
            raise ConsultarIOError(f"Erro HTTP {response.status_code}: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        raise ConsultarIOError(
            f"Timeout ao buscar API (>{TIMEOUT_SECONDS}s). Tente novamente."
        )
    except requests.exceptions.RequestException as exc:
        raise ConsultarIOError(f"Erro de rede ao buscar API: {exc}")


# Mapeamento de categorias (código → descrição) para uso na UI
CATEGORIAS_CRO = {
    "cd": "Cirurgião Dentista",
    "tsb": "Técnico em Saúde Bucal",
    "tpd": "Técnico em Prótese Dentária",
    "asb": "Auxiliar em Saúde Bucal",
    "apd": "Auxiliar de Prótese Dentária",
    "estagiario": "Estagiário",
    "clinica-assistencia": "Clínica/Entidade Prestadora de Assistência Odontológica",
    "laboratorio": "Laboratório de Prótese Dentária",
    "comercio-industria": "Comércio/Indústria de Produtos Odontológicos",
}


def get_categoria_descricao(codigo: str) -> str:
    """Retorna a descrição da categoria pelo código."""
    return CATEGORIAS_CRO.get(codigo.lower().strip(), codigo)
