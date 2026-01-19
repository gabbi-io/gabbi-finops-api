4.1 Custo total (time series)

select
  $__timeGroup(time, '1d') as time,
  sum(value) as value
from finops.vw_cost_by_day
where tenant_id = '$tenant'
  and $__timeFilter(time)
group by 1
order by 1;

4.2 Manual vs Automação (stacked time series)

select
  $__timeGroup(time, '1d') as time,
  metric,
  sum(value) as value
from finops.vw_cost_by_source_day
where tenant_id = '$tenant'
  and $__timeFilter(time)
group by 1,2
order by 1,2;

4.3 Custo por modelo (bar/pie)

select metric, value
from finops.vw_cost_by_model
where tenant_id = '$tenant'
order by value desc;

4.4 Custo por modelo por dia (stacked)

select
  $__timeGroup(time, '1d') as time,
  metric,
  sum(value) as value
from finops.vw_cost_by_model_day
where tenant_id = '$tenant'
  and $__timeFilter(time)
group by 1,2
order by 1,2;

4.5 Top Tasks (automação)

select metric, value
from finops.vw_top_tasks
where tenant_id = '$tenant'
order by value desc
limit 10;

4.6 Top Flows (automação)

select metric, value
from finops.vw_top_flows
where tenant_id = '$tenant'
order by value desc
limit 10;

4.7 Tabela Ledger (drill-down)

select
  occurred_at,
  source_type,
  model,
  tokens_billed,
  amount_brl,
  collaborator_id,
  task_id,
  flow_id,
  idempotency_key
from finops.vw_ledger_table
where tenant_id = '$tenant'
  and $__timeFilter(occurred_at)
order by occurred_at desc
limit 500;