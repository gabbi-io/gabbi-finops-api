-- Schema dedicado para FinOps
create schema if not exists finops;

-- ================
-- 1) Interaction (log bruto)
-- ================
create table if not exists finops.interaction (
  id bigserial primary key,
  tenant_id text not null,
  occurred_at timestamptz not null,
  source_type text not null check (source_type in ('MANUAL','AUTOMATION')),
  model text not null,
  tokens_total int not null check (tokens_total >= 0),

  collaborator_id text null,
  conversation_id text null,

  task_id text null,
  flow_id text null,

  created_at timestamptz not null default now()
);

create index if not exists ix_interaction_tenant_time on finops.interaction (tenant_id, occurred_at desc);
create index if not exists ix_interaction_model on finops.interaction (tenant_id, model);
create index if not exists ix_interaction_source on finops.interaction (tenant_id, source_type);

-- ================
-- 2) BillingAccumulator (sobras por balde)
-- ================
create table if not exists finops.billing_accumulator (
  id bigserial primary key,
  tenant_id text not null,
  bucket_type text not null check (bucket_type in ('MANUAL','AUTOMATION')),
  model text not null,

  bucket_key text not null, -- exemplo: "collab:123" ou "task:abc"
  pending_tokens int not null default 0 check (pending_tokens >= 0),
  close_count int not null default 0 check (close_count >= 0),

  updated_at timestamptz not null default now(),
  unique (tenant_id, bucket_type, model, bucket_key)
);

create index if not exists ix_acc_tenant on finops.billing_accumulator (tenant_id);

-- ================
-- 3) ModelPricing (min_tokens / custo)
-- ================
create table if not exists finops.model_pricing (
  id bigserial primary key,
  tenant_id text not null,
  model text not null,
  min_tokens int not null check (min_tokens > 0),
  min_cost_brl numeric(12,4) not null check (min_cost_brl >= 0),
  valid_from timestamptz not null default now(),
  valid_to timestamptz null,
  unique (tenant_id, model, valid_from)
);

create index if not exists ix_pricing_tenant_model on finops.model_pricing (tenant_id, model, valid_from desc);

-- ================
-- 4) CostLedger (financeiro / fonte de verdade)
-- ================
create table if not exists finops.cost_ledger (
  id bigserial primary key,
  tenant_id text not null,
  occurred_at timestamptz not null,

  source_type text not null check (source_type in ('MANUAL','AUTOMATION')),
  model text not null,

  tokens_billed int not null check (tokens_billed > 0),
  amount_brl numeric(12,4) not null check (amount_brl >= 0),

  collaborator_id text null,
  task_id text null,
  flow_id text null,

  idempotency_key text not null unique,
  created_at timestamptz not null default now()
);

create index if not exists ix_ledger_tenant_time on finops.cost_ledger (tenant_id, occurred_at desc);
create index if not exists ix_ledger_source on finops.cost_ledger (tenant_id, source_type);
create index if not exists ix_ledger_model on finops.cost_ledger (tenant_id, model);
create index if not exists ix_ledger_task on finops.cost_ledger (tenant_id, task_id);
create index if not exists ix_ledger_flow on finops.cost_ledger (tenant_id, flow_id);
