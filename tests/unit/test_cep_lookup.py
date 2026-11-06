import pytest

from fincore.infrastructure import cep_lookup


class StubResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class StubClient:
    payload = {}

    def __init__(self, **kwargs):
        assert kwargs["timeout"] == 5.0
        assert kwargs["follow_redirects"] is False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url):
        assert url == "https://viacep.com.br/ws/01001000/json/"
        return StubResponse(self.payload)


@pytest.mark.asyncio
async def test_lookup_cep_returns_normalized_address(monkeypatch):
    StubClient.payload = {
        "cep": "01001-000",
        "logradouro": "Praça da Sé",
        "bairro": "Sé",
        "localidade": "São Paulo",
        "uf": "sp",
    }
    monkeypatch.setattr(cep_lookup.httpx, "AsyncClient", StubClient)
    assert await cep_lookup.lookup_cep("01001-000") == {
        "postal_code": "01001-000",
        "street": "Praça da Sé",
        "district": "Sé",
        "city": "São Paulo",
        "state": "SP",
    }


@pytest.mark.asyncio
async def test_lookup_cep_rejects_invalid_and_unknown_cep(monkeypatch):
    with pytest.raises(cep_lookup.CepLookupError, match="oito dígitos"):
        await cep_lookup.lookup_cep("123")

    StubClient.payload = {"erro": True}
    monkeypatch.setattr(cep_lookup.httpx, "AsyncClient", StubClient)
    with pytest.raises(cep_lookup.CepLookupError, match="não encontrado"):
        await cep_lookup.lookup_cep("01001000")
