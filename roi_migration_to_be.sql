-- ROI TO-BE v3 - MIGRAÇÃO INCREMENTAL E COMPATÍVEL
-- Não remove tabelas AS-IS. Execute em homologação, faça backup e valide antes de produção.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS roi;

-- Ajustes compatíveis na tarefa atual
ALTER TABLE roi.roi_task ADD COLUMN IF NOT EXISTS owner_area_id text;
ALTER TABLE roi.roi_task ADD COLUMN IF NOT EXISTS code_generated boolean NOT NULL DEFAULT false;
ALTER TABLE roi.roi_task_baseline ADD COLUMN IF NOT EXISTS baseline_status varchar(30) NOT NULL DEFAULT 'DRAFT';
ALTER TABLE roi.roi_task_baseline ADD COLUMN IF NOT EXISTS rejected_by text;
ALTER TABLE roi.roi_task_baseline ADD COLUMN IF NOT EXISTS rejected_at timestamp(3);
ALTER TABLE roi.roi_task_baseline ADD COLUMN IF NOT EXISTS rejection_reason text;

-- Métodos reutilizáveis
CREATE TABLE IF NOT EXISTS roi.roi_method (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 code text NOT NULL UNIQUE,
 name text NOT NULL,
 description text,
 status varchar(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','inactive')),
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS roi.roi_method_version (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 roi_method_id text NOT NULL REFERENCES roi.roi_method(id),
 version_number integer NOT NULL,
 formula_key text NOT NULL,
 status varchar(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','inactive')),
 published_at timestamp(3),
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 UNIQUE(roi_method_id,version_number)
);
CREATE TABLE IF NOT EXISTS roi.roi_method_parameter (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 roi_method_version_id text NOT NULL REFERENCES roi.roi_method_version(id),
 parameter_key text NOT NULL,
 label text NOT NULL,
 data_type varchar(20) NOT NULL DEFAULT 'numeric',
 unit varchar(30),
 scope varchar(30) NOT NULL DEFAULT 'TASK_ROI' CHECK (scope IN ('TASK_ROI','TASK_PROCESS','AUTOMATION','PARTICIPANT')),
 editable_by varchar(20) NOT NULL DEFAULT 'BOTH' CHECK (editable_by IN ('OWNER','ADMIN','BOTH','SYSTEM')),
 required boolean NOT NULL DEFAULT false,
 default_value jsonb,
 min_value numeric,
 max_value numeric,
 display_order integer NOT NULL DEFAULT 0,
 validation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 UNIQUE(roi_method_version_id,parameter_key)
);

-- Processos/subtarefas
CREATE TABLE IF NOT EXISTS roi.roi_task_process (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_id text NOT NULL REFERENCES roi.roi_task(id),
 code text NOT NULL,
 name text NOT NULL,
 description text,
 order_index integer NOT NULL DEFAULT 0,
 status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
 created_by text, updated_by text,
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 UNIQUE(task_id,code)
);

-- Instância opcional do ROI da tarefa
CREATE TABLE IF NOT EXISTS roi.task_roi (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_id text NOT NULL REFERENCES roi.roi_task(id),
 roi_method_version_id text NOT NULL REFERENCES roi.roi_method_version(id),
 status varchar(30) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending_admin_setup','pending_baseline','pending_approval','active','inactive','archived')),
 active_from date NOT NULL DEFAULT current_date,
 active_to date,
 created_by text, approved_by text, approved_at timestamp(3),
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_roi_one_current
 ON roi.task_roi(task_id) WHERE active_to IS NULL AND status NOT IN ('inactive','archived');

-- Automação por processo + participantes
CREATE TABLE IF NOT EXISTS roi.task_process_automation (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_process_id text NOT NULL REFERENCES roi.roi_task_process(id),
 name text NOT NULL,
 execution_mode varchar(40), channel varchar(80),
 status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
 active_from date NOT NULL DEFAULT current_date, active_to date,
 created_by text, updated_by text,
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS roi.task_process_automation_participant (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 automation_id text NOT NULL REFERENCES roi.task_process_automation(id),
 agent_id text, workflow_id text, dag_id text,
 agent_name_snapshot text, workflow_name_snapshot text, dag_name_snapshot text,
 role varchar(50), order_index integer NOT NULL DEFAULT 0,
 status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
 created_by text, created_at timestamp(3) NOT NULL DEFAULT current_timestamp
);

-- Valores dinâmicos por escopo. scope_key elimina problema de NULL em unique composto.
CREATE TABLE IF NOT EXISTS roi.task_roi_parameter_value (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_roi_id text NOT NULL REFERENCES roi.task_roi(id),
 task_process_id text REFERENCES roi.roi_task_process(id),
 automation_id text REFERENCES roi.task_process_automation(id),
 participant_id text REFERENCES roi.task_process_automation_participant(id),
 roi_method_parameter_id text NOT NULL REFERENCES roi.roi_method_parameter(id),
 parameter_key text NOT NULL,
 scope_key text GENERATED ALWAYS AS (
   coalesce(task_process_id,'-') || '|' || coalesce(automation_id,'-') || '|' || coalesce(participant_id,'-')
 ) STORED,
 value_numeric numeric, value_text text, value_boolean boolean, value_json jsonb,
 filled_by text,
 created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 UNIQUE(task_roi_id,scope_key,roi_method_parameter_id)
);

CREATE TABLE IF NOT EXISTS roi.task_roi_process_baseline (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_roi_id text NOT NULL REFERENCES roi.task_roi(id),
 task_process_id text NOT NULL REFERENCES roi.roi_task_process(id),
 status varchar(30) NOT NULL DEFAULT 'draft',
 confidence_level varchar(20) NOT NULL DEFAULT 'MEDIUM',
 evidence_required boolean NOT NULL DEFAULT true,
 approved_by text, approved_at timestamp(3), rejected_by text, rejected_at timestamp(3), rejection_reason text,
 baseline_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_by text, created_at timestamp(3) NOT NULL DEFAULT current_timestamp,
 updated_at timestamp(3) NOT NULL DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS roi.task_roi_evidence (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_roi_id text NOT NULL REFERENCES roi.task_roi(id),
 task_process_id text REFERENCES roi.roi_task_process(id),
 baseline_id text REFERENCES roi.task_roi_process_baseline(id),
 automation_id text REFERENCES roi.task_process_automation(id),
 description text, file_url text, source_url text, source_type varchar(40), pii_masked boolean NOT NULL DEFAULT true,
 created_by text, created_at timestamp(3) NOT NULL DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS roi.task_roi_calculation_result (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 task_roi_id text NOT NULL REFERENCES roi.task_roi(id),
 period_start date NOT NULL, period_end date NOT NULL,
 gross_benefit_brl numeric(14,2) NOT NULL DEFAULT 0,
 automation_cost_brl numeric(14,2) NOT NULL DEFAULT 0,
 net_savings_brl numeric(14,2) NOT NULL DEFAULT 0,
 roi_pct numeric(14,2) NOT NULL DEFAULT 0,
 payback_months numeric(14,4),
 calculation_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 calculated_by text, calculated_at timestamp(3) NOT NULL DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS roi.task_roi_calculation_result_item (
 id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 calculation_result_id text NOT NULL REFERENCES roi.task_roi_calculation_result(id),
 task_process_id text REFERENCES roi.roi_task_process(id),
 automation_id text REFERENCES roi.task_process_automation(id),
 gross_benefit_brl numeric(14,2) NOT NULL DEFAULT 0,
 automation_cost_brl numeric(14,2) NOT NULL DEFAULT 0,
 net_savings_brl numeric(14,2) NOT NULL DEFAULT 0,
 calculation_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Catálogo inicial idempotente
INSERT INTO roi.roi_method(code,name,description,status) VALUES
 ('TIME_SAVED','H:H / Tempo economizado','Benefício baseado em tempo manual economizado.','published'),
 ('BUSINESS_RESULT','Resultado de negócio','Benefício baseado em eventos de negócio.','published'),
 ('HYBRID','Híbrido','Combina tempo economizado e resultado de negócio.','published')
ON CONFLICT(code) DO UPDATE SET name=excluded.name,description=excluded.description;
INSERT INTO roi.roi_method_version(roi_method_id,version_number,formula_key,status,published_at)
SELECT id,1,lower(code),'published',now() FROM roi.roi_method
ON CONFLICT(roi_method_id,version_number) DO NOTHING;

-- Parâmetros iniciais
WITH v AS (SELECT v.id,m.code FROM roi.roi_method_version v JOIN roi.roi_method m ON m.id=v.roi_method_id WHERE v.version_number=1),
p(code,key,label,type,unit,scope,edit,req,ord) AS (VALUES
 ('TIME_SAVED','avg_manual_time_min','Tempo manual médio','numeric','min','TASK_PROCESS','BOTH',true,10),
 ('TIME_SAVED','monthly_volume','Volume mensal','numeric','eventos','TASK_PROCESS','BOTH',true,20),
 ('TIME_SAVED','cost_per_hour_brl','Custo por hora','numeric','BRL','TASK_PROCESS','BOTH',true,30),
 ('TIME_SAVED','coverage_pct','Cobertura da automação','numeric','%','AUTOMATION','ADMIN',true,40),
 ('TIME_SAVED','human_review_pct','Revisão humana','numeric','%','AUTOMATION','ADMIN',false,50),
 ('TIME_SAVED','attribution_pct','Atribuição ao GABBI','numeric','%','TASK_ROI','BOTH',true,60),
 ('TIME_SAVED','agent_monthly_cost_brl','Custo mensal da solução','numeric','BRL','TASK_ROI','BOTH',true,70),
 ('TIME_SAVED','implementation_cost_brl','Custo de implantação','numeric','BRL','TASK_ROI','BOTH',false,80),
 ('BUSINESS_RESULT','value_event_name','Evento de valor','text',null,'TASK_PROCESS','BOTH',true,10),
 ('BUSINESS_RESULT','event_unit_value_brl','Valor unitário do evento','numeric','BRL','TASK_PROCESS','BOTH',true,20),
 ('BUSINESS_RESULT','expected_events_month','Eventos esperados/mês','numeric','eventos','TASK_PROCESS','BOTH',true,30),
 ('BUSINESS_RESULT','attribution_pct','Atribuição ao GABBI','numeric','%','TASK_ROI','BOTH',true,40),
 ('BUSINESS_RESULT','agent_monthly_cost_brl','Custo mensal da solução','numeric','BRL','TASK_ROI','BOTH',true,50),
 ('HYBRID','avg_manual_time_min','Tempo manual médio','numeric','min','TASK_PROCESS','BOTH',true,10),
 ('HYBRID','monthly_volume','Volume mensal','numeric','eventos','TASK_PROCESS','BOTH',true,20),
 ('HYBRID','cost_per_hour_brl','Custo por hora','numeric','BRL','TASK_PROCESS','BOTH',true,30),
 ('HYBRID','value_event_name','Evento de valor','text',null,'TASK_PROCESS','BOTH',true,40),
 ('HYBRID','event_unit_value_brl','Valor unitário do evento','numeric','BRL','TASK_PROCESS','BOTH',true,50),
 ('HYBRID','expected_events_month','Eventos esperados/mês','numeric','eventos','TASK_PROCESS','BOTH',true,60),
 ('HYBRID','coverage_pct','Cobertura da automação','numeric','%','AUTOMATION','ADMIN',true,70),
 ('HYBRID','human_review_pct','Revisão humana','numeric','%','AUTOMATION','ADMIN',false,80),
 ('HYBRID','attribution_pct','Atribuição ao GABBI','numeric','%','TASK_ROI','BOTH',true,90),
 ('HYBRID','agent_monthly_cost_brl','Custo mensal da solução','numeric','BRL','TASK_ROI','BOTH',true,100)
)
INSERT INTO roi.roi_method_parameter(roi_method_version_id,parameter_key,label,data_type,unit,scope,editable_by,required,display_order,min_value,max_value)
SELECT v.id,p.key,p.label,p.type,p.unit,p.scope,p.edit,p.req,p.ord,
       CASE WHEN p.unit IN ('%','BRL','min','eventos') THEN 0 END,
       CASE WHEN p.unit='%' THEN 100 END
FROM p JOIN v ON v.code=p.code
ON CONFLICT(roi_method_version_id,parameter_key) DO NOTHING;

-- Processo Principal para tarefas existentes
INSERT INTO roi.roi_task_process(task_id,code,name,description,order_index,status,created_by)
SELECT t.id,'MAIN','Principal',coalesce(t.process_name,'Processo principal'),0,'active',t.created_by
FROM roi.roi_task t
WHERE NOT EXISTS (SELECT 1 FROM roi.roi_task_process p WHERE p.task_id=t.id)
ON CONFLICT(task_id,code) DO NOTHING;

-- Migração best-effort dos vínculos publicados para task_roi (somente se a tabela legada existir)
DO $$
BEGIN
 IF to_regclass('roi.roi_task_framework') IS NOT NULL THEN
   EXECUTE $sql$
     INSERT INTO roi.task_roi(task_id,roi_method_version_id,status,active_from,created_by)
     SELECT DISTINCT ON (tf.task_id) tf.task_id, mv.id,
            CASE WHEN c.status='PUBLISHED' THEN 'active' ELSE 'draft' END,
            coalesce(tf.active_from,current_date),tf.created_by
     FROM roi.roi_task_framework tf
     JOIN roi.roi_configuration c ON c.id=tf.framework_id
     JOIN roi.roi_method m ON m.code=upper(CASE c.calculation_method WHEN 'time_saved' THEN 'TIME_SAVED' WHEN 'hybrid' THEN 'HYBRID' ELSE 'BUSINESS_RESULT' END)
     JOIN roi.roi_method_version mv ON mv.roi_method_id=m.id AND mv.version_number=1
     WHERE tf.active_to IS NULL
       AND NOT EXISTS (SELECT 1 FROM roi.task_roi tr WHERE tr.task_id=tf.task_id AND tr.active_to IS NULL)
     ORDER BY tf.task_id,tf.created_at DESC
   $sql$;
 END IF;
END $$;

-- Mappings antigos para automação no processo Principal
INSERT INTO roi.task_process_automation(task_process_id,name,execution_mode,channel,status,active_from,active_to,created_by,updated_by)
SELECT p.id,coalesce(m.agent_name,'Automação legada'),m.execution_mode,m.channel,
       lower(CASE WHEN m.status='ACTIVE' THEN 'active' ELSE 'inactive' END),m.active_from,m.active_to,m.created_by,m.updated_by
FROM roi.roi_task_mapping m JOIN roi.roi_task_process p ON p.task_id=m.task_id AND p.code='MAIN'
WHERE NOT EXISTS (SELECT 1 FROM roi.task_process_automation a WHERE a.task_process_id=p.id AND a.name=coalesce(m.agent_name,'Automação legada'));

-- Views auxiliares
CREATE OR REPLACE VIEW roi.vw_task_roi_current AS
SELECT tr.*,t.customer_id,t.project_id,t.code task_code,t.name task_name,m.code method_code,m.name method_name,v.version_number,v.formula_key
FROM roi.task_roi tr JOIN roi.roi_task t ON t.id=tr.task_id
JOIN roi.roi_method_version v ON v.id=tr.roi_method_version_id
JOIN roi.roi_method m ON m.id=v.roi_method_id
WHERE tr.active_to IS NULL AND tr.status NOT IN ('inactive','archived');

COMMIT;
