-- 1) KPI: custo total / manual / automação (por período)
create or replace view finops.vw_kpi_cost as
select
  tenant_id,
  date_trunc('day', occurred_at) as day,
  sum(amount_brl) as total_cost_brl,
  sum(case when source_type='MANUAL' then amount_brl else 0 end) as manual_cost_brl,
  sum(case when source_type='AUTOMATION' then amount_brl else 0 end) as automation_cost_brl,
  count(*) as ledger_rows
from finops.cost_ledger
group by tenant_id, date_trunc('day', occurred_at);

-- 2) Série temporal: custo por dia
create or replace view finops.vw_cost_by_day as
select
  tenant_id,
  date_trunc('day', occurred_at) as time,
  sum(amount_brl) as value
from finops.cost_ledger
group by tenant_id, date_trunc('day', occurred_at);

-- 3) Custo por origem (manual x automação) por dia
create or replace view finops.vw_cost_by_source_day as
select
  tenant_id,
  date_trunc('day', occurred_at) as time,
  source_type as metric,
  sum(amount_brl) as value
from finops.cost_ledger
group by tenant_id, date_trunc('day', occurred_at), source_type;

-- 4) Custo por modelo (bar/pie)
create or replace view finops.vw_cost_by_model as
select
  tenant_id,
  model as metric,
  sum(amount_brl) as value
from finops.cost_ledger
group by tenant_id, model;

-- 5) Custo por modelo por dia (stacked series)
create or replace view finops.vw_cost_by_model_day as
select
  tenant_id,
  date_trunc('day', occurred_at) as time,
  model as metric,
  sum(amount_brl) as value
from finops.cost_ledger
group by tenant_id, date_trunc('day', occurred_at), model;

-- 6) Top tasks (automação) - ranking
create or replace view finops.vw_top_tasks as
select
  tenant_id,
  coalesce(task_id,'(no-task)') as metric,
  sum(amount_brl) as value
from finops.cost_ledger
where source_type='AUTOMATION'
group by tenant_id, coalesce(task_id,'(no-task)')
order by value desc;

-- 7) Top flows (automação) - ranking
create or replace view finops.vw_top_flows as
select
  tenant_id,
  coalesce(flow_id,'(no-flow)') as metric,
  sum(amount_brl) as value
from finops.cost_ledger
where source_type='AUTOMATION'
group by tenant_id, coalesce(flow_id,'(no-flow)')
order by value desc;

-- 8) Ledger “tabela” (drill-down)
create or replace view finops.vw_ledger_table as
select
  tenant_id,
  occurred_at,
  source_type,
  model,
  tokens_billed,
  amount_brl,
  collaborator_id,
  task_id,
  flow_id,
  idempotency_key
from finops.cost_ledger;

-- 9) Interactions “tabela” (auditoria)
create or replace view finops.vw_interactions_table as
select
  tenant_id,
  occurred_at,
  source_type,
  model,
  tokens_total,
  collaborator_id,
  conversation_id,
  task_id,
  flow_id
from finops.interaction;

-- 10) Accumulators “tabela”
create or replace view finops.vw_accumulators_table as
select
  tenant_id,
  bucket_type,
  model,
  bucket_key,
  pending_tokens,
  close_count,
  updated_at
from finops.billing_accumulator;
