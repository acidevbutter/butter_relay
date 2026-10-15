# Prompts — butter_relay para clientes externos (EDIY)

Prompts para colar numa sessão do Claude Code aberta na raiz de `butter_relay`, uma branch nova por prompt.
Ordem obrigatória: **1 → 2**.

| # | Arquivo | Escopo |
|---|---|---|
| 1 | `PROMPT-butter-relay-clientes-externos.md` | S1–S6: provider com TLS/segredos cifrados, tipos de e-mail e upload de template, escopos de chave, `POST /api/v1/messages/send` por HTTPS, cadastro por manifesto JSON |
| 2 | `PROMPT-butter-relay-mailings.md` | M1–M6: categoria transacional × marketing, contatos/listas/consentimento/supressão, descadastro, campanhas, eventos (bounce/complaint) e contrato do plugin WordPress |

Apoio:

- `ediy.manifest.json` — manifesto de exemplo da EDIY (copiar para `docs/examples/` ao implementar S5).
- `analise-butter-relay.md` — análise do relay (gaps para a EDIY), referenciada pelo prompt 1.

Os prompts citam `docs/analise-butter-relay.md` e `docs/examples/ediy.manifest.json`; ao implementar, copie os arquivos desta pasta para esses caminhos.

Regras que valem para os dois: padrão `fastapi-builder-api` (router → service → repository), Alembic timestamped com head único, todo bug vira issue no GitHub e é fechada ao fim do slice, e nada de segredo em log ou resposta.

Origem: repo `ediy_v2` (`docs/relay/`), 15/10/2026. As issues de acompanhamento estão no Linear, projeto "DevButter Relay API", epic "EDIY ↔ butter_relay via HTTP".
