# Integração API Consultar.io - Validação de CRO

## Visão Geral

Implementação completa da integração com a API de validação de CRO (Conselho Regional de Odontologia) do [Consultar.io](https://consultar.io).

A integração valida automaticamente o registro profissional CRO durante o checkout no Totem, armazenando os dados validados na transação.

## Documentação da API

- **Documentação oficial**: https://docs.consultar.io/api/cro/
- **Autenticação**: Token (header `Authorization: Token <token>`)
- **Custo**: R$ 0,20 por consulta/busca bem-sucedida
- **Timeout recomendado**: 30 segundos

## Arquivos Modificados/Criados

### Novo Módulo: `consultar_io.py`
Módulo Python de integração com a API Consultar.io:

- **`consultar_cro(uf, numero_registro, categoria)`**: Valida CRO específico
- **`buscar_cro(nome_razao_social, categoria)`**: Busca por nome (até 100 resultados)
- **Classes de exceção**: `ConsultarIOError`, `RegistroNaoEncontrado`, `CredenciaisInvalidas`, `RequisicaoInvalida`
- **`CATEGORIAS_CRO`**: Mapeamento código → descrição (cd, tsb, tpd, etc.)

### Banco de Dados: `database.py`

**Novas colunas na tabela `transactions`**:
- `client_cro_uf` (TEXT): UF do registro CRO
- `client_cro_numero` (TEXT): Número do registro
- `client_cro_categoria` (TEXT): Código da categoria (ex.: "cd")
- `client_cro_validated` (INTEGER): Flag 0/1 indicando validação bem-sucedida
- `client_cro_validation_data` (TEXT): JSON com resposta da API (nome, situação, etc.)

**Funções alteradas**:
- `_ensure_transactions_cro_columns()`: Nova função de migração
- `init_db()`: Inclui chamada da migração CRO
- `create_transaction()`: Novos parâmetros `client_cro_*`

### Backend: `app.py`

**Rota modificada: `/api/transacoes` (POST)**:
- Extrai campos `cro_uf`, `cro_numero`, `cro_categoria` do payload `client`
- Chama `consultar_cro()` se todos os campos CRO estiverem preenchidos
- Valida de forma **não-bloqueante**: erro de validação não impede a venda (log apenas)
- Armazena flag `client_cro_validated` e JSON `client_cro_validation_data`

### Frontend: `templates/payment.html`

**Nova seção "Registro Profissional (CRO)"** adicionada após o CPF:
- **Campo UF** (select): Sigla do estado do CRO
- **Campo Número** (input text): Número do registro (até 7 dígitos)
- **Campo Categoria** (select): Tipo de profissional/estabelecimento
  - Cirurgião Dentista (cd)
  - Técnico em Saúde Bucal (tsb)
  - Técnico em Prótese Dentária (tpd)
  - Auxiliar em Saúde Bucal (asb)
  - Auxiliar de Prótese Dentária (apd)
  - Estagiário (estagiario)
  - Clínica/Entidade (clinica-assistencia)
  - Laboratório (laboratorio)
  - Comércio/Indústria (comercio-industria)

Todos os campos são **obrigatórios** (`required`).

### JavaScript: `static/js/payment-form.js`

**Funções alteradas**:
- `maskCRO()`: Remove caracteres não-numéricos, limita a 7 dígitos
- `getData()`: Inclui campos `cro_uf`, `cro_numero`, `cro_categoria`
- `save()` / restauração: Persiste e restaura dados CRO do sessionStorage
- Listener `input`: Aplica máscara ao campo `cro_numero`

### CSS: `static/css/payment.css`

**Novos estilos**:
- `.payment__section-title--cro`: Separador visual (borda superior)
- `.payment__section-title--address`: Espaçamento para seção de endereço
- `.payment-cro__hint`: Texto explicativo sobre validação CRO

## Configuração

### Token de Autenticação

O token pode ser configurado via:

1. **Variável de ambiente** (recomendado em produção):
   ```bash
   export CONSULTAR_IO_TOKEN="seu-token-aqui"
   ```

2. **Hard-coded** (padrão atual para desenvolvimento):
   ```python
   # consultar_io.py
   CONSULTAR_IO_TOKEN = "da3791449055fe80c705cb489dcb9516d50df58b"
   ```

**IMPORTANTE**: Em produção, sempre use variável de ambiente e nunca commite o token no repositório.

### Dependências

O módulo `requests` já está listado no `requirements.txt`:
```
requests>=2.31.0
```

## Fluxo de Validação

```
1. Cliente preenche formulário de checkout
   └─ Inclui: UF, número CRO, categoria
   
2. Frontend envia dados para /api/transacoes
   
3. Backend (app.py)
   ├─ Extrai campos CRO
   ├─ Se todos preenchidos → chama consultar_cro()
   │  ├─ SUCESSO → cro_validated=True, salva JSON da API
   │  └─ ERRO → cro_validated=False, log de warning (venda prossegue)
   └─ Registra transação com dados CRO
   
4. Transação salva no banco com:
   ├─ Dados informados (uf, numero, categoria)
   └─ Resultado da validação (flag + JSON ou NULL)
```

## Tratamento de Erros

A validação é **não-bloqueante**:

- **404 (Registro não encontrado)**: Venda prossegue, `cro_validated=False`
- **403 (Sem créditos/Plano inativo)**: Venda prossegue, warning no log
- **400 (Parâmetros inválidos)**: Venda prossegue, warning no log
- **500 (Erro servidor API)**: Venda prossegue, warning no log
- **Timeout/Rede**: Venda prossegue, warning no log

Apenas erros críticos do próprio sistema (banco de dados, estoque) bloqueiam a venda.

## Testes

### Script de Teste

Execute o script de teste incluído:

```bash
python test_consultar_io.py
```

**ATENÇÃO**: Testes de API reais consomem R$ 0,20 por consulta. O script pede confirmação antes de executar testes que gastam créditos.

### Teste Manual (Frontend)

1. Inicie o servidor Flask
2. Acesse a página de pagamento (`/pagamento`)
3. Preencha os campos CRO com dados válidos/inválidos
4. Finalize a compra
5. Verifique nos logs do servidor se a validação foi tentada
6. Consulte a transação no banco: `SELECT client_cro_* FROM transactions ORDER BY id DESC LIMIT 1;`

## Consultas ao Banco de Dados

### Ver todas as transações com CRO validado
```sql
SELECT 
    order_number,
    client_name,
    client_cro_uf || '-' || client_cro_numero AS cro,
    client_cro_categoria,
    client_cro_validated,
    json_extract(client_cro_validation_data, '$.nome_razao_social') AS nome_validado,
    json_extract(client_cro_validation_data, '$.situacao') AS situacao_cro
FROM transactions
WHERE client_cro_numero IS NOT NULL
ORDER BY created_at DESC;
```

### Estatísticas de validação
```sql
SELECT 
    client_cro_validated,
    COUNT(*) AS total,
    COUNT(DISTINCT client_cro_uf || '-' || client_cro_numero) AS cros_unicos
FROM transactions
WHERE client_cro_numero IS NOT NULL
GROUP BY client_cro_validated;
```

## Códigos de Categoria

| Código | Descrição |
|--------|-----------|
| `cd` | Cirurgião Dentista |
| `tsb` | Técnico em Saúde Bucal |
| `tpd` | Técnico em Prótese Dentária |
| `asb` | Auxiliar em Saúde Bucal |
| `apd` | Auxiliar de Prótese Dentária |
| `estagiario` | Estagiário |
| `clinica-assistencia` | Clínica/Entidade Prestadora de Assistência Odontológica |
| `laboratorio` | Laboratório de Prótese Dentária |
| `comercio-industria` | Comércio/Indústria de Produtos Odontológicos |

## Próximos Passos (Opcionais)

1. **Painel Admin**: Exibir dados de CRO validado nas telas de transações/relatórios
2. **Indicador visual**: Badge "CRO Validado" nas transações com `client_cro_validated=True`
3. **Revalidação**: Botão para revalidar CRO de transações antigas
4. **Busca por nome**: Implementar autocomplete usando `buscar_cro()` (custa R$ 0,20 por busca)
5. **Cache**: Armazenar resultado de validações recentes (ex.: Redis) para evitar consultas duplicadas no mesmo dia
6. **Webhook/Notificação**: Alertar admin quando validação falhar por falta de créditos

## Custos

- **Consulta individual**: R$ 0,20 por CRO validado
- **Busca por nome**: R$ 0,20 por busca (retorna até 100 resultados)
- **Apenas respostas 200 e 404** consomem créditos (erros 400/403/500 não cobram)

**Estimativa**: 500 vendas/mês = R$ 100,00 em créditos (considerando 100% validação bem-sucedida)

## Suporte

- **Documentação API**: https://docs.consultar.io/
- **Suporte Consultar.io**: Conforme página de suporte na documentação
- **Issues deste projeto**: [Adicionar link do repositório se aplicável]

## Licença

Mesma licença do projeto Totem Odonto Master.
