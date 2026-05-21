"""Metadados do catálogo para o totem.

O catálogo real vem da **Wake Commerce** (sincronização via API) e é persistido
no SQLite. Não há mais *seed* de produtos fictícios.

``CATEGORIES`` é preenchido em tempo de execução em ``app.py`` após a sync
com a Wake (lista mutável compartilhada com os templates).
"""

from __future__ import annotations

from typing import Dict, List

# Inicialmente vazio; substituído após sync Wake em ``app.py``.
CATEGORIES: List[str] = []


def get_seed_products() -> List[Dict]:
    """Compatibilidade: o banco não é mais populado por produtos locais."""
    return []


def get_products() -> List[Dict]:
    """Compatibilidade retroativa — catálogo vem do banco, não deste módulo."""
    return []
