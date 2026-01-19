-- Limpa dataset de demo
delete from finops.cost_ledger;
delete from finops.interaction;
delete from finops.billing_accumulator;
delete from finops.model_pricing;

-- Tenants fake (use 1 para demo)
-- tenant_id = "spread"
-- (pode colocar outros depois)

-- Pricing fake (ex.: mínimo 50k tokens = R$ 1,20 para gpt-4o; etc.)
insert into finops.model_pricing (tenant_id, model, min_tokens, min_cost_brl, valid_from)
values
('spread','gpt-4o',     50000, 1.2000, now() - interval '30 days'),
('spread','gpt-4.1',    50000, 1.4000, now() - interval '30 days'),
('spread','gpt-4o-mini',50000, 0.6000, now() - interval '30 days');

-- Helper: gera dias com crescimento + um spike (explosão) em um dia
-- Você pode ajustar os números.
with days as (
  select (now()::date - s.i)::date as d,
         (1 + (s.i * 0.04))::numeric as growth_factor
  from generate_series(0, 29) as s(i)
),
base as (
  select d,
         growth_factor,
         case when d = (now()::date - interval '3 days')::date then 6.0 else 1.0 end as spike_factor
  from days
)
-- Ledger fake: manual + automation por dia, por modelo
insert into finops.cost_ledger
(tenant_id, occurred_at, source_type, model, tokens_billed, amount_brl, collaborator_id, task_id, flow_id, idempotency_key)
select
  'spread' as tenant_id,
  (b.d + time '10:00')::timestamptz as occurred_at,
  src.source_type,
  mdl.model,
  50000 as tokens_billed,
  round( (mp.min_cost_brl * b.growth_factor * b.spike_factor
          * case when src.source_type='AUTOMATION' then 2.2 else 0.8 end
          * case when mdl.model='gpt-4.1' then 1.1 when mdl.model='gpt-4o-mini' then 0.65 else 1.0 end
        )::numeric, 4) as amount_brl,
  case when src.source_type='MANUAL' then 'collab:bruno' else null end as collaborator_id,
  case when src.source_type='AUTOMATION' then 'task:n8n-payroll' else null end as task_id,
  case when src.source_type='AUTOMATION' then 'flow:auditoria' else null end as flow_id,
  ('seed|'||b.d||'|'||src.source_type||'|'||mdl.model)::text as idempotency_key
from base b
cross join (values ('MANUAL'), ('AUTOMATION')) as src(source_type)
cross join (values ('gpt-4o'), ('gpt-4.1'), ('gpt-4o-mini')) as mdl(model)
join finops.model_pricing mp on mp.tenant_id='spread' and mp.model='gpt-4o' -- usa base do pricing p/ escala
;

-- Interactions fake (só para auditoria / drill)
-- (gera 20 interações por dia; automation com task/flow)
with days as (
  select (now()::date - i)::date as d from generate_series(0,29) i
),
rows as (
  select d, n from days cross join generate_series(1,20) n
)
insert into finops.interaction
(tenant_id, occurred_at, source_type, model, tokens_total, collaborator_id, conversation_id, task_id, flow_id)
select
  'spread',
  (r.d + (interval '1 minute' * (r.n * 20)))::timestamptz,
  case when r.n % 3 = 0 then 'AUTOMATION' else 'MANUAL' end,
  case when r.n % 5 = 0 then 'gpt-4.1'
       when r.n % 2 = 0 then 'gpt-4o-mini'
       else 'gpt-4o' end,
  (800 + (r.n * 35))::int,
  case when r.n % 3 = 0 then null else 'collab:bruno' end,
  case when r.n % 3 = 0 then null else ('conv:'||r.d||':'||r.n) end,
  case when r.n % 3 = 0 then 'task:n8n-payroll' else null end,
  case when r.n % 3 = 0 then 'flow:auditoria' else null end
from rows r;

-- Accumulators fake (snapshot)
insert into finops.billing_accumulator
(tenant_id, bucket_type, model, bucket_key, pending_tokens, close_count, updated_at)
values
('spread','MANUAL','gpt-4o','collab:bruno', 12000, 18, now()),
('spread','AUTOMATION','gpt-4o','task:n8n-payroll', 34000, 52, now()),
('spread','AUTOMATION','gpt-4.1','task:n8n-payroll', 9000, 28, now());
