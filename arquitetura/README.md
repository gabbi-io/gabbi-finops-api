# gabbi-finops-poc (Flask) — FinOps Core | Fase 1 (Custo)

## Visão Geral

Esta PoC implementa o **FinOps Core do GABBI – Fase 1**, cujo objetivo é **transformar consumo de IA em custo financeiro auditável**, pronto para dashboards e governança.

> ⚠️ **PoC com dados simulados**  
> Toda a lógica, arquitetura e regras são equivalentes ao produto final. Apenas as fontes de dados são mockadas.

---

## Objetivos da Fase 1

- Mensurar custo de IA com **rastreabilidade total**
- Respeitar **mínimo de cobrança por modelo**
- Separar **uso manual vs automação**
- Gerar **fonte única de verdade financeira (CostLedger)**
- Preparar integração nativa com **Grafana**

### Conceito Central

```
Interaction → BillingAccumulator → CostLedger → Grafana
```

---

## Arquitetura (Resumo)

### Componentes

- **Front GABBI (Web UI)**  
  Uso manual por colaboradores

- **Orquestradores (n8n / Airflow)**  
  Uso automatizado (flows, agents, tasks)

- **FinOps Service (Flask – este projeto)**  
  - Registra Interactions
  - Acumula tokens respeitando mínimo
  - Gera lançamentos financeiros

- **Camada de Dados**
  - Interaction (auditável)
  - BillingAccumulator (estado)
  - CostLedger (financeiro)
  - ModelPricing (versionado)

- **Observabilidade**
  - CostLedger → métricas
  - Grafana (embed preparado)

---

## Escopo

### Dentro do escopo (Fase 1)
- Custo auditável
- Mínimo de billing
- Manual vs Automação
- Rastreamento por modelo/task/flow
- Storytelling de crescimento e explosão
- Preparação para Grafana

### Fora do escopo
- Receita / Benefício
- ROI
- Rateio por área
- Monetização

---

## Contrato de Eventos – InteractionEvent

### Estrutura lógica

```json
{
  "interaction_id": "uuid",
  "timestamp": "ISO-8601",
  "tenant_id": "string",
  "source_type": "MANUAL | AUTOMATION",
  "model": "gpt-4o | gpt-4.1 | ...",
  "tokens_input": 123,
  "tokens_output": 456,
  "tokens_total": 579,
  "collaborator_id": "string | null",
  "task_id": "string | null",
  "flow_id": "string | null",
  "conversation_id": "string",
  "metadata": {}
}
```

### Princípios
- **Sempre gerado**, mesmo sem custo imediato
- Fonte de **auditoria e rastreabilidade**
- Nunca usado diretamente para dashboard financeiro

---

## Modelo Financeiro (CostLedger)

Cada fechamento de mínimo gera um lançamento:

```json
{
  "ledger_id": "uuid",
  "timestamp": "ISO-8601",
  "tenant_id": "string",
  "source_type": "MANUAL | AUTOMATION",
  "model": "string",
  "amount": 12.34,
  "tokens_billed": 1000,
  "task_id": "string | null",
  "flow_id": "string | null",
  "idempotency_key": "unique"
}
```

> 📌 **Grafana deve consultar exclusivamente o CostLedger**

---

## Guia para Desenvolvedores

### Regras de ouro

1. **Interaction ≠ custo**
2. **Ledger = financeiro**
3. **Accumulator introduz estado**
4. **Idempotência é obrigatória**
5. **Dashboard nunca recalcula custo**

---

## Executando Localmente

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate # Windows

pip install -r requirements.txt
python app.py
```

---

## Executando com Docker

### Pré-requisitos
- Docker >= 20
- Docker Compose v2

### Configuração

Crie `.env`:

```env
AZURE_OPENAI_ENDPOINT=https://SEU-RESOURCE.openai.azure.com/
AZURE_OPENAI_API_KEY=xxxx
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_DEPLOYMENT=deployment-name
```

### Subir

```bash
docker compose up -d --build
```

Healthcheck:

```bash
curl http://localhost:8000/health
```

---

## Principais Rotas

| Rota | Descrição |
|---|---|
| `/finops` | Dashboard |
| `/ledger` | CostLedger |
| `/interactions` | Auditabilidade |
| `/accumulators` | Billing buckets |
| `/pricing` | Regras de mínimo |
| `/settings` | Grafana embed |

---

## RUNBOOK Operacional (Resumo)

### Problema: custo divergente
- Verificar Pricing vigente
- Conferir Accumulators (pending_tokens)
- Validar fechamento no Ledger

### Problema: custo não aparece no dashboard
- Confirmar leitura do CostLedger
- Validar período / filtros
- Conferir idempotência

### Deploy
- Stateless API
- Dados persistidos externamente
- Rollback seguro (Ledger imutável)

---

## Evolução para Produção (Próximo Passo)

### Prod-ready inclui:
- PostgreSQL
- SQLAlchemy + Alembic
- Migrations versionadas
- Locks transacionais
- Observabilidade (Prometheus)
- Grafana produtivo
- RBAC e LGPD

---

## Roadmap

- **Fase 2**: Receita / Benefício
- **Fase 3**: Rateio por área
- Alertas de custo
- SLO financeiro
- Integração billing real

---

## Mensagem Final

> **A Fase 1 transforma custo em dado confiável.**  
> A partir disso, ROI e governança deixam de ser discussão e viram cálculo.

---

© Gabbi / Spread — FinOps Core
