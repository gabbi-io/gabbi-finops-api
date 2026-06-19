from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

import psycopg2
from flask import Flask, jsonify, redirect, render_template, request, url_for, Response, make_response
from flask_cors import CORS
from flask.json.provider import DefaultJSONProvider
import bcrypt
from flask import session, redirect, url_for, render_template, request, flash
from functools import wraps
import simulator as sim
from real_provider import summarize_real, upsert_pricing, ingest_usage, get_finops_filter_options, get_cost_by_agent, get_hero_fold
from db import fetch_one



class GabbiJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

app = Flask(__name__)
app.json = GabbiJSONProvider(app)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "gabbi-super-secret-key-2026"

# -------------------------------------------------------------------
# CORS
# -------------------------------------------------------------------
# Permite que o frontend React/Node em ambiente local consuma a API Python.
# Em produção, informe FINOPS_CORS_ORIGINS com a lista de origens permitidas, separadas por vírgula.
# Exemplo:
# FINOPS_CORS_ORIGINS=http://localhost:5173,http://192.168.230.107:5173,https://finops.gabbi.io
_allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "FINOPS_CORS_ORIGINS",
        ",".join([
            # Local
            "http://localhost:5173",
            "http://127.0.0.1:5173",

            # FinOps API server
            "http://192.168.230.107:5173",
            "http://192.168.230.107:8098",

            # Dev server
            "http://192.168.230.99",
            "http://192.168.230.99:5173",
            "http://192.168.230.99:8098",
            "https://192.168.230.99",
            "https://192.168.230.99:5173",
            "https://192.168.230.99:8098",

            # Prod server / banco atual
            "http://192.168.230.108",
            "http://192.168.230.108:5173",
            "http://192.168.230.108:8098",
            "https://192.168.230.108",
            "https://192.168.230.108:5173",
            "https://192.168.230.108:8098",

            # Domínios Gabbi
            "https://dev.gabbi.io",
            "https://gabbi.io",
            "https://www.gabbi.io"
        ])
    ).split(",")
    if origin.strip()
]

CORS(
    app,
    resources={r"/api/*": {"origins": _allowed_origins}},
    supports_credentials=True,
    allow_headers=[
        "Content-Type",
        "Authorization",
        "clientKey",
        "ClientKey",
        "X-Client-Key",
        "X-Tenant-Id",
        "X-Company-Id",
        "X-Empresa-Id",
    ],
    expose_headers=[
        "clientKey",
        "X-Client-Key",
        "X-Tenant-Id",
        "X-Company-Id",
    ],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    max_age=86400,
)


def _apply_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, clientKey, ClientKey, X-Client-Key, "
            "X-Tenant-Id, X-Company-Id, X-Empresa-Id, X-User-Id, userId"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "86400"
    return response


@app.before_request
def _handle_api_options_preflight():
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
        response = make_response("", 204)
        return _apply_cors_headers(response)
    return None


@app.after_request
def _after_request_cors(response):
    return _apply_cors_headers(response)


@app.errorhandler(Exception)
def _json_error_handler(error):
    code = getattr(error, "code", 500) or 500
    if code == 404:
        return jsonify({"ok": False, "error": "not_found", "message": str(error)}), 404
    print(f"[APP][ERROR] {type(error).__name__}: {error}")
    return jsonify({"ok": False, "error": "internal_error" if code >= 500 else "request_error", "message": str(error)}), code

def _tenant_id_from_request() -> str | None:
    return (
        request.headers.get("clientKey")
        or request.headers.get("ClientKey")
        or request.headers.get("X-Client-Key")
        or request.headers.get("X-Tenant-Id")
        or request.headers.get("X-Company-Id")
        or request.headers.get("X-Empresa-Id")
        or request.args.get("clientKey")
        or request.args.get("client_key")
        or request.args.get("tenant_id")
        or request.args.get("company_id")
        or request.args.get("empresa_id")
        or None
    )

def _effective_project_key() -> str | None:
    """Resolve o cliente do frontend para o project_key usado pelo FinOps.

    Padrão do front Gabbi:
      headers: { clientKey: customerId }

    Fluxo correto:
      1. Recebe clientKey contendo public."Customer".id
      2. Consulta public."Customer"
      3. Recupera public."Customer".key
      4. Usa Customer.key como finops.interaction_usage.project_key

    Compatibilidades mantidas:
      - Se project_key vier explicitamente na query, ele tem prioridade.
      - Se clientKey já vier como project_key antigo, faz fallback para o próprio valor recebido.
    """
    explicit_project_key = request.args.get("project_key")
    if explicit_project_key:
        return explicit_project_key

    client_key = _tenant_id_from_request()
    if not client_key:
        return None

    try:
        customer = fetch_one(
            """
            select id, name, key
            from public."Customer"
            where id = %(customer_id)s
              and deleted = false
            limit 1
            """,
            {"customer_id": client_key},
        )

        if customer and customer.get("key"):
            return customer["key"]

        # Fallback: mantém compatibilidade se o header já contiver o project_key.
        return client_key

    except Exception as e:
        print(f"[FINOPS][WARN] erro ao resolver Customer.key pelo clientKey: {e}")
        return client_key


def _customer_context_from_request() -> dict:
    """Retorna metadados do Customer para auditoria/depuração no retorno da API."""
    client_key = _tenant_id_from_request()
    if not client_key:
        return {"clientKey": None, "customer_id": None, "customer_key": None, "customer_name": None}

    try:
        customer = fetch_one(
            """
            select id, name, key
            from public."Customer"
            where id = %(customer_id)s
              and deleted = false
            limit 1
            """,
            {"customer_id": client_key},
        )
        if customer:
            return {
                "clientKey": client_key,
                "customer_id": customer.get("id"),
                "customer_key": customer.get("key"),
                "customer_name": customer.get("name"),
            }
    except Exception as e:
        print(f"[FINOPS][WARN] erro ao carregar contexto do Customer: {e}")

    return {"clientKey": client_key, "customer_id": None, "customer_key": None, "customer_name": None}


OPENAPI_DESCRIPTION = """
# Gabbi FinOps — API de custos, governança e retorno de IA

O **Gabbi FinOps** expõe uma camada de API para consolidar custos de uso de IA, automações, agentes, ROI, filtros executivos e indicadores de decisão para o dashboard real em Node/React.

A API foi organizada para responder perguntas de gestão como:

- Quanto a IA custou no período?
- Quanto desse custo veio de automações?
- Quais agentes concentram maior gasto?
- Qual área de negócio está consumindo mais?
- Qual o ROI estimado e a economia acumulada?
- O que deve aparecer na dobra principal do dashboard FinOps?

## Jornada recomendada de consumo pelo frontend

1. `GET /api/finops/filters` — carrega períodos, áreas, projetos e agentes disponíveis.
2. `GET /api/finops/dataset` — carrega visão consolidada completa do painel.
3. `GET /api/finops/agents/cost` — carrega ranking de custo por agente.
4. `GET /api/finops/hero-fold` — carrega os blocos da dobra principal.
5. `POST /api/finops/usage` — registra uso real vindo do backend Java/JPA, Node ou outro consumidor.
6. `POST /api/finops/pricing` — atualiza tabela de preço por modelo.

## Observação sobre área de negócio

O header `clientKey` é o padrão recomendado para informar a empresa/cliente que está consultando o FinOps. O valor esperado é o `id` da tabela `public."Customer"`. A API valida esse `id`, recupera o campo `Customer.key` e usa esse valor como `project_key` nas consultas FinOps. Também são aceitos, por compatibilidade, `X-Tenant-Id`, `X-Company-Id`, `X-Empresa-Id` e query params de apoio.

O filtro `business_area` é suportado pela API. Caso o banco ainda não possua uma coluna explícita de área, a API continua funcionando e utiliza os dados disponíveis, sem obrigar criação imediata de tabela nova.
"""

TAGS_METADATA = [
    {"name": "00. Portal", "description": "Rotas visuais e documentação customizada da solução FinOps."},
    {"name": "01. Observabilidade", "description": "Health check e validação básica da aplicação."},
    {"name": "02. FinOps Dashboard", "description": "Consulta consolidada para o dashboard executivo de custos, ROI e automações."},
    {"name": "03. Filtros", "description": "Opções disponíveis para filtros de período, área de negócio, projeto e agente."},
    {"name": "04. Agentes", "description": "Ranking de custo por agente com valor absoluto e percentual de participação."},
    {"name": "05. Dobra Principal", "description": "Dados resumidos para os cards principais da visão executiva FinOps."},
    {"name": "06. Ingestão", "description": "Registro de uso, tokens, modelos, automações e precificação."},
]


def _openapi_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Gabbi FinOps API",
            "version": "2.1.0",
            "description": OPENAPI_DESCRIPTION,
            "contact": {"name": "Gabbi / Spread", "url": "https://www.spread.com.br"},
            "license": {"name": "Proprietary / Internal Use"},
        },
        "tags": TAGS_METADATA,
        "servers": [
            {"url": "/", "description": "Servidor atual"},
            {"url": "http://localhost:5000", "description": "Flask local"},
            {"url": "http://localhost:8000", "description": "Gunicorn local"},
        ],
        "paths": {
            "/health": {
                "get": {
                    "tags": ["01. Observabilidade"],
                    "summary": "Health Check",
                    "description": "Verifica se a API FinOps está ativa.",
                    "responses": {"200": {"description": "Aplicação disponível"}},
                }
            },
            "/api/finops/dataset": {
                "get": {
                    "tags": ["02. FinOps Dashboard"],
                    "summary": "Dataset consolidado do dashboard FinOps",
                    "description": "Retorna KPIs, séries, tabelas, recomendações, showback e filtros aplicados para alimentar o dashboard principal.",
                    "parameters": [
                        {"name": "clientKey", "in": "header", "schema": {"type": "string"}, "description": "ID do cliente na tabela public.Customer. Exemplo: Customer.id."},
                        {"name": "X-Tenant-Id", "in": "header", "schema": {"type": "string"}, "description": "Compatibilidade: identificador legado da empresa/tenant."},
                        {"name": "days", "in": "query", "schema": {"type": "integer", "default": 30, "enum": [7, 15, 30, 60, 90]}, "description": "Janela de análise em dias."},
                        {"name": "business_area", "in": "query", "schema": {"type": "string"}, "description": "Área de negócio filtrada. Também aceita alias `area`."},
                        {"name": "project_key", "in": "query", "schema": {"type": "string"}, "description": "Projeto ou tenant lógico do Gabbi."},
                        {"name": "agent_name", "in": "query", "schema": {"type": "string"}, "description": "Nome do agente para análise específica."},
                    ],
                    "responses": {"200": {"description": "Dataset consolidado retornado com sucesso", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FinOpsDataset"}}}}},
                }
            },
            "/api/finops/filters": {
                "get": {
                    "tags": ["03. Filtros"],
                    "summary": "Opções de filtros para o frontend",
                    "description": "Retorna períodos disponíveis, áreas de negócio, projetos e agentes para montar os filtros do dashboard.",
                    "parameters": [
                        {"name": "clientKey", "in": "header", "schema": {"type": "string"}, "description": "ID do cliente na tabela public.Customer. Exemplo: Customer.id."},
                        {"name": "X-Tenant-Id", "in": "header", "schema": {"type": "string"}, "description": "Compatibilidade: identificador legado da empresa/tenant."},
                        {"name": "days", "in": "query", "schema": {"type": "integer", "default": 30}, "description": "Janela de referência para procurar opções."}
                    ],
                    "responses": {"200": {"description": "Filtros disponíveis", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FinOpsFilters"}}}}},
                }
            },
            "/api/finops/agents/cost": {
                "get": {
                    "tags": ["04. Agentes"],
                    "summary": "Custo por agente",
                    "description": "Retorna ranking de agentes com custo total e percentual de representatividade sobre o custo do período.",
                    "parameters": [
                        {"name": "clientKey", "in": "header", "schema": {"type": "string"}, "description": "ID do cliente na tabela public.Customer. Exemplo: Customer.id."},
                        {"name": "X-Tenant-Id", "in": "header", "schema": {"type": "string"}, "description": "Compatibilidade: identificador legado da empresa/tenant."},
                        {"name": "days", "in": "query", "schema": {"type": "integer", "default": 30}, "description": "Janela de análise em dias."},
                        {"name": "business_area", "in": "query", "schema": {"type": "string"}, "description": "Área de negócio."},
                        {"name": "project_key", "in": "query", "schema": {"type": "string"}, "description": "Projeto."},
                        {"name": "agent_name", "in": "query", "schema": {"type": "string"}, "description": "Agente específico."},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Quantidade máxima de agentes no ranking."},
                    ],
                    "responses": {"200": {"description": "Ranking de custo por agente", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentCostResponse"}}}}},
                }
            },
            "/api/finops/hero-fold": {
                "get": {
                    "tags": ["05. Dobra Principal"],
                    "summary": "Dados da dobra principal do FinOps",
                    "description": "Retorna os blocos executivos da seção 'O que mostrar na dobra principal do FinOps'.",
                    "parameters": [
                        {"name": "clientKey", "in": "header", "schema": {"type": "string"}, "description": "ID do cliente na tabela public.Customer. Exemplo: Customer.id."},
                        {"name": "X-Tenant-Id", "in": "header", "schema": {"type": "string"}, "description": "Compatibilidade: identificador legado da empresa/tenant."},
                        {"name": "days", "in": "query", "schema": {"type": "integer", "default": 30}, "description": "Janela de análise em dias."},
                        {"name": "business_area", "in": "query", "schema": {"type": "string"}, "description": "Área de negócio."},
                        {"name": "project_key", "in": "query", "schema": {"type": "string"}, "description": "Projeto."},
                        {"name": "agent_name", "in": "query", "schema": {"type": "string"}, "description": "Agente específico."},
                    ],
                    "responses": {"200": {"description": "Dados da dobra principal", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HeroFoldResponse"}}}}},
                }
            },
            "/api/finops/usage": {
                "post": {
                    "tags": ["06. Ingestão"],
                    "summary": "Registrar uso de IA",
                    "description": "Recebe dados de uso/tokenização de uma interação para alimentar custos, ROI, automações e showback.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UsageIngestRequest"}}}},
                    "responses": {"200": {"description": "Uso registrado"}, "400": {"description": "Payload inválido"}, "500": {"description": "Erro interno"}},
                }
            },
            "/api/finops/pricing": {
                "post": {
                    "tags": ["06. Ingestão"],
                    "summary": "Atualizar precificação de modelo",
                    "description": "Registra ou atualiza o custo por 1k tokens de um modelo.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PricingRequest"}}}},
                    "responses": {"200": {"description": "Precificação salva"}, "400": {"description": "Payload inválido"}, "500": {"description": "Erro interno"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ClientKeyHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "clientKey",
                    "description": "ID do cliente na tabela public.Customer. A API resolve Customer.key e filtra project_key."
                },
                "TenantHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Tenant-Id",
                    "description": "Compatibilidade legada para identificador da empresa/tenant."
                },
                "CompanyHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Company-Id",
                    "description": "Alias aceito para o identificador da empresa."
                }
            },
            "schemas": {
                "FinOpsDataset": {
                    "type": "object",
                    "properties": {
                        "data_source": {"type": "string", "example": "real"},
                        "days": {"type": "integer", "example": 30},
                        "kpis": {"$ref": "#/components/schemas/FinOpsKpis"},
                        "series": {"type": "object"},
                        "tables": {"type": "object"},
                        "recommendations": {"type": "array", "items": {"type": "object"}},
                        "showback": {"type": "object"},
                        "filters": {"type": "object"},
                    },
                },
                "FinOpsKpis": {
                    "type": "object",
                    "properties": {
                        "total_cost": {"type": "number", "example": 2009.60},
                        "manual_cost": {"type": "number", "example": 1342.22},
                        "automation_cost": {"type": "number", "example": 667.38},
                        "interaction_rows": {"type": "integer", "example": 539},
                        "estimated_savings_brl": {"type": "number", "example": 2425.50},
                        "roi_percent": {"type": "number", "example": 20.70},
                        "budget_used_percent": {"type": "number", "example": 80.6},
                    },
                },
                "FinOpsFilters": {
                    "type": "object",
                    "properties": {
                        "periods": {"type": "array", "items": {"type": "integer"}, "example": [7, 15, 30, 60, 90]},
                        "business_areas": {"type": "array", "items": {"type": "string"}, "example": ["Todas as áreas", "Atendimento", "Financeiro"]},
                        "project_keys": {"type": "array", "items": {"type": "string"}},
                        "agents": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "AgentCostResponse": {
                    "type": "object",
                    "properties": {
                        "total_cost": {"type": "number", "example": 2009.60},
                        "agents": {"type": "array", "items": {"$ref": "#/components/schemas/AgentCostItem"}},
                        "filters": {"type": "object"},
                    },
                },
                "AgentCostItem": {
                    "type": "object",
                    "properties": {
                        "agent_name": {"type": "string", "example": "Agente 007"},
                        "total_cost": {"type": "number", "example": 667.38},
                        "percentage": {"type": "number", "example": 33.2},
                    },
                },
                "HeroFoldResponse": {
                    "type": "object",
                    "properties": {
                        "cards": {"type": "array", "items": {"type": "object"}},
                        "summary": {"type": "object"},
                        "filters": {"type": "object"},
                    },
                },
                "UsageIngestRequest": {
                    "type": "object",
                    "required": ["interaction_id", "session_id", "project_key", "agent_name", "model", "input_tokens", "output_tokens", "total_tokens"],
                    "properties": {
                        "interaction_id": {"type": "string", "example": "int-20260511-001"},
                        "session_id": {"type": "string", "example": "sess-abc"},
                        "project_key": {"type": "string", "example": "spread"},
                        "agent_name": {"type": "string", "example": "Agente 007"},
                        "model": {"type": "string", "example": "gpt-4o"},
                        "input_tokens": {"type": "integer", "example": 1200},
                        "output_tokens": {"type": "integer", "example": 400},
                        "total_tokens": {"type": "integer", "example": 1600},
                        "source_type": {"type": "string", "enum": ["MANUAL", "AUTOMATION"], "example": "AUTOMATION"},
                        "task_id": {"type": "string", "example": "T-ANALISE-CONTRATO"},
                        "flow_id": {"type": "string", "example": "F-N8N-RISCO"},
                    },
                },
                "PricingRequest": {
                    "type": "object",
                    "required": ["model", "cost_per_1k_tokens_brl"],
                    "properties": {
                        "model": {"type": "string", "example": "gpt-4o"},
                        "cost_per_1k_tokens_brl": {"type": "number", "example": 0.85},
                        "min_tokens": {"type": "integer", "example": 800},
                        "min_cost_brl": {"type": "number", "example": 0.80},
                    },
                },
            }
        },
    }


def _custom_swagger_html() -> str:
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Gabbi FinOps | API Docs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    :root{--spread-purple:#5F259F;--spread-purple-2:#7A3CC2;--spread-orange:#FF7A00;--bg:#F6F4FB;--panel:#FFFFFF;--text:#2E2E38;--muted:#6E6390;--border:#E6E0F2;--soft:#FAF8FE;--dark:#1F1630;--shadow:0 18px 42px rgba(95,37,159,.14);--radius:22px}*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;background:radial-gradient(circle at top left,rgba(95,37,159,.14),transparent 28%),linear-gradient(180deg,#fff 0%,var(--bg) 42%,#F1ECFA 100%);color:var(--text)}.hero{background:linear-gradient(135deg,#241338 0%,#5F259F 58%,#FF7A00 140%);color:white;padding:34px 42px 40px;position:relative;overflow:hidden}.hero:after{content:"";position:absolute;right:-120px;top:-100px;width:420px;height:420px;border-radius:50%;background:rgba(255,122,0,.18)}.hero-inner{position:relative;z-index:1;max-width:1480px;margin:0 auto;display:grid;grid-template-columns:1fr auto;gap:24px;align-items:center}.brand{display:flex;align-items:center;gap:16px;margin-bottom:18px}.brand-mark{width:54px;height:54px;border-radius:18px;background:linear-gradient(135deg,#FFB547,#FF7A00);display:grid;place-items:center;font-weight:900;color:#241338;box-shadow:0 10px 30px rgba(255,122,0,.28)}.brand small{display:block;font-size:12px;letter-spacing:.18em;text-transform:uppercase;opacity:.78;margin-top:2px}.hero h1{margin:0;font-size:clamp(30px,4vw,52px);letter-spacing:-.04em;line-height:1.02}.hero p{max-width:900px;font-size:17px;line-height:1.72;opacity:.92;margin:18px 0 0}.hero-actions{display:flex;gap:12px;flex-wrap:wrap;justify-content:flex-end}.pill{border:1px solid rgba(255,255,255,.24);background:rgba(255,255,255,.1);color:#fff;padding:11px 15px;border-radius:999px;text-decoration:none;font-size:13px;font-weight:800;backdrop-filter:blur(8px)}.pill.primary{background:#fff;color:#5F259F}.wrap{max-width:1480px;margin:-24px auto 48px;padding:0 28px;position:relative;z-index:2}.grid{display:grid;grid-template-columns:1.05fr .95fr;gap:22px;margin-bottom:22px}.card{background:rgba(255,255,255,.92);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:24px}.card h2{margin:0 0 10px;font-size:22px;letter-spacing:-.02em}.card p,.card li{color:var(--muted);line-height:1.65}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric{background:var(--soft);border:1px solid var(--border);border-radius:18px;padding:16px}.metric b{display:block;font-size:22px;color:var(--spread-purple);margin-bottom:5px}.metric span{font-size:12px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.06em}.mermaid{background:#fff;border:1px solid var(--border);border-radius:18px;padding:16px;overflow:auto}.swagger-shell{background:#fff;border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}.swagger-title{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;background:linear-gradient(90deg,#FAF8FE,#FFF7F0);border-bottom:1px solid var(--border)}.swagger-title h2{margin:0;font-size:22px}.badge{background:#FFF1E6;color:#C75000;border:1px solid #FFD8BA;border-radius:999px;padding:8px 12px;font-weight:900;font-size:12px}.swagger-ui{padding:18px}.swagger-ui .topbar{display:none}.swagger-ui .info{display:none}.swagger-ui .opblock{border-radius:16px!important;overflow:hidden;border-color:#E9E0F5!important;box-shadow:0 8px 24px rgba(95,37,159,.06)!important}.swagger-ui .opblock-tag{font-size:18px!important;color:#251339!important;border-bottom:1px solid #E9E0F5!important}.swagger-ui .btn.execute{background:var(--spread-purple)!important;border-color:var(--spread-purple)!important}.swagger-ui .scheme-container{border-radius:16px;background:#FAF8FE!important;box-shadow:none!important;border:1px solid #E9E0F5!important}@media(max-width:980px){.hero-inner,.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.hero-actions{justify-content:flex-start}}
  </style>
</head>
<body>
  <section class="hero">
    <div class="hero-inner">
      <div>
        <div class="brand"><div class="brand-mark">G</div><div><strong>Gabbi FinOps</strong><small>Spread · API Docs</small></div></div>
        <h1>Documentação executiva e técnica da API FinOps</h1>
        <p>Custos, governança, automações, custo por agente, filtros por área de negócio e dados da dobra principal do dashboard em uma documentação no padrão visual Gabbi/Spread.</p>
      </div>
      <div class="hero-actions"><a class="pill primary" href="#swagger">Abrir endpoints</a><a class="pill" href="/openapi.json" target="_blank">OpenAPI JSON</a><a class="pill" href="/health" target="_blank">Health</a></div>
    </div>
  </section>
  <main class="wrap">
    <section class="grid">
      <div class="card"><h2>Visão da solução</h2><p>Esta API atende o frontend real em Node/React e também mantém compatibilidade com a PoC Flask. A leitura principal deve começar pelo endpoint de filtros e seguir para dataset, agentes e dobra principal.</p><div class="metrics"><div class="metric"><b>4</b><span>Endpoints principais</span></div><div class="metric"><b>ROI</b><span>Retorno estimado</span></div><div class="metric"><b>Área</b><span>Filtro executivo</span></div><div class="metric"><b>Agente</b><span>Showback</span></div></div></div>
      <div class="card"><h2>Fluxo recomendado</h2><div class="mermaid">flowchart TD
        A[Frontend Node/React] --> B[GET /api/finops/filters]
        B --> C[GET /api/finops/dataset]
        C --> D[GET /api/finops/agents/cost]
        C --> E[GET /api/finops/hero-fold]
        F[Backend Java/JPA] --> G[POST /api/finops/usage]
        H[Admin FinOps] --> I[POST /api/finops/pricing]
      </div></div>
    </section>
    <section id="swagger" class="swagger-shell"><div class="swagger-title"><h2>Swagger UI</h2><span class="badge">Try it out habilitado</span></div><div id="swagger-ui"></div></section>
  </main>
  <script>
    mermaid.initialize({startOnLoad:true,theme:'base',themeVariables:{primaryColor:'#F4ECFF',primaryTextColor:'#241338',primaryBorderColor:'#5F259F',lineColor:'#7A3CC2',secondaryColor:'#FFF1E6',tertiaryColor:'#FFFFFF'}});
    window.onload=function(){SwaggerUIBundle({url:'/openapi.json',dom_id:'#swagger-ui',deepLinking:true,displayRequestDuration:true,docExpansion:'none',filter:true,tryItOutEnabled:true,presets:[SwaggerUIBundle.presets.apis,SwaggerUIStandalonePreset],layout:'BaseLayout',syntaxHighlight:{theme:'obsidian'}})};
  </script>
</body>
</html>
"""


@app.route("/openapi.json", methods=["GET"])
def openapi_json():
    return jsonify(_openapi_spec())


@app.route("/docs", methods=["GET"])
@app.route("/apidocs", methods=["GET"])
@app.route("/apidocs/", methods=["GET"])
@app.route("/swagger", methods=["GET"])
def swagger_docs():
    return Response(_custom_swagger_html(), mimetype="text/html")

def get_connection():
    return psycopg2.connect(
        host="192.168.230.108",
        database="gabbi-io",
        user="gabbi_io",
        password="lrc2An*gvNP%00SkW%bY5cFLQV6S0o5v7^",
    )

def _state():
    return sim.load_state()

def get_user_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'select id, email, password, name, deleted from public."User" where lower(email) = lower(%s)',
        (email,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user

def _save(state):
    sim.save_state(state)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        user = get_user_by_email(email)

        if not user:
            flash("Usuário não encontrado", "danger")
            return render_template("login.html")

        user_id, user_email, password_hash, name, active = user

        if not active:
            flash("Usuário inativo", "danger")
            return render_template("login.html")

        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            flash("Senha inválida", "danger")
            return render_template("login.html")

        # salva sessão
        session["user_id"] = str(user_id)
        session["user_name"] = name
        session["user_email"] = user_email
        session["authenticated"] = True

        return redirect(url_for("dashboard"))

    return render_template("login.html")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/health")
def health():
    return jsonify(status="ok"), 200


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


def _get_dataset(state: dict):
    data_source = state.get("data_source", "real")
    days = int(state.get("days", 30))

    if data_source == "real":
        project_key = state.get("project_key")
        agent_name = state.get("agent_name")
        business_area = state.get("business_area")
        return summarize_real(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area)

    return sim.summarize(state)


@app.route("/finops")
def dashboard():
    state = _state()

    data_source = request.args.get("data_source")
    scenario = request.args.get("scenario")
    days = request.args.get("days")

    if data_source or scenario or days:
        sim.apply_controls(
            state,
            data_source=data_source or state.get("data_source", "fake_static"),
            scenario=scenario or state.get("scenario", "growth"),
            days=int(days or state.get("days", 30)),
        )
        _save(state)

    dataset = _get_dataset(state)
    return render_template("finops_dashboard.html", dataset=dataset)


@app.route("/ledger")
def ledger():
    state = _state()
    dataset = _get_dataset(state)
    return render_template("finops_ledger.html", dataset=dataset)


@app.route("/interactions")
def interactions():
    state = _state()
    dataset = _get_dataset(state)
    return render_template("finops_interactions.html", dataset=dataset)


@app.route("/accumulators")
def accumulators():
    state = _state()
    dataset = _get_dataset(state)
    return render_template("finops_accumulators.html", dataset=dataset)


@app.route("/pricing", methods=["GET", "POST"])
def pricing():
    state = _state()

    if request.method == "POST":
        model = (request.form.get("model") or "").strip()

        cost_per_1k = request.form.get("cost_per_1k_tokens_brl")  # opcional
        min_tokens = request.form.get("min_tokens")
        min_cost = request.form.get("min_cost")

        # valida mínimo
        if not model:
            return redirect(url_for("pricing"))

        # Se você não mandar cost_per_1k no form, usa um default (ou busca o último vigente)
        if not cost_per_1k:
            # default simples (você pode mudar)
            cost_per_1k = "0.85"

        try:
            upsert_pricing(
                model=model,
                cost_per_1k_tokens_brl=float(cost_per_1k),
                min_tokens=int(min_tokens) if min_tokens else None,
                min_cost_brl=float(min_cost) if min_cost else None,
            )
        except Exception as e:
            print(f"[PRICING][WARN] falha ao salvar pricing real: {e}")

        return redirect(url_for("pricing"))

    dataset = _get_dataset(state)
    return render_template("finops_pricing.html", dataset=dataset)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    state = _state()
    if request.method == "POST":
        url = (request.form.get("grafana_embed_url") or "").strip()
        sim.set_grafana_url(state, url)
        _save(state)
        return redirect(url_for("settings"))
    dataset = _get_dataset(state)
    return render_template("finops_settings.html", dataset=dataset)


# ---------------- API (front / demo) ----------------

@app.route("/api/state")
def api_state():
    state = _state()
    return jsonify(_get_dataset(state))


@app.route("/api/simulate/reset", methods=["POST"])
def api_reset():
    state = _state()
    sim.reset_live(state)
    _save(state)
    return jsonify({"ok": True, "message": "Estado resetado (live).", "state": _get_dataset(state)})


@app.route("/api/simulate/step", methods=["POST"])
def api_step():
    state = _state()
    payload = request.get_json(silent=True) or {}
    steps = int(payload.get("steps") or 10)
    spike = bool(payload.get("spike") or False)

    if state.get("data_source") == "real":
        return jsonify({"ok": False, "message": "Modo REAL: simulação desabilitada.", "state": _get_dataset(state)}), 400

    out = sim.step_live(state, steps=steps, spike=spike)
    _save(state)
    return jsonify({"ok": True, "result": out, "state": _get_dataset(state)})


@app.route("/api/simulate/spike", methods=["POST"])
def api_spike():
    state = _state()

    if state.get("data_source") == "real":
        return jsonify({"ok": False, "message": "Modo REAL: spike desabilitado.", "state": _get_dataset(state)}), 400

    out = sim.step_live(state, steps=30, spike=True)
    _save(state)
    return jsonify({"ok": True, "result": out, "state": _get_dataset(state)})


# ---------------- API (para consumo do backend Java/JPA) ----------------

@app.route("/api/finops/usage", methods=["POST"])
def api_finops_usage():
    payload = request.get_json(silent=True) or {}
    try:
        out = ingest_usage(payload)
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": "exception", "message": str(e)}), 500


@app.route("/api/finops/pricing", methods=["POST"])
def api_finops_pricing():
    payload = request.get_json(silent=True) or {}
    model = (payload.get("model") or "").strip()
    cost = payload.get("cost_per_1k_tokens_brl")
    if not model or cost is None:
        return jsonify({"ok": False, "error": "missing_field:model|cost_per_1k_tokens_brl"}), 400

    try:
        upsert_pricing(
            model=model,
            cost_per_1k_tokens_brl=float(cost),
            min_tokens=int(payload["min_tokens"]) if payload.get("min_tokens") is not None else None,
            min_cost_brl=float(payload["min_cost_brl"]) if payload.get("min_cost_brl") is not None else None,
        )
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": "exception", "message": str(e)}), 500


@app.route("/api/finops/dataset", methods=["GET"])
def api_finops_dataset():
    days = int(request.args.get("days", "30"))
    project_key = _effective_project_key()
    tenant_id = _tenant_id_from_request()
    agent_name = request.args.get("agent_name") or None
    business_area = request.args.get("business_area") or request.args.get("area") or None
    data = summarize_real(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area)
    data.setdefault("filters", {})["tenant_id"] = tenant_id
    data.setdefault("filters", {})["company_id"] = tenant_id
    data.setdefault("filters", {})["customer"] = _customer_context_from_request()
    return jsonify(data), 200


@app.route("/api/finops/filters", methods=["GET"])
def api_finops_filters():
    days = int(request.args.get("days", "30"))
    tenant_id = _tenant_id_from_request()
    data = get_finops_filter_options(days=days)
    data["tenant_id"] = tenant_id
    data["company_id"] = tenant_id
    return jsonify(data), 200


@app.route("/api/finops/agents/cost", methods=["GET"])
def api_finops_agents_cost():
    days = int(request.args.get("days", "30"))
    limit = int(request.args.get("limit", "20"))
    project_key = _effective_project_key()
    tenant_id = _tenant_id_from_request()
    agent_name = request.args.get("agent_name") or None
    business_area = request.args.get("business_area") or request.args.get("area") or None
    data = get_cost_by_agent(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area, limit=limit)
    data.setdefault("filters", {})["tenant_id"] = tenant_id
    data.setdefault("filters", {})["company_id"] = tenant_id
    data.setdefault("filters", {})["customer"] = _customer_context_from_request()
    return jsonify(data), 200


@app.route("/api/finops/hero-fold", methods=["GET"])
def api_finops_hero_fold():
    days = int(request.args.get("days", "30"))
    project_key = _effective_project_key()
    tenant_id = _tenant_id_from_request()
    agent_name = request.args.get("agent_name") or None
    business_area = request.args.get("business_area") or request.args.get("area") or None
    data = get_hero_fold(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area)
    data.setdefault("filters", {})["tenant_id"] = tenant_id
    data.setdefault("filters", {})["company_id"] = tenant_id
    data.setdefault("filters", {})["customer"] = _customer_context_from_request()
    return jsonify(data), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
