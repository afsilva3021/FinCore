// Aplica o tema salvo antes da pintura da página (carregado de forma síncrona no <head>).
try { document.documentElement.dataset.theme = localStorage.getItem('fincore-theme') || 'light'; } catch { /* armazenamento indisponível */ }
