# Ajustes de CORS e Tenant/Empresa

## CORS

A API agora permite consumo pelo frontend local React/Vite, por padrão:

- http://localhost:5173
- http://127.0.0.1:5173
- http://192.168.230.107:5173

Para configurar outras origens em produção:

```bash
export FINOPS_CORS_ORIGINS="http://localhost:5173,http://192.168.230.107:5173,https://seu-front.com"
```

## Tenant / Empresa

Padrão recomendado para o frontend:

```http
X-Tenant-Id: spread
```

Também são aceitos:

```http
X-Company-Id: spread
X-Empresa-Id: spread
```

E, para facilitar testes no Swagger/Postman:

```http
?tenant_id=spread
?company_id=spread
?empresa_id=spread
```

## Compatibilidade com a base atual

Enquanto a base não tiver uma coluna formal `tenant_id` ou `company_id`, o valor enviado em `X-Tenant-Id` será usado como fallback para `project_key` quando `project_key` não vier na query.

Exemplo:

```http
GET /api/finops/hero-fold?days=30
X-Tenant-Id: spread
```

Será tratado como:

```http
GET /api/finops/hero-fold?days=30&project_key=spread
```

## Exemplo no React

```ts
const response = await fetch("http://192.168.230.107:8098/api/finops/hero-fold?days=30", {
  headers: {
    "Content-Type": "application/json",
    "X-Tenant-Id": "spread"
  }
});

const data = await response.json();
```
