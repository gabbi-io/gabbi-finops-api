-- Gabbi ROI MVP schema
-- Execute no banco gabbi-io como usuário com permissão de DDL.

create extension if not exists pgcrypto;
create schema if not exists roi;

create table if not exists roi.roi_configuration (
    id text primary key default gen_random_uuid()::text,
    customer_id text not null references public."Customer"(id),
    project_id text not null,
    task_id text,
    agent_id text,
    workflow_id text,
    dag_id text,
    name text not null,
    description text,
    calculation_method varchar(40) not null default 'business_result',
    value_event_name text,
    event_unit_value_brl numeric(14,2) not null default 0,
    expected_events_month numeric(14,2) not null default 0,
    attribution_pct numeric(7,2) not null default 100,
    baseline_monthly_brl numeric(14,2) not null default 0,
    agent_monthly_cost_brl numeric(14,2) not null default 0,
    human_review_pct numeric(7,2) not null default 0,
    require_evidence boolean not null default false,
    human_review_required boolean not null default false,
    responsible_area text,
    status varchar(20) not null default 'DRAFT',
    assumptions_json jsonb not null default '{}'::jsonb,
    last_simulation_json jsonb,
    published_at timestamp(3),
    published_by text,
    created_by text,
    updated_by text,
    created_at timestamp(3) not null default current_timestamp,
    updated_at timestamp(3)
);

create index if not exists ix_roi_configuration_customer on roi.roi_configuration(customer_id);
create index if not exists ix_roi_configuration_project on roi.roi_configuration(project_id);
create index if not exists ix_roi_configuration_status on roi.roi_configuration(status);

create table if not exists roi.roi_configuration_version (
    id text primary key default gen_random_uuid()::text,
    configuration_id text not null references roi.roi_configuration(id),
    version int not null,
    status varchar(20) not null default 'PUBLISHED',
    snapshot_json jsonb not null,
    published_at timestamp(3) not null default current_timestamp,
    published_by text,
    checksum text generated always as (md5(snapshot_json::text)) stored,
    unique(configuration_id, version)
);

create table if not exists roi.roi_task (
    id text primary key default gen_random_uuid()::text,
    customer_id text not null references public."Customer"(id),
    project_id text not null,
    code varchar(80),
    name text not null,
    description text,
    area_id text,
    process_name text,
    owner_id text,
    status varchar(20) not null default 'DRAFT',
    created_by text,
    updated_by text,
    created_at timestamp(3) not null default current_timestamp,
    updated_at timestamp(3),
    unique(project_id, code)
);

create index if not exists ix_roi_task_customer on roi.roi_task(customer_id);
create index if not exists ix_roi_task_project on roi.roi_task(project_id);
create index if not exists ix_roi_task_status on roi.roi_task(status);

create table if not exists roi.roi_task_baseline (
    id text primary key default gen_random_uuid()::text,
    task_id text not null references roi.roi_task(id),
    avg_manual_time_min numeric(14,2) not null default 0,
    monthly_volume numeric(14,2) not null default 0,
    cost_per_hour_brl numeric(14,2) not null default 0,
    manual_sla_hours numeric(14,2),
    manual_error_rate numeric(7,4),
    baseline_date date not null default current_date,
    confidence_level varchar(20) not null default 'MEDIUM',
    evidence_required boolean not null default true,
    approved boolean not null default false,
    approved_by text,
    approved_at timestamp(3),
    created_by text,
    created_at timestamp(3) not null default current_timestamp
);

create index if not exists ix_roi_task_baseline_task on roi.roi_task_baseline(task_id);

create table if not exists roi.roi_task_mapping (
    id text primary key default gen_random_uuid()::text,
    task_id text not null references roi.roi_task(id),
    agent_id text,
    agent_name text,
    workflow_id text,
    dag_id text,
    coverage_pct numeric(7,2) not null default 100,
    human_review_pct numeric(7,2) not null default 0,
    execution_mode varchar(40),
    channel varchar(80),
    status varchar(20) not null default 'ACTIVE',
    active_from date not null default current_date,
    active_to date,
    created_by text,
    updated_by text,
    created_at timestamp(3) not null default current_timestamp,
    updated_at timestamp(3)
);

create index if not exists ix_roi_task_mapping_task on roi.roi_task_mapping(task_id);
create index if not exists ix_roi_task_mapping_agent on roi.roi_task_mapping(agent_id);
create index if not exists ix_roi_task_mapping_workflow on roi.roi_task_mapping(workflow_id);

create table if not exists roi.roi_calculation_result (
    id text primary key default gen_random_uuid()::text,
    customer_id text not null references public."Customer"(id),
    project_id text not null,
    task_id text references roi.roi_task(id),
    configuration_id text references roi.roi_configuration(id),
    entity_type varchar(40) not null default 'TASK',
    entity_id text,
    period_start date not null,
    period_end date not null,
    gross_savings_brl numeric(14,2) not null default 0,
    ai_cost_brl numeric(14,2) not null default 0,
    net_savings_brl numeric(14,2) not null default 0,
    roi_pct numeric(14,2) not null default 0,
    payback_months numeric(14,4),
    confidence_level varchar(20) not null default 'MEDIUM',
    calculation_snapshot_json jsonb not null default '{}'::jsonb,
    calculated_at timestamp(3) not null default current_timestamp,
    calculated_by text
);

create index if not exists ix_roi_calc_customer_period on roi.roi_calculation_result(customer_id, period_start, period_end);
create index if not exists ix_roi_calc_project_period on roi.roi_calculation_result(project_id, period_start, period_end);
create index if not exists ix_roi_calc_task on roi.roi_calculation_result(task_id);

create table if not exists roi.roi_evidence (
    id text primary key default gen_random_uuid()::text,
    customer_id text references public."Customer"(id),
    entity_type varchar(40) not null,
    entity_id text not null,
    file_url text,
    source_url text,
    source_type varchar(40),
    description text,
    pii_masked boolean not null default true,
    uploaded_by text,
    created_at timestamp(3) not null default current_timestamp
);

create index if not exists ix_roi_evidence_entity on roi.roi_evidence(entity_type, entity_id);

create table if not exists roi.roi_audit_event (
    id text primary key default gen_random_uuid()::text,
    event_type varchar(40) not null,
    entity_type varchar(40) not null,
    entity_id text not null,
    user_id text,
    customer_id text,
    before_json jsonb,
    after_json jsonb,
    ip_address inet,
    created_at timestamp(3) not null default current_timestamp
);

create index if not exists ix_roi_audit_customer on roi.roi_audit_event(customer_id, created_at desc);
create index if not exists ix_roi_audit_entity on roi.roi_audit_event(entity_type, entity_id, created_at desc);
