# Endpoints FinOps V2 - Backend Python

## 1. Dataset completo com novos filtros
`GET /api/finops/dataset?days=30&business_area=<area>&project_key=<projeto>&agent_name=<agente>`

Retorna o dataset atual completo, agora aceitando `business_area`/`area` além dos filtros já existentes.

## 2. Opções de filtro para o frontend
`GET /api/finops/filters?days=30`

Retorna:
- `periods`
- `business_areas`
- `project_keys`
- `agents`

Observação: se a tabela ainda não tiver coluna `business_area`, o backend usa `project_key` como fallback compatível.

## 3. Custo por agente
`GET /api/finops/agents/cost?days=30&business_area=<area>&limit=20`

Retorna lista de agentes com:
- `rank`
- `agent_name`
- `total_cost_brl`
- `cost_percent`

## 4. Dobra principal FinOps
`GET /api/finops/hero-fold?days=30&business_area=<area>`

Retorna os cards da seção "O que mostrar na dobra principal do FinOps":
- Concentração no Top 3
- ROI precisa de contexto
- Lacunas críticas do painel

## 5. Campos adicionados no dataset principal
No retorno de `/api/finops/dataset` foram adicionados/garantidos:
- `kpis.automation_cost`
- `showback.by_agent`
- `filters.business_area`
- suporte a cálculo auxiliar via endpoints específicos acima
