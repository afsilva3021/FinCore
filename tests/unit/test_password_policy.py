import pytest

from fincore.application.identity import password_problems, validate_password


@pytest.mark.parametrize(
    "password",
    [
        "Cavalo-Bateria-42",
        "correto cavalo grampo 9",
        "Ação-Contábil-2026",
        "ZZTOP#guitarra",
    ],
)
def test_accepts_long_passwords_with_three_character_types(password: str) -> None:
    assert password_problems(password, email="ana@empresa.com", full_name="Ana Souza") == []


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("Curta-1", "12 a 128"),
        ("x" * 129 + "A1!", "12 a 128"),
        ("somenteletrasminusculas", "3 destes tipos"),
        ("SOMENTEMAIUSCULAS123", "3 destes tipos"),
        ("Aa1!Aa1!Aa1!", "poucos caracteres"),
        ("Mariana-Senha-2026", "seu nome"),
        ("financeiro-Forte-9", "e-mail"),
    ],
)
def test_rejects_weak_passwords_with_a_clear_reason(password: str, fragment: str) -> None:
    problems = password_problems(password, email="financeiro@empresa.com", full_name="Mariana Lima")
    assert any(fragment in problem for problem in problems)


def test_validate_password_raises_with_every_reason_joined() -> None:
    with pytest.raises(ValueError) as error:
        validate_password("abc", email="a@b.com")
    message = str(error.value)
    assert "12 a 128" in message
    assert "3 destes tipos" in message
