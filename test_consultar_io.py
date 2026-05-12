#!/usr/bin/env python3
"""Script de teste para a integração com Consultar.io API de CRO.

Uso:
    python test_consultar_io.py
"""
from consultar_io import (
    consultar_cro,
    buscar_cro,
    ConsultarIOError,
    RegistroNaoEncontrado,
    CredenciaisInvalidas,
    RequisicaoInvalida,
    CATEGORIAS_CRO,
    get_categoria_descricao,
)


def test_categorias():
    """Testa o mapeamento de categorias."""
    print("\n=== Teste: Categorias CRO ===")
    print(f"Total de categorias: {len(CATEGORIAS_CRO)}")
    for codigo, descricao in CATEGORIAS_CRO.items():
        print(f"  {codigo}: {descricao}")
    
    # Testa helper
    assert get_categoria_descricao("cd") == "Cirurgião Dentista"
    assert get_categoria_descricao("CD") == "Cirurgião Dentista"  # case insensitive
    assert get_categoria_descricao("invalido") == "invalido"  # fallback
    print("✓ Categorias OK")


def test_consultar_exemplo():
    """Testa consulta de CRO (exemplo fictício - ajustar para teste real)."""
    print("\n=== Teste: Consultar CRO ===")
    
    # Exemplo: ajuste estes dados para um CRO real válido para testar
    UF_TESTE = "SP"
    NUMERO_TESTE = "123456"
    CATEGORIA_TESTE = "cd"
    
    print(f"Tentando consultar: CRO-{UF_TESTE} {NUMERO_TESTE} (categoria: {CATEGORIA_TESTE})")
    print("NOTA: Ajuste os dados acima para um CRO válido antes de executar este teste.")
    
    try:
        resultado = consultar_cro(
            uf=UF_TESTE,
            numero_registro=NUMERO_TESTE,
            categoria=CATEGORIA_TESTE,
        )
        print("✓ Consulta bem-sucedida:")
        print(f"  UF: {resultado.get('uf')}")
        print(f"  Número: {resultado.get('numero_registro')}")
        print(f"  Categoria: {resultado.get('categoria')}")
        print(f"  Nome/Razão Social: {resultado.get('nome_razao_social')}")
        print(f"  Situação: {resultado.get('situacao')}")
        return True
        
    except RegistroNaoEncontrado as e:
        print(f"✗ Registro não encontrado (esperado para dados fictícios): {e}")
        return False
    except CredenciaisInvalidas as e:
        print(f"✗ Erro de credenciais/créditos: {e}")
        print("  Verifique o token da API e saldo de créditos.")
        return False
    except RequisicaoInvalida as e:
        print(f"✗ Requisição inválida: {e}")
        print("  Verifique os parâmetros (UF, número, categoria).")
        return False
    except ConsultarIOError as e:
        print(f"✗ Erro na API: {e}")
        return False


def test_buscar_exemplo():
    """Testa busca de CRO por nome (exemplo fictício)."""
    print("\n=== Teste: Buscar CRO ===")
    
    NOME_TESTE = "João Silva"
    CATEGORIA_TESTE = "cd"
    
    print(f"Tentando buscar: '{NOME_TESTE}' (categoria: {CATEGORIA_TESTE})")
    print("NOTA: Ajuste o nome acima para um profissional real antes de executar este teste.")
    
    try:
        resultados = buscar_cro(
            nome_razao_social=NOME_TESTE,
            categoria=CATEGORIA_TESTE,
        )
        print(f"✓ Busca retornou {len(resultados)} resultado(s):")
        for i, r in enumerate(resultados[:5], 1):  # Mostra até 5
            print(f"  {i}. CRO-{r.get('uf')} {r.get('numero_registro')} - {r.get('nome_razao_social')}")
        if len(resultados) > 5:
            print(f"  ... e mais {len(resultados) - 5} resultado(s)")
        return True
        
    except RegistroNaoEncontrado as e:
        print(f"✗ Nenhum registro encontrado (esperado para dados fictícios): {e}")
        return False
    except CredenciaisInvalidas as e:
        print(f"✗ Erro de credenciais/créditos: {e}")
        return False
    except RequisicaoInvalida as e:
        print(f"✗ Requisição inválida: {e}")
        return False
    except ConsultarIOError as e:
        print(f"✗ Erro na API: {e}")
        return False


def main():
    """Executa todos os testes."""
    print("=" * 60)
    print("Teste de Integração - Consultar.io API de CRO")
    print("=" * 60)
    
    test_categorias()
    
    print("\n" + "=" * 60)
    print("TESTES DE API (requerem créditos e dados válidos)")
    print("=" * 60)
    print("\nIMPORTANTE: Os testes abaixo consomem créditos da API.")
    print("Cada consulta/busca custa R$ 0,20 (conforme documentação).")
    print("Ajuste os dados de teste para CROs reais antes de executar.\n")
    
    resposta = input("Deseja executar os testes de API? (s/N): ").strip().lower()
    
    if resposta == 's':
        test_consultar_exemplo()
        test_buscar_exemplo()
    else:
        print("\nTestes de API ignorados (economizando créditos).")
    
    print("\n" + "=" * 60)
    print("Testes concluídos")
    print("=" * 60)


if __name__ == "__main__":
    main()
