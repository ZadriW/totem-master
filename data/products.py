"""Catálogo de produtos — **seed inicial** do banco.

Historicamente este módulo servia o catálogo direto para o front. A partir
da introdução do controle de estoque, ele passou a ser usado apenas como
*seed* da tabela ``products`` (via ``database.init_db``) quando a base está
vazia. A fonte de verdade do catálogo é o SQLite; o cadastro é mantido e
editado pelo painel administrativo.

Mantém a estrutura de dados original (id, **sku**, nome, categoria, preço, etc.) +
``estoque_minimo`` para alimentar o ponto de reposição do painel.

Cada item recebe um **SKU** estável no formato ``OM-NNNNN`` (prefixo Odonto Master
+ id numérico com 5 dígitos), alinhado ao estoque e às notas fiscais.
"""

from __future__ import annotations

import random
from typing import List, Dict

CATEGORIES: List[str] = [
    "Anestésicos",
    "Biossegurança",
    "Brocas",
    "Dentística",
    "Descartáveis",
    "Endodontia",
    "Instrumentais",
    "Ortodontia",
    "Profilaxia",
    "Resinas",
]

_NAMES_BY_CATEGORY: Dict[str, List[str]] = {
    "Anestésicos": [
        "Anestésico Lidocaína 2%",
        "Anestésico Mepivacaína 3%",
        "Anestésico Articaína 4%",
        "Agulha Gengival Curta 30G",
    ],
    "Biossegurança": [
        "Máscara Tripla Descartável",
        "Avental TNT Descartável",
        "Touca Sanfonada",
        "Álcool 70% 1L",
    ],
    "Brocas": [
        "Broca Diamantada 1014",
        "Broca Carbide 330",
        "Broca Cirúrgica Zekrya",
        "Kit Brocas Diamantadas",
    ],
    "Dentística": [
        "Matriz de Poliéster",
        "Cunha de Madeira",
        "Aplicador Descartável",
        "Tira de Lixa de Aço",
    ],
    "Descartáveis": [
        "Luva de Procedimento M",
        "Sugador Descartável",
        "Rolete de Algodão",
        "Babador Impermeável",
    ],
    "Endodontia": [
        "Lima Endodôntica K 25mm",
        "Cimento Endodôntico",
        "Cone de Guta Percha",
        "Solução de Hipoclorito",
    ],
    "Instrumentais": [
        "Espelho Bucal n°5",
        "Sonda Exploradora",
        "Pinça Clínica",
        "Cureta Periodontal Gracey",
    ],
    "Ortodontia": [
        "Bráquete Roth 0.022\"",
        "Fio de NiTi Superior",
        "Elástico Intermaxilar",
        "Kit Acadêmico de Ortodontia",
    ],
    "Profilaxia": [
        "Pasta Profilática 90g",
        "Escova Robinson",
        "Flúor Gel Neutro",
        "Taça de Borracha",
    ],
    "Resinas": [
        "Resina Composta A2 4g",
        "Resina Flow A3",
        "Resina Bulk Fill",
        "Adesivo Universal 5ml",
    ],
}

# Imagens placeholder coloridas (serviço público picsum). Como usamos seed
# baseado no id, cada produto recebe sempre a mesma imagem.
_PLACEHOLDER_IMAGE = "https://picsum.photos/seed/odontomaster-{seed}/400/400"


def _build_catalog() -> List[Dict]:
    rng = random.Random(42)  # seed fixa => catálogo reprodutível
    products: List[Dict] = []
    product_id = 1
    for category in CATEGORIES:
        for name in _NAMES_BY_CATEGORY[category]:
            price = round(rng.uniform(12.0, 450.0), 2)
            stock = rng.randint(3, 80)
            products.append(
                {
                    "id": product_id,
                    "sku": f"OM-{product_id:05d}",
                    "nome": name,
                    "categoria": category,
                    "descricao": f"{name} — produto {category.lower()} de uso profissional.",
                    "preco": price,
                    "estoque": stock,
                    # ponto de reposição sugerido ~ 20% do estoque inicial,
                    # com mínimo de 5 unidades.
                    "estoque_minimo": max(5, round(stock * 0.20)),
                    "imagem": _PLACEHOLDER_IMAGE.format(seed=product_id),
                }
            )
            product_id += 1
    return products


_CATALOG: List[Dict] = _build_catalog()


def get_seed_products() -> List[Dict]:
    """Retorna uma cópia do catálogo para *seed* do banco.

    Usada por ``database.init_db`` quando a tabela ``products`` está vazia.
    """
    return list(_CATALOG)


# Compatibilidade retroativa — algum código antigo pode importar get_products().
def get_products() -> List[Dict]:
    return list(_CATALOG)
