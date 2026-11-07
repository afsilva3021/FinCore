from fincore.domain.categorization import suggest, tokenize, train

HISTORY = [
    ("Conta de luz Enel 03/2026", "energia"),
    ("Pgto ENEL energia elétrica", "energia"),
    ("Luz escritório Enel", "energia"),
    ("Aluguel sala comercial", "aluguel"),
    ("Aluguel março imobiliária", "aluguel"),
    ("Posto Shell gasolina", "combustivel"),
    ("Gasolina posto Ipiranga", "combustivel"),
]


def test_tokenize_ignores_case_accents_numbers_and_noise_words() -> None:
    assert tokenize("PGTO Energia Elétrica 03/2026 - Enel") == ["energia", "eletrica", "enel"]


def test_suggests_the_category_used_for_similar_descriptions() -> None:
    model = train(HISTORY)
    result = suggest(model, "ENEL conta de luz abril")
    assert result is not None
    assert result.label == "energia"
    assert result.confidence >= 0.6
    assert suggest(model, "gasolina posto BR").label == "combustivel"


def test_no_suggestion_without_enough_evidence() -> None:
    model = train(HISTORY)
    assert suggest(model, "assinatura software contábil") is None  # palavras nunca vistas
    assert suggest(train(HISTORY[:2]), "luz enel") is None  # histórico pequeno demais
    assert suggest(model, "") is None


def test_ambiguous_description_is_not_forced() -> None:
    model = train([("serviço mensal", "a"), ("serviço mensal", "b"), ("serviço mensal", "c")])
    assert suggest(model, "serviço mensal") is None
