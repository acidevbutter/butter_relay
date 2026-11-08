# Prod relay outage — 2026-10-20

## Sintoma
`https://relay.devbutter.tech` respondia 503 ("no available server").

## Causa raiz
O release do master de 2026-10-18 passou a exigir `RELAY_SECRETS_KEYS` fora de development
(validator em `app/settings/config.py`). Os apps de **prod** no Coolify não tinham essa variável:

- `butter-relay-api` (Coolify app id 26, uuid `nijt08cuds5oypw2thjir35j`, https://relay.devbutter.tech)
- `butter-relay-worker` (Coolify app id 27, uuid `rfrw2bzxbsgzfh4evlphxqgm`)

O container da API entrava em crash-loop com
`ValidationError: RELAY_SECRETS_KEYS must be set outside development`.
Staging (apps 3, 22) e preproduction (apps 16, 33) já tinham a variável.

## O que foi feito
1. Acesso: `ssh root@ssh.devbutter.tech` (Cloudflare Access; exige `cloudflared access login ssh.devbutter.tech` antes). Host: `srv1962123`.
2. Diagnóstico só de leitura: `docker ps -a`, `docker logs`, nomes das env vars (sem valores) e consulta ao `coolify-db`.
3. Gerado um novo `RELAY_SECRETS_KEYS` (Fernet, `openssl rand -base64 32` url-safe) e criado, via `php artisan tinker` no container `coolify` (model `Application::environment_variables()`, valor criptografado pelo Coolify), nos apps 26 e 27, em runtime, normal e preview (env var ids 1570, 1572, 1573, 1575). O valor não foi impresso nem salvo em arquivo.
4. Redeploy enfileirado dos dois apps (`queue_application_deployment`). Ambos terminaram `finished`.

## Resultado
- `nijt08…-20261020T103046` (api) e `rfrw2bz…-20261020T103046` (worker): Up.
- `GET https://relay.devbutter.tech/health` -> 200.

## Pendências / atenção
- **Guardar o valor de `RELAY_SECRETS_KEYS` de prod** (fica só no Coolify, app 26/27). Perder a chave deixa ilegíveis os segredos de entrega criptografados por projeto. Os dois apps precisam ter o MESMO valor (foram criados com a mesma chave).
- Prod nunca havia rodado com a chave (boot bloqueado), então não existia dado criptografado com outra chave. Se algum projeto de prod tiver segredo SMTP/Brevo por projeto, ele precisa ser recadastrado se tiver sido criado antes por outro caminho.
- Não validado: envio real de e-mail em prod (só `/health`). Rodar o smoke test contra prod é o próximo passo.
- Issue no GitHub descrevendo o bug (regra da org) **ainda não criada**; criar e fechar como resolvido.
- Fora de escopo, mas vistos quebrados no mesmo servidor (não mexi):
  - `v6hvtd45…` crash-loop: `MARITACA_API_KEY must be set outside development`.
  - `kdnemodlu…` crash-loop: alembic `Can't locate revision 'a2b3c4d5e6f7'`.
- Trabalho local não commitado em `fix/dvb-188a-idempotency-and-permanent-errors` (idempotência e falha terminal) continua pendente de commit/PR.
- Sugestão: documentar `RELAY_SECRETS_KEYS` como obrigatório no checklist de deploy do Coolify e/ou usar `docker-compose.hostinger.yml` para falhar cedo com mensagem clara.
