"""Headers HTTP de segurança aplicados a todas as respostas da aplicação."""

from robyn import Response

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        # Sem 'unsafe-inline': nenhum script inline é executado, o que bloqueia XSS refletido.
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com "
        "https://fonts.googleapis.com",
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com",
        # data: é usado pelos ícones SVG embutidos no CSS do Bootstrap.
        "img-src 'self' data:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Só faz sentido atrás de HTTPS; ativado junto com SECURE_COOKIES.
HSTS_VALUE = "max-age=31536000; includeSubDomains"


def apply_security_headers(response: Response, *, https: bool) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.set(name, value)
    if https:
        response.headers.set("Strict-Transport-Security", HSTS_VALUE)
    return response
