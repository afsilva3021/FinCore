"""Regras de negócio puras do FinCore.

Nada aqui importa Robyn, Tortoise, banco de dados ou HTTP: são funções e valores que
recebem dados e devolvem resultados ou levantam `ValueError` com a mensagem para o
usuário. A camada `application` busca os dados, chama estas regras e grava o resultado.
Isso deixa as regras financeiras testáveis em milissegundos e independentes do framework.
"""
