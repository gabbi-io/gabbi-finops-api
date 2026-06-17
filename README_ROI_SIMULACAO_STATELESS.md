# Atualização ROI - Simulação Stateless

Esta versão deixa explícito o fluxo correto para o front:

1. `GET /api/roi/calculation-methods` para carregar métodos e campos dinâmicos.
2. `POST /api/roi/configurations/simulate` para simular sem gravar no banco.
3. `POST /api/roi/configurations` para salvar a configuração somente quando o usuário clicar em salvar/publicar.
4. `PATCH /api/roi/configurations/{id}` para editar uma configuração já salva.
5. `POST /api/roi/configurations/{id}/publish` para publicar/travar a configuração.

O endpoint de simulação retorna `persisted: false` para deixar claro que não gravou nada no banco.

## Arquivos alterados

- `app.py`
- `roi_provider.py`

Não há necessidade de alteração de banco para essa atualização.
