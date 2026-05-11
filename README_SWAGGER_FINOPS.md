# Gabbi FinOps API - Swagger Customizado

Esta versão adiciona documentação OpenAPI/Swagger no mesmo padrão visual do anexo Gabbi Nexus, adaptado para FinOps.

## URLs

- Swagger customizado: `http://localhost:5000/docs`
- Alias: `http://localhost:5000/apidocs/`
- Alias: `http://localhost:5000/swagger`
- OpenAPI JSON: `http://localhost:5000/openapi.json`
- Health: `http://localhost:5000/health`

Se rodar via Gunicorn, troque a porta para `8000`.

## Sem dependência nova

A implementação usa Swagger UI via CDN e uma rota Flask para `/openapi.json`.
Não é necessário instalar Flasgger, Flask-RESTX ou FastAPI.

## Arquivos alterados

- `app.py`: adiciona OpenAPI JSON e página Swagger customizada.
- `real_provider.py`: mantém os endpoints FinOps V2 já criados.

## Endpoints documentados

- `GET /health`
- `GET /api/finops/dataset`
- `GET /api/finops/filters`
- `GET /api/finops/agents/cost`
- `GET /api/finops/hero-fold`
- `POST /api/finops/usage`
- `POST /api/finops/pricing`
