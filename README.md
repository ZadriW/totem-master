# Totem Odonto Master

Sistema de venda de produtos odontológicos para totem físico (Windows).
Back-end em Python + Flask, front-end em HTML, CSS e JavaScript puro.

## Estrutura

```
Totem/
├── app.py                  # entrada da aplicação Flask
├── requirements.txt
├── data/
│   └── products.py         # catálogo aleatório (placeholder)
├── images/                 # logo original
├── static/
│   ├── css/                # estilos
│   ├── images/             # assets servidos pelo Flask
│   └── js/                 # scripts do cliente
└── templates/
    ├── base.html
    ├── welcome.html        # tela inicial
    └── catalog.html        # catálogo de produtos
```

## Como executar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Abra no navegador (ou no modo quiosque do totem):
<http://localhost:5000/>

## Rotas

| Rota            | Descrição                                        |
| --------------- | ------------------------------------------------ |
| `/`             | Tela de boas-vindas com logo e botão de entrada. |
| `/catalogo`     | Catálogo de produtos com busca e filtros.        |
| `/api/produtos` | JSON de produtos (suporta `?q=` e `?categoria=`).|

## Próximos passos

- Substituir produtos aleatórios por itens reais do estoque.
- Carrinho, confirmação de pagamento (via maquininha) e geração de nota
  de retirada.
- Painel administrativo (estoque, movimentações, relatórios).
