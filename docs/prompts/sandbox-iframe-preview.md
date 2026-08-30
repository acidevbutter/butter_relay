Corrija o achado H-04 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 4): os `<iframe srcdoc="...">` que renderizam
HTML de template/e-mail relayed não têm atributo `sandbox`.

## 1. Adicionar `sandbox` no preview de template

Em `app/admin/templates/partials/_preview.html:5-6`:

```html
<iframe style="width:100%; height:300px; margin-top:8px; border:1px solid var(--border); background:#fff;"
        sandbox=""
        srcdoc="{{ html | e }}"></iframe>
```

`sandbox=""` (vazio) aplica todas as restrições -- sem scripts, sem same-origin, sem
form-submit, sem top-navigation. Não adicione `allow-scripts` nem `allow-same-origin` juntos
nesse atributo (a combinação dos dois anula o sandbox).

## 2. Adicionar `sandbox` no detalhe de mensagem

Em `app/admin/templates/message_detail.html:31-33`, mesma mudança:

```html
<iframe style="width:100%; height:500px; margin-top:8px; border:1px solid var(--border); background:#fff;"
        sandbox=""
        srcdoc="{{ html | e }}"></iframe>
```

## 3. Checar se falta CSP

Confira se `app/settings/middleware.py` ou `app/main.py` já define algum header CSP. Se não
existir nenhum, adicione um `Content-Security-Policy` básico nas respostas HTML do `/admin/*`
(ex. via `RequestContextMiddleware` ou middleware novo) restringindo `frame-src`/`script-src`
a `'self'`, para reforçar o sandbox do iframe com defesa em profundidade. Não é obrigatório
neste prompt se já houver CSP definida em outro lugar do monorepo (ex. proxy reverso) -- nesse
caso, só anote em `docs/butter-relay-fluxos.md` onde a CSP é aplicada.

## 4. Testes

Em `tests/` (crie `tests/test_admin_templates.py` se não houver teste HTTP do admin ainda, ou
adicione a um arquivo existente que já suba a app via `TestClient`/`httpx.AsyncClient`):

- Requisição autenticada a `GET /admin/messages/{id}` (ou ao endpoint de preview) -- confirme
  que a resposta contém `sandbox=""` (ou `sandbox` sem `allow-scripts allow-same-origin` juntos)
  no HTML do iframe.

Rode `poetry run pytest -q` ao final.
