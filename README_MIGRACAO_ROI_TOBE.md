# Gabbi FinOps — ROI TO-BE v3 compatível

Esta entrega implementa o redesenho AS IS → TO BE sem remover as tabelas ou rotas atuais.

## Arquivos

- `roi_migration_to_be.sql`: migração incremental e catálogo inicial de métodos/parâmetros.
- `roi_provider.py`: provider completo atual acrescido da camada TO-BE.
- `app.py`: aplicação completa com rotas antigas preservadas e novas rotas.
- `db.py`: conexão via variáveis de ambiente, sem senha gravada no código.
- `real_provider.py`, `simulator.py` e `wsgi.py`: arquivos completos preservados.
- `.env.example`: exemplo de configuração.

## Ordem de implantação

1. Faça backup do banco `gabbi-io`.
2. Crie uma cópia dos arquivos atuais da aplicação.
3. Configure as variáveis de ambiente, principalmente `DB_PASSWORD`.
4. Execute `roi_migration_to_be.sql` primeiro em homologação.
5. Substitua `app.py`, `roi_provider.py` e `db.py` pelos arquivos desta pasta.
6. Os demais arquivos podem ser substituídos integralmente para manter o pacote uniforme.
7. Reinicie o Gunicorn/Flask.
8. Teste `/health`, `/openapi.json` e as rotas legadas.
9. Teste as novas rotas TO-BE.

## Compatibilidade preservada

Continuam disponíveis:

- `GET/POST /api/roi/configurations`
- `GET/POST /api/roi/tasks`
- `POST /api/roi/tasks/{id}/baseline`
- `POST /api/roi/tasks/{id}/approve-baseline`
- `GET /api/roi/tasks/{id}/results`
- `GET/POST /api/roi/evidences`
- `GET/POST /api/roi/mappings`
- `GET /api/roi/dashboard/executive`

O cálculo TO-BE grava também uma cópia em `roi.roi_calculation_result`. Por isso o dashboard executivo atual continua lendo os resultados sem mudança obrigatória no front.

## Novos endpoints

### Métodos e campos dinâmicos

```http
GET /api/roi/methods
GET /api/roi/method-versions/{version_id}/parameters
```

### Processos/subtarefas

```http
GET  /api/roi/tasks/{task_id}/processes
POST /api/roi/tasks/{task_id}/processes
```

Payload:

```json
{
  "name": "Consultar CRM",
  "description": "Consulta os dados do candidato",
  "order_index": 1
}
```

### ROI opcional da tarefa

```http
GET  /api/roi/tasks/{task_id}/roi
POST /api/roi/tasks/{task_id}/roi
```

Payload:

```json
{
  "roi_method_version_id": "ID_DA_VERSAO",
  "status": "draft"
}
```

### Valores por escopo

```http
POST /api/roi/task-roi-values
```

Payload:

```json
{
  "task_roi_id": "ID_DO_TASK_ROI",
  "values": [
    {
      "roi_method_parameter_id": "ID_PARAMETRO",
      "task_process_id": "ID_PROCESSO",
      "value": 25
    }
  ]
}
```

Para parâmetros `ADMIN`, envie `X-User-Role: ADMIN`. O backend bloqueia usuário comum.

### Automação e participantes (Admin)

```http
POST /api/roi/admin/task-process-automations
POST /api/roi/admin/task-process-automations/{automation_id}/participants
GET  /api/roi/tasks/{task_id}/automations
```

Headers:

```http
X-User-Id: ID_DO_USUARIO
X-User-Role: ADMIN
```

### Cálculo oficial

```http
POST /api/roi/task-roi/{task_roi_id}/calculate
```

Payload:

```json
{
  "period_start": "2026-07-01",
  "period_end": "2026-07-31"
}
```

## Fluxo recomendado para o frontend

1. Criar a tarefa usando a rota atual.
2. Buscar os métodos publicados em `/api/roi/methods`.
3. Buscar os parâmetros da versão escolhida.
4. Criar o ROI opcional da tarefa.
5. Criar processos ou usar o processo `Principal` criado automaticamente.
6. Salvar parâmetros por escopo.
7. Admin configura automações e participantes.
8. Registrar/aprovar baseline usando as rotas legadas durante a transição.
9. Executar o cálculo TO-BE.
10. Manter o dashboard atual consumindo `/api/roi/dashboard/executive`.

## Observações sobre owner, área e código

A estrutura TO-BE e as colunas necessárias foram criadas. A geração automática definitiva de código, a derivação de área e a consulta de perfil dependem do modelo real de usuários/áreas da plataforma. Como essa estrutura não foi fornecida, a entrega evita presumir nomes de tabelas ou relacionamentos e mantém o comportamento legado. O ponto de extensão está pronto para receber a regra oficial sem alterar contratos do frontend.

## Validação SQL

```sql
SELECT code, name, status FROM roi.roi_method ORDER BY code;
SELECT * FROM roi.roi_method_version ORDER BY roi_method_id, version_number;
SELECT scope, editable_by, count(*) FROM roi.roi_method_parameter GROUP BY scope, editable_by;
SELECT count(*) FROM roi.roi_task_process;
SELECT * FROM roi.vw_task_roi_current;
```

## Rollback

Como a migração não remove tabelas antigas, o rollback da aplicação consiste em restaurar os arquivos anteriores. As novas tabelas podem permanecer sem afetar as rotas antigas. Não remova tabelas TO-BE antes de exportar eventuais dados criados nelas.
