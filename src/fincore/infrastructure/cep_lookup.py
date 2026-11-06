"""Adapter de consulta de CEP pelo serviço público ViaCEP."""

import re

import httpx


class CepLookupError(ValueError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


async def lookup_cep(value: str) -> dict[str, str]:
    cep = re.sub(r"\D", "", value)
    if len(cep) != 8:
        raise CepLookupError("Informe um CEP com oito dígitos.", 400)
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(f"https://viacep.com.br/ws/{cep}/json/")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CepLookupError(
            "O serviço de CEP está indisponível. Preencha o endereço manualmente."
        ) from exc

    if not isinstance(payload, dict) or payload.get("erro") is True:
        raise CepLookupError("CEP não encontrado. Preencha o endereço manualmente.", 404)
    return {
        "postal_code": str(payload.get("cep") or f"{cep[:5]}-{cep[5:]}")[:9],
        "street": str(payload.get("logradouro") or "")[:160],
        "district": str(payload.get("bairro") or "")[:100],
        "city": str(payload.get("localidade") or "")[:100],
        "state": str(payload.get("uf") or "").upper()[:2],
    }
