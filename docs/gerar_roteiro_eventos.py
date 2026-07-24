# -*- coding: utf-8 -*-
"""Gera o PDF "Roteiro de Aplicação do Sistema Totem em Eventos".

Documento de apoio operacional (não faz parte da aplicação web). Descreve o
passo a passo para rodar o sistema no dia de um evento em dois cenários:

    A) Rede local via roteador Wi-Fi (sem depender de internet).
    B) Nuvem via Starlink + Railway (com internet via satélite).

Uso::

    pip install reportlab
    python docs/gerar_roteiro_eventos.py

Gera ``docs/Roteiro-Eventos-Totem.pdf``.
"""
from __future__ import annotations

import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Paleta e estilos
# ---------------------------------------------------------------------------

NAVY = colors.HexColor("#0e167a")
NAVY_DARK = colors.HexColor("#090f52")
AMBER = colors.HexColor("#a5620a")
AMBER_BG = colors.HexColor("#fdf1e2")
GREEN = colors.HexColor("#1c7c3f")
GREEN_BG = colors.HexColor("#e9f7ef")
RED = colors.HexColor("#b3221c")
RED_BG = colors.HexColor("#fbeaea")
GREY_BG = colors.HexColor("#f3f4f8")
TEXT = colors.HexColor("#1c2033")
MUTED = colors.HexColor("#5b6178")

OUT_PATH = os.path.join(os.path.dirname(__file__), "Roteiro-Eventos-Totem.pdf")

base = getSampleStyleSheet()

styles = {
    "cover_title": ParagraphStyle(
        "cover_title", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=27, leading=32, textColor=colors.white, alignment=TA_LEFT,
        spaceAfter=10,
    ),
    "cover_sub": ParagraphStyle(
        "cover_sub", parent=base["Normal"], fontName="Helvetica",
        fontSize=13.5, leading=19, textColor=colors.white, alignment=TA_LEFT,
    ),
    "cover_meta": ParagraphStyle(
        "cover_meta", parent=base["Normal"], fontName="Helvetica",
        fontSize=10, leading=14, textColor=colors.HexColor("#c7cdfa"),
    ),
    "h1": ParagraphStyle(
        "h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=18,
        leading=22, textColor=NAVY, spaceBefore=4, spaceAfter=10,
    ),
    "h2": ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13.5,
        leading=17, textColor=NAVY_DARK, spaceBefore=14, spaceAfter=6,
    ),
    "h3": ParagraphStyle(
        "h3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=11.3,
        leading=15, textColor=TEXT, spaceBefore=10, spaceAfter=4,
    ),
    "body": ParagraphStyle(
        "body", parent=base["Normal"], fontName="Helvetica", fontSize=10,
        leading=14.5, textColor=TEXT, spaceAfter=6, alignment=TA_LEFT,
    ),
    "body_bold": ParagraphStyle(
        "body_bold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=14.5, textColor=TEXT, spaceAfter=6,
    ),
    "bullet": ParagraphStyle(
        "bullet", parent=base["Normal"], fontName="Helvetica", fontSize=10,
        leading=14.5, textColor=TEXT,
    ),
    "step_num": ParagraphStyle(
        "step_num", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=14, textColor=colors.white, alignment=TA_CENTER,
    ),
    "step_body": ParagraphStyle(
        "step_body", parent=base["Normal"], fontName="Helvetica", fontSize=10,
        leading=14.5, textColor=TEXT,
    ),
    "code": ParagraphStyle(
        "code", parent=base["Normal"], fontName="Courier", fontSize=9.3,
        leading=13.5, textColor=colors.HexColor("#eef0ff"),
    ),
    "box_title": ParagraphStyle(
        "box_title", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=13, textColor=TEXT,
    ),
    "box_body": ParagraphStyle(
        "box_body", parent=base["Normal"], fontName="Helvetica", fontSize=9.7,
        leading=13.6, textColor=TEXT,
    ),
    "table_head": ParagraphStyle(
        "table_head", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9.3,
        leading=12.5, textColor=colors.white,
    ),
    "table_cell": ParagraphStyle(
        "table_cell", parent=base["Normal"], fontName="Helvetica", fontSize=9.3,
        leading=12.8, textColor=TEXT,
    ),
    "toc_item": ParagraphStyle(
        "toc_item", parent=base["Normal"], fontName="Helvetica", fontSize=11,
        leading=20, textColor=TEXT,
    ),
    "toc_num": ParagraphStyle(
        "toc_num", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=11,
        leading=20, textColor=NAVY,
    ),
}

story = []


# ---------------------------------------------------------------------------
# Helpers de conteúdo
# ---------------------------------------------------------------------------

def h1(text):
    story.append(Paragraph(text, styles["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.4, color=NAVY, spaceAfter=10))


def h2(text):
    story.append(Paragraph(text, styles["h2"]))


def h3(text):
    story.append(Paragraph(text, styles["h3"]))


def p(text):
    story.append(Paragraph(text, styles["body"]))


def bullets(items):
    story.append(
        ListFlowable(
            [ListItem(Paragraph(it, styles["bullet"]), spaceAfter=4) for it in items],
            bulletType="bullet",
            start="•",
            leftIndent=14,
            bulletFontSize=9,
        )
    )
    story.append(Spacer(1, 4))


def steps(items):
    """Lista numerada visual com "pílulas" numeradas."""
    rows = []
    for i, text in enumerate(items, start=1):
        badge = Table([[Paragraph(str(i), styles["step_num"])]], colWidths=[0.62 * cm], rowHeights=[0.62 * cm])
        badge.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0, colors.white),
            ])
        )
        row = Table(
            [[badge, Paragraph(text, styles["step_body"])]],
            colWidths=[0.95 * cm, 15.6 * cm],
        )
        row.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        rows.append(row)
    story.extend(rows)
    story.append(Spacer(1, 2))


def _callout(label, title, lines, bg, border):
    label_style = ParagraphStyle(
        "callout_label", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.4,
        leading=10, textColor=colors.white,
    )
    label_badge = Table([[Paragraph(label, label_style)]], colWidths=[None])
    label_badge.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), border),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    content = [label_badge, Spacer(1, 5), Paragraph(f"<b>{title}</b>", styles["box_title"])]
    for t in lines:
        content.append(Paragraph(t, styles["box_body"]))
    tbl = Table([[content]], colWidths=[16.5 * cm])
    tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("BOX", (0, 0), (-1, -1), 0.9, border),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ])
    )
    story.append(Spacer(1, 4))
    story.append(KeepTogether(tbl))
    story.append(Spacer(1, 8))


def tip(title, *lines):
    _callout("DICA", title, lines, GREEN_BG, GREEN)


def warn(title, *lines):
    _callout("ATENÇÃO", title, lines, AMBER_BG, AMBER)


def critical(title, *lines):
    _callout("CRÍTICO", title, lines, RED_BG, RED)


def code(lines):
    text = "<br/>".join(lines)
    tbl = Table([[Paragraph(text, styles["code"])]], colWidths=[16.5 * cm])
    tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY_DARK),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ])
    )
    story.append(tbl)
    story.append(Spacer(1, 8))


def _checkbox_flowable():
    box = Table([[""]], colWidths=[0.32 * cm], rowHeights=[0.32 * cm])
    box.setStyle(
        TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, MUTED),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ])
    )
    return box


def checklist(items):
    rows = [[_checkbox_flowable(), Paragraph(it, styles["table_cell"])] for it in items]
    tbl = Table(rows, colWidths=[0.8 * cm, 15.7 * cm])
    tbl.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e3e5f0")),
        ])
    )
    story.append(tbl)
    story.append(Spacer(1, 8))


def data_table(head, rows, col_widths):
    data = [[Paragraph(h, styles["table_head"]) for h in head]]
    for r in rows:
        data.append([Paragraph(c, styles["table_cell"]) for c in r])
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6d9ea")),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), GREY_BG))
    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    story.append(Spacer(1, 10))


def spacer(h=8):
    story.append(Spacer(1, h))


def page_break():
    story.append(PageBreak())


# ---------------------------------------------------------------------------
# Capa
# ---------------------------------------------------------------------------

cover_content = [
    Spacer(1, 6.6 * cm),
    Paragraph("ODONTO MASTER", ParagraphStyle(
        "brand", fontName="Helvetica-Bold", fontSize=12, textColor=colors.HexColor("#c7cdfa"),
        leading=14, spaceAfter=14,
    )),
    Paragraph("Roteiro de Aplicação do<br/>Sistema Totem em Eventos", styles["cover_title"]),
    Spacer(1, 8),
    Paragraph(
        "Guia passo a passo para preparar e operar o sistema no dia do evento, "
        "cobrindo dois cenários de conectividade:",
        styles["cover_sub"],
    ),
    Spacer(1, 6),
    Paragraph("<b>A)</b> Rede local com roteador Wi-Fi", styles["cover_sub"]),
    Paragraph("<b>B)</b> Nuvem com Starlink + Railway", styles["cover_sub"]),
    Spacer(1, 3.2 * cm),
    Paragraph(
        "Documento de apoio operacional — uso interno da equipe Odonto Master.<br/>"
        "Revise sempre antes de cada evento, pois o sistema pode evoluir.",
        styles["cover_meta"],
    ),
]
cover_table = Table([[c] for c in cover_content], colWidths=[17 * cm])
cover_table.setStyle(
    TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ])
)
story.append(cover_table)
page_break()


# ---------------------------------------------------------------------------
# Sumário
# ---------------------------------------------------------------------------

h1("Sumário")
toc_entries = [
    ("1", "Visão geral do sistema"),
    ("2", "Duas formas de operar no dia do evento"),
    ("3", "Cenário A — Rede local com roteador Wi-Fi"),
    ("4", "Cenário B — Nuvem com Starlink + Railway"),
    ("5", "Comparativo rápido entre os cenários"),
    ("6", "Backup e continuidade dos dados"),
    ("7", "Segurança e credenciais"),
    ("8", "Solução de problemas comuns"),
    ("9", "Checklist final consolidado"),
]
toc_rows = [[Paragraph(n, styles["toc_num"]), Paragraph(t, styles["toc_item"])] for n, t in toc_entries]
toc_tbl = Table(toc_rows, colWidths=[1.1 * cm, 15.4 * cm])
toc_tbl.setStyle(
    TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e3e5f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])
)
story.append(toc_tbl)
page_break()


# ---------------------------------------------------------------------------
# 1. Visão geral
# ---------------------------------------------------------------------------

h1("1. Visão geral do sistema")
p(
    "O <b>Totem</b> é a plataforma de vendas assistidas da Odonto Master usada em feiras, "
    "congressos e ações comerciais. Não existe autoatendimento público: toda venda é feita "
    "por um <b>vendedor autenticado</b>, vinculado a um evento ativo, usando um navegador em "
    "computador, tablet ou celular."
)
p("O sistema tem dois papéis principais:")
bullets([
    "<b>Administrador</b> — cadastra o evento, os produtos, o estoque, os vendedores e as promoções antes do evento, e acompanha vendas e financeiro durante e depois.",
    "<b>Vendedor</b> — no dia do evento, consulta o catálogo, monta o carrinho, conduz o checkout e confirma a venda com o código <b>AUT</b> da maquininha de cartão/PIX.",
])
p("Fluxo resumido de uma venda:")
steps([
    "O vendedor adiciona produtos ao carrinho (preços e promoções calculados e validados pelo servidor).",
    "Informa os dados do cliente e a forma de pagamento (checkout).",
    "O sistema cria um pedido <b>pendente</b> — o estoque ainda não é alterado.",
    "O pagamento é feito na maquininha; o vendedor digita o código <b>AUT</b> retornado por ela.",
    "O sistema confirma a venda, baixa o estoque do evento e emite a nota de retirada.",
])
warn(
    "Ponto crítico de todo o roteiro",
    "O sistema roda em <b>um único servidor</b> (o computador que executa o programa Python/Flask). "
    "Todos os dispositivos dos vendedores precisam conseguir <b>acessar esse servidor pela rede</b> "
    "— seja uma rede Wi-Fi local (Cenário A), seja a internet (Cenário B). Este documento existe "
    "justamente para garantir que essa conectividade funcione no dia do evento.",
)


# ---------------------------------------------------------------------------
# 2. Duas formas de operar
# ---------------------------------------------------------------------------

h1("2. Duas formas de operar no dia do evento")
p(
    "Dependendo da estrutura do local do evento, você pode optar por uma das duas estratégias "
    "abaixo — ou preparar as duas como plano principal e plano de contingência."
)

h2("Cenário A — Rede local com roteador Wi-Fi")
p(
    "O sistema roda em um notebook levado pela equipe, dentro do próprio local do evento. Um "
    "roteador Wi-Fi cria uma rede local isolada e os dispositivos dos vendedores se conectam a "
    "ela para acessar o sistema. <b>Não depende de internet</b> — funciona mesmo sem sinal de "
    "celular ou Wi-Fi do local."
)
h2("Cenário B — Nuvem com Starlink + Railway")
p(
    "O sistema fica hospedado na internet, em um serviço de nuvem (Railway). O acesso à internet "
    "no local do evento é fornecido pela antena <b>Starlink</b>. Qualquer dispositivo conectado à "
    "rede Wi-Fi do Starlink acessa o sistema normalmente pelo navegador, como qualquer site."
)

data_table(
    ["Critério", "Cenário A — Wi-Fi local", "Cenário B — Starlink + Railway"],
    [
        ["Depende de internet?", "Não", "Sim (via Starlink)"],
        ["Onde ficam os dados", "No notebook servidor", "Na nuvem (Railway)"],
        ["Complexidade de preparo", "Baixa/média", "Média (feito uma vez, com antecedência)"],
        ["Custo recorrente", "Nenhum extra", "Assinatura Starlink + uso do Railway"],
        ["Resiliência a falhas", "Depende do notebook/roteador local", "Depende do link Starlink e do Railway"],
        ["Acesso remoto (fora do local)", "Não", "Sim, de qualquer lugar com internet"],
        ["Indicado para", "Locais sem internet confiável, orçamento controlado", "Locais sem nenhuma infraestrutura de rede, ou eventos em vários pontos simultâneos"],
    ],
    [4.6 * cm, 6.0 * cm, 6.2 * cm],
)
tip(
    "Recomendação",
    "Sempre que possível, prepare os <b>dois cenários com antecedência</b>. Tenha o notebook "
    "configurado para rodar localmente (Cenário A) mesmo que o plano principal seja o Cenário B — "
    "isso vira o seu <b>plano de contingência</b> caso o Starlink falhe no dia do evento.",
)

page_break()

# ---------------------------------------------------------------------------
# 3. Cenário A
# ---------------------------------------------------------------------------

h1("3. Cenário A — Rede local com roteador Wi-Fi")

h2("3.1 O que você vai precisar")
bullets([
    "Um notebook (Windows) para atuar como <b>servidor</b>, com o projeto Totem instalado.",
    "Um roteador Wi-Fi (pode ser um roteador portátil de viagem ou o próprio roteador doméstico levado para o evento).",
    "Cabo de rede (opcional, mas recomendado para ligar o notebook servidor ao roteador com mais estabilidade que o Wi-Fi).",
    "Tablets, celulares e/ou notebooks para os vendedores, com navegador atualizado (Chrome, Edge ou Safari).",
    "Um estabilizador/no-break para o notebook e o roteador, se a energia local for instável.",
])

h2("3.2 Preparar o servidor (com antecedência, antes do dia do evento)")
p("Esta etapa deve ser feita em casa ou no escritório, <b>nunca deixe para o dia do evento</b>.")

h3("Passo 1 — Instalar o Python")
p(
    "Baixe o Python 3.11 (ou mais recente) em <b>python.org/downloads</b>. Durante a instalação no "
    "Windows, marque a opção <b>“Add Python to PATH”</b> antes de clicar em instalar — sem isso, os "
    "comandos abaixo não vão funcionar no terminal."
)

h3("Passo 2 — Copiar os arquivos do projeto")
p("Copie a pasta completa do projeto Totem para o notebook servidor (ex.: via pendrive, ou clonando o repositório Git, se disponível).")

h3("Passo 3 — Criar o ambiente e instalar as dependências")
p("Abra o <b>PowerShell</b> na pasta do projeto e execute:")
code([
    "python -m venv .venv",
    ".\\.venv\\Scripts\\Activate.ps1",
    "pip install -r requirements.txt",
])

h3("Passo 4 — Configurar as variáveis de ambiente")
p(
    "Copie o arquivo <b>.env.example</b> e renomeie a cópia para <b>.env</b>. Abra-o em um editor de "
    "texto e preencha, no mínimo, a chave de segurança da sessão:"
)
code([
    "TOTEM_SECRET_KEY=cole-aqui-uma-chave-longa-e-aleatoria",
    "TOTEM_ADMIN_USER=admin_evento",
    "TOTEM_ADMIN_PASS=defina-uma-senha-forte",
    "TOTEM_SELLER_NAME=Vendedor Padrão",
    "TOTEM_SELLER_EMAIL=vendedor@odontomaster.com.br",
    "TOTEM_SELLER_PASS=defina-uma-senha-forte",
])
p("Para gerar uma chave aleatória segura para <b>TOTEM_SECRET_KEY</b>, execute:")
code(["python -c \"import secrets; print(secrets.token_hex(32))\""])
warn(
    "Não pule esta etapa",
    "Se <b>TOTEM_SECRET_KEY</b> não for definida, o sistema gera uma chave aleatória a cada vez que "
    "for iniciado — isso <b>invalida todos os logins</b> sempre que o programa for reiniciado. "
    "Defina uma chave fixa antes do evento.",
)

h3("Passo 5 — Testar e cadastrar o evento com antecedência")
p(
    "Ainda em casa/escritório, rode o sistema uma vez para testar e já cadastrar tudo o que puder: "
    "evento, produtos, estoque inicial, promoções e vendedores. Isso evita perda de tempo no dia do evento."
)
code(["python app.py"])
p("Acesse no navegador: <font face='Courier'>http://localhost:5000/admin</font> e <font face='Courier'>http://localhost:5000/vendedor</font>.")

h3("Passo 6 — Fazer um backup do banco de dados")
p(
    "Depois de cadastrar tudo, copie o arquivo <b>database\\totem.sqlite3</b> para um pendrive ou "
    "pasta na nuvem. Esse é o seu <b>ponto de restauração</b> caso algo dê errado durante os "
    "cadastros no dia do evento."
)

h2("3.3 Configurar a rede Wi-Fi no dia do evento")
steps([
    "Posicione o roteador em um ponto central do espaço, próximo de uma tomada, e ligue-o.",
    "Conecte o notebook servidor ao roteador — de preferência com <b>cabo de rede</b>; se não houver entrada de rede, conecte-o ao Wi-Fi do próprio roteador.",
    "Descubra o endereço IP local do notebook servidor: abra o PowerShell e digite <font face='Courier'>ipconfig</font>; anote o número em <b>“Endereço IPv4”</b> da rede conectada (ex.: 192.168.0.105).",
    "Se possível, acesse as configurações do roteador e faça uma <b>reserva de IP (DHCP reservation)</b> para o notebook servidor — assim o endereço não muda durante o evento.",
    "Verifique se o roteador tem alguma opção de <b>“isolamento de cliente” (AP/Client Isolation)</b> e <b>desative-a</b>. Roteadores de hotéis e centros de convenções costumam ativar isso por padrão, o que impediria os vendedores de alcançar o servidor.",
    "Libere a porta 5000 no Firewall do Windows do notebook servidor: Painel de Controle → Windows Defender Firewall → Configurações Avançadas → Regras de Entrada → Nova Regra → Porta → TCP → 5000 → Permitir a conexão.",
])

h2("3.4 Ligar o sistema")
steps([
    "No notebook servidor, abra o PowerShell na pasta do projeto e ative o ambiente virtual.",
    "Execute o comando abaixo e <b>deixe a janela aberta</b> durante todo o evento (pode minimizar, nunca fechar):",
])
code([".\\.venv\\Scripts\\Activate.ps1", "python app.py"])
tip(
    "Evite que o notebook “durma”",
    "Nas Configurações de Energia do Windows, defina para <b>nunca suspender</b> e <b>nunca desligar a tela</b> "
    "enquanto conectado à energia. Um notebook em modo de suspensão interrompe o servidor e desconecta "
    "todos os vendedores.",
)

h2("3.5 Conectar os dispositivos dos vendedores")
steps([
    "Conecte cada tablet, celular ou notebook de vendedor à <b>mesma rede Wi-Fi</b> criada pelo roteador.",
    "Abra o navegador e acesse o endereço do servidor seguido da porta e do caminho do vendedor, por exemplo: <font face='Courier'>http://192.168.0.105:5000/vendedor</font>",
    "Faça login com a conta de vendedor cadastrada previamente.",
])
tip(
    "Facilite o acesso com um QR Code",
    "Gere um QR Code apontando para o endereço do servidor (ex.: <font face='Courier'>http://192.168.0.105:5000/vendedor</font>) "
    "usando qualquer gerador gratuito de QR Code, e imprima/exiba para os vendedores escanearem com a "
    "câmera do celular — evita erro de digitação do endereço.",
)

h2("3.6 Checklist — Cenário A")
h3("Antes do evento (com antecedência)")
checklist([
    "Python instalado e projeto copiado para o notebook servidor.",
    "Dependências instaladas (<font face='Courier'>pip install -r requirements.txt</font>).",
    "Arquivo <font face='Courier'>.env</font> criado e preenchido, com <font face='Courier'>TOTEM_SECRET_KEY</font> fixa.",
    "Evento, produtos, estoque, promoções e vendedores já cadastrados.",
    "Backup do arquivo <font face='Courier'>database\\totem.sqlite3</font> feito e guardado em local seguro.",
    "Testado o login de admin e de ao menos um vendedor.",
])
h3("No dia do evento")
checklist([
    "Roteador Wi-Fi posicionado, ligado e testado.",
    "Notebook servidor conectado ao roteador e com IP local identificado (e, se possível, reservado).",
    "Isolamento de cliente desativado no roteador.",
    "Porta 5000 liberada no Firewall do Windows.",
    "Servidor iniciado (<font face='Courier'>python app.py</font>) e janela mantida aberta.",
    "Energia do notebook configurada para nunca suspender.",
    "Ao menos um dispositivo de vendedor testado com sucesso antes da abertura ao público.",
    "QR Code ou anotação do endereço do servidor disponível para os vendedores.",
])

page_break()

# ---------------------------------------------------------------------------
# 4. Cenário B
# ---------------------------------------------------------------------------

h1("4. Cenário B — Nuvem com Starlink + Railway")
p(
    "Neste cenário, o sistema não roda em um notebook local: ele fica hospedado permanentemente na "
    "internet, em um serviço de nuvem chamado <b>Railway</b>. No local do evento, a conexão à internet "
    "é feita pela antena <b>Starlink</b>, que também cria uma rede Wi-Fi para os dispositivos."
)

h2("4.1 O que você vai precisar")
bullets([
    "Kit Starlink (antena + roteador) com plano de dados ativo, já testado previamente.",
    "Conta no <b>Railway</b> (railway.app), com forma de pagamento cadastrada (cobrança conforme uso).",
    "Conta no <b>GitHub</b> com o código-fonte do projeto Totem em um repositório (recomendado: privado).",
    "Tablets, celulares e/ou notebooks para os vendedores, com navegador atualizado.",
])

h2("4.2 Publicar o sistema no Railway (feito com antecedência)")
p("Esta etapa é feita <b>uma única vez</b>, em qualquer lugar com internet — não precisa ser no dia do evento.")

h3("Passo 1 — Subir o código para o GitHub")
p("Envie a pasta do projeto para um repositório no GitHub (de preferência privado, por conter regras de negócio internas).")

h3("Passo 2 — Criar o projeto no Railway")
p(
    "Em railway.app, crie um novo projeto e escolha <b>“Deploy from GitHub repo”</b>, selecionando o "
    "repositório do Totem."
)

h3("Passo 3 — Definir o comando de início")
p(
    "Em <b>Settings → Deploy</b>, defina o comando de início (Start Command) do servidor de produção:"
)
code(["gunicorn -w 1 -b 0.0.0.0:$PORT main:app"])
p(
    "O parâmetro <font face='Courier'>-w 1</font> mantém <b>um único processo</b> — importante porque "
    "o banco de dados é um arquivo SQLite, que não deve ser acessado por múltiplos processos ao mesmo tempo."
)

h3("Passo 4 — Configurar as variáveis de ambiente")
p("Em <b>Settings → Variables</b>, cadastre as mesmas variáveis do Cenário A:")
code([
    "TOTEM_SECRET_KEY=cole-aqui-uma-chave-longa-e-aleatoria",
    "TOTEM_ADMIN_USER=admin_evento",
    "TOTEM_ADMIN_PASS=defina-uma-senha-forte",
    "TOTEM_SELLER_NAME=Vendedor Padrão",
    "TOTEM_SELLER_EMAIL=vendedor@odontomaster.com.br",
    "TOTEM_SELLER_PASS=defina-uma-senha-forte",
    "WAKE_TOKEN=token-se-for-usar-importacao-wake",
])

h3("Passo 5 — Adicionar um Volume persistente (etapa crítica)")
critical(
    "Sem isso, os dados do evento podem se perder",
    "O banco de dados SQLite é <b>um arquivo dentro da pasta do projeto</b>. Por padrão, serviços como "
    "o Railway recriam os arquivos do zero a cada novo deploy ou reinício — o que apagaria todo o "
    "cadastro do evento. Para evitar isso, configure um <b>Volume persistente</b>:",
)
steps([
    "No projeto do Railway, acesse <b>Settings → Volumes → Add Volume</b>.",
    "Defina o caminho de montagem (<i>mount path</i>) exatamente como <font face='Courier'>/app/database</font>.",
    "Salve e faça um novo deploy para o volume entrar em vigor.",
])
p(
    "A partir disso, o arquivo <font face='Courier'>totem.sqlite3</font> passa a ser gravado dentro do "
    "volume e sobrevive a reinícios e novos deploys."
)

h3("Passo 6 — Publicar e obter a URL")
p(
    "Aguarde o build finalizar e copie a URL pública gerada pelo Railway (formato "
    "<font face='Courier'>https://seu-projeto.up.railway.app</font>). Opcionalmente, configure um "
    "domínio próprio em <b>Settings → Networking</b>."
)

h3("Passo 7 — Testar e cadastrar o evento com antecedência")
p(
    "Acesse a URL publicada e repita o cadastro do evento, produtos, estoque, promoções e vendedores — "
    "assim como no Cenário A. Teste o login de admin e de ao menos um vendedor pela internet, de "
    "preferência em uma rede diferente da sua rede doméstica (ex.: dados móveis), para simular o acesso real."
)

h3("Passo 8 — Teste de carga leve (recomendado)")
p(
    "Peça para 2–3 pessoas acessarem o sistema simultaneamente e simularem uma venda completa, para "
    "confirmar que o Railway responde bem antes do dia do evento."
)

h2("4.3 No dia do evento (com Starlink)")
steps([
    "Monte a antena Starlink em um local com <b>visão livre do céu</b> (sem árvores, tendas ou estruturas por cima) e conecte-a à energia.",
    "Aguarde o alinhamento automático da antena — o aplicativo Starlink mostrará o status <b>“Online”</b> quando estiver pronto.",
    "Confirme que o roteador Wi-Fi do Starlink está ativo e anote o nome da rede (SSID) e a senha.",
    "Conecte os dispositivos dos vendedores à rede Wi-Fi do Starlink.",
    "Em cada dispositivo, acesse pelo navegador: <font face='Courier'>https://seu-projeto.up.railway.app/vendedor</font>",
    "Faça login com a conta de vendedor cadastrada previamente.",
])

h2("4.4 Cuidados e boas práticas")
bullets([
    "Monitore o consumo de dados do plano Starlink durante o evento, se o plano tiver limite.",
    "Tenha um <b>plano B de internet</b> (ex.: hotspot 4G/5G de um celular) para o caso do sinal Starlink cair.",
    "Se possível, acompanhe o painel do Railway (aba <b>Deployments/Logs</b>) durante o evento por outro dispositivo com internet, para identificar erros rapidamente.",
    "Nunca divulgue publicamente a URL do sistema fora da equipe — mesmo exigindo login, evite exposição desnecessária.",
    "Evite fazer alterações de código/deploy durante o evento; qualquer novo deploy reinicia o servidor por alguns segundos.",
])

h2("4.5 Checklist — Cenário B")
h3("Antes do evento (com antecedência)")
checklist([
    "Repositório no GitHub com o código do projeto.",
    "Projeto criado no Railway e comando de início configurado (<font face='Courier'>gunicorn -w 1 -b 0.0.0.0:$PORT main:app</font>).",
    "Variáveis de ambiente cadastradas, com <font face='Courier'>TOTEM_SECRET_KEY</font> fixa.",
    "Volume persistente configurado em <font face='Courier'>/app/database</font>.",
    "Deploy concluído e URL pública testada.",
    "Evento, produtos, estoque, promoções e vendedores já cadastrados na nuvem.",
    "Teste de acesso feito por uma rede externa (dados móveis).",
    "Kit Starlink testado e funcionando previamente.",
])
h3("No dia do evento")
checklist([
    "Antena Starlink posicionada com visão livre do céu e status “Online” confirmado.",
    "Rede Wi-Fi do Starlink identificada (nome e senha) e compartilhada com a equipe.",
    "Dispositivos dos vendedores conectados à rede Wi-Fi do Starlink.",
    "Login testado em ao menos um dispositivo antes da abertura ao público.",
    "Plano B de internet (hotspot móvel) disponível e testado.",
])

page_break()

# ---------------------------------------------------------------------------
# 5. Comparativo
# ---------------------------------------------------------------------------

h1("5. Comparativo rápido entre os cenários")
data_table(
    ["Situação", "Melhor opção"],
    [
        ["Local sem nenhuma internet disponível e sem sinal de celular", "Cenário A (Wi-Fi local)"],
        ["Local totalmente sem infraestrutura de rede (área externa, estande avulso)", "Cenário B (Starlink + Railway)"],
        ["Orçamento restrito, sem custo recorrente de nuvem", "Cenário A (Wi-Fi local)"],
        ["Necessidade de acompanhar vendas remotamente durante o evento", "Cenário B (Starlink + Railway)"],
        ["Evento em múltiplos estandes/salas simultâneos que precisam compartilhar o mesmo estoque", "Cenário B (Starlink + Railway)"],
        ["Time pequeno, sem alguém disponível para configurar Railway/Starlink", "Cenário A (Wi-Fi local)"],
    ],
    [10.0 * cm, 6.8 * cm],
)
tip(
    "Dica final de resiliência",
    "O ideal é preparar o Cenário A como contingência mesmo quando o plano principal for o Cenário B: "
    "leve o notebook configurado e um roteador Wi-Fi reserva. Se o Starlink falhar, a equipe consegue "
    "trocar para a rede local em poucos minutos.",
)

page_break()

# ---------------------------------------------------------------------------
# 6. Backup
# ---------------------------------------------------------------------------

h1("6. Backup e continuidade dos dados")

h2("6.1 Cenário A — Wi-Fi local")
bullets([
    "Copie o arquivo <font face='Courier'>database\\totem.sqlite3</font> para um pendrive ou pasta na "
    "nuvem periodicamente durante o evento (ex.: a cada 1–2 horas) e novamente ao final.",
    "Se possível, feche o programa (<font face='Courier'>Ctrl+C</font> no terminal) antes de copiar o "
    "arquivo, para garantir que não há gravação em andamento.",
])

h2("6.2 Cenário B — Starlink + Railway")
bullets([
    "Com o Volume persistente configurado corretamente (ver seção 4.2), o banco de dados sobrevive a "
    "reinícios automaticamente — não é necessário backup manual do arquivo.",
    "Ainda assim, é recomendado exportar o relatório financeiro em PDF (painel Admin → Financeiro → "
    "Exportar PDF) ao final do evento, como registro adicional.",
])

h2("6.3 Cuidado com o botão “Reiniciar sistema”")
critical(
    "Use com extremo cuidado",
    "O painel administrativo possui uma função de <b>“Reiniciar sistema”</b>, que restaura o estado "
    "inicial e <b>apaga dados cadastrados</b>. Nunca utilize essa função durante ou após um evento real "
    "sem ter certeza absoluta do que está fazendo — e sempre com um backup recente em mãos.",
)

page_break()

# ---------------------------------------------------------------------------
# 7. Segurança
# ---------------------------------------------------------------------------

h1("7. Segurança e credenciais")
bullets([
    "Troque o usuário e a senha padrão do administrador (<font face='Courier'>TOTEM_ADMIN_USER</font> / "
    "<font face='Courier'>TOTEM_ADMIN_PASS</font>) antes de qualquer evento real.",
    "Use senhas fortes e diferentes para cada conta de vendedor cadastrada.",
    "Não deixe credenciais salvas em dispositivos compartilhados entre a equipe (evite “lembrar senha” em navegadores de tablets compartilhados).",
    "A chave <font face='Courier'>TOTEM_SECRET_KEY</font> não deve ser alterada depois de definida em "
    "produção — trocá-la invalida todas as sessões ativas — e nunca deve ser compartilhada publicamente.",
    "No Cenário B, mantenha o repositório do GitHub como <b>privado</b> e restrinja o acesso ao painel do Railway apenas à equipe responsável.",
])

page_break()

# ---------------------------------------------------------------------------
# 8. Troubleshooting
# ---------------------------------------------------------------------------

h1("8. Solução de problemas comuns")

h3("O vendedor não consegue acessar o endereço do servidor (Cenário A)")
bullets([
    "Confirme que o dispositivo está conectado à <b>mesma rede Wi-Fi</b> do roteador do evento (não à rede de dados móveis).",
    "Verifique se o roteador não tem o <b>isolamento de cliente</b> ativado (ver seção 3.3).",
    "Confirme se a porta 5000 está liberada no Firewall do Windows do notebook servidor.",
    "Confirme se o endereço IP digitado ainda é o correto — reinicializações do roteador podem alterar o IP se não houver reserva de DHCP.",
])

h3("A página fica carregando infinitamente ou “sem conexão” (Cenário B)")
bullets([
    "Verifique o status da antena Starlink no aplicativo (deve estar “Online”).",
    "Teste o acesso à internet em outro site pelo mesmo dispositivo, para confirmar se o problema é de rede ou do sistema.",
    "Acesse o painel do Railway e confira se o serviço está no ar (aba Deployments) e os logs recentes.",
])

h3("Erro inesperado (páginas de erro 404/500) durante o uso")
bullets([
    "Anote a ação que causou o erro e a hora aproximada.",
    "No Cenário A, verifique o terminal onde o servidor está rodando — mensagens de erro aparecem ali.",
    "No Cenário B, verifique os logs em Railway → Deployments → Logs.",
    "Caso não seja possível resolver no momento, oriente o vendedor a tentar novamente em alguns instantes; a maioria dos erros de rede é temporária.",
])

h3("O banco de dados parece “zerado” após reiniciar o serviço no Railway")
bullets([
    "Isso indica que o <b>Volume persistente</b> não foi configurado, ou foi montado em um caminho diferente de <font face='Courier'>/app/database</font>.",
    "Revise a seção 4.2 (Passo 5) e configure o volume corretamente antes do próximo evento.",
])

h3("As sessões de login caem com frequência")
bullets([
    "Confirme que a variável <font face='Courier'>TOTEM_SECRET_KEY</font> está definida e fixa no ambiente (não deixada em branco).",
    "No Cenário B, verifique se o serviço não está reiniciando sozinho com frequência (aba Deployments do Railway).",
])

page_break()

# ---------------------------------------------------------------------------
# 9. Checklist final
# ---------------------------------------------------------------------------

h1("9. Checklist final consolidado")
p("Use esta página como conferência rápida no dia do evento, independentemente do cenário escolhido.")

h2("Uma semana antes")
checklist([
    "Cenário escolhido definido (A, B, ou os dois).",
    "Sistema testado de ponta a ponta (login, catálogo, checkout, AUT, confirmação).",
    "Evento, produtos, estoque, promoções e vendedores cadastrados.",
    "Credenciais de admin e vendedores revisadas e fortes.",
])

h2("Na véspera")
checklist([
    "Equipamentos carregados e testados (notebook, roteador, ou verificação do status do Railway/Starlink).",
    "Backup do banco de dados feito (Cenário A) ou volume persistente confirmado (Cenário B).",
    "Endereço de acesso (IP local ou URL) anotado e, se possível, QR Code preparado.",
])

h2("No dia, antes da abertura")
checklist([
    "Rede/servidor ligados e testados com pelo menos um dispositivo de vendedor.",
    "Plano de contingência (rede alternativa) disponível e testado.",
    "Equipe orientada sobre a URL/endereço de acesso e sobre este roteiro.",
])

h2("Durante o evento")
checklist([
    "Monitorar periodicamente a conexão e o funcionamento do sistema.",
    "Fazer backups intermediários (Cenário A) ou acompanhar o painel do Railway (Cenário B).",
])

h2("Após o evento")
checklist([
    "Backup final do banco de dados ou exportação do relatório financeiro em PDF.",
    "Encerrar o servidor local com segurança (Cenário A) ou apenas manter o Railway ativo (Cenário B).",
    "Registrar aprendizados e ajustes para o próximo evento.",
])

spacer(20)
story.append(
    Paragraph(
        "Odonto Master — Sistema Totem · Documento de apoio operacional interno.",
        ParagraphStyle("footer_note", parent=base["Normal"], fontName="Helvetica-Oblique",
                        fontSize=8.6, textColor=MUTED, alignment=TA_CENTER),
    )
)


# ---------------------------------------------------------------------------
# Cabeçalho / rodapé de página + geração
# ---------------------------------------------------------------------------

def _on_page(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 0.9 * cm, width, 0.9 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawString(1.7 * cm, height - 0.62 * cm, "TOTEM ODONTO MASTER — ROTEIRO DE APLICAÇÃO EM EVENTOS")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawRightString(width - 1.7 * cm, 0.8 * cm, f"Página {doc.page}")
    canvas.drawString(1.7 * cm, 0.8 * cm, "Uso interno — Odonto Master")
    canvas.restoreState()


def _on_cover(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, height, stroke=0, fill=1)
    canvas.restoreState()


doc = SimpleDocTemplate(
    OUT_PATH,
    pagesize=A4,
    leftMargin=1.7 * cm,
    rightMargin=1.7 * cm,
    topMargin=1.6 * cm,
    bottomMargin=1.5 * cm,
    title="Roteiro de Aplicação do Sistema Totem em Eventos",
    author="Odonto Master",
)

doc.build(story, onFirstPage=_on_cover, onLaterPages=_on_page)
print(f"PDF gerado em: {OUT_PATH}")
