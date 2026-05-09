# Fashion Enrichment API

This API lets customers create an account, upload a product catalog CSV for enrichment, and search an enriched collection.

Production base URL:

```text
https://xydj4rm9eg.execute-api.us-east-1.amazonaws.com
```

Local development uses two services:

```text
Ingestion/auth: http://localhost:8000
Search:         http://localhost:8001
```

## Authentication

Catalog ingestion and search use API-key authentication.

Send a customer API key in every protected ingestion/search request:

```http
x-api-key: fep_...
```

Login sessions are separate from catalog API authorization. `POST /auth/signup` and `POST /auth/login` return session objects for product UI flows. UI-only API-key management endpoints use the `refresh_token` as a bearer token.

## Errors

Errors use FastAPI's standard response shape:

```json
{
  "detail": "Missing API key."
}
```

Common statuses:

| Status | Meaning |
| --- | --- |
| `200` | Request succeeded |
| `400` | Bad signup input or duplicate email |
| `401` | Missing/invalid API key or login failure |
| `402` | Plan limit exceeded |
| `404` | Collection not found or not owned by customer |
| `422` | Invalid JSON/form request shape |

## Signup

Creates a customer, login session, and initial API key.

```http
POST /auth/signup
content-type: application/json
```

Request:

```json
{
  "email": "buyer@example.com",
  "password": "password123"
}
```

Response:

```json
{
  "customer": {
    "customer_id": "cust_...",
    "email": "buyer@example.com",
    "status": "ACTIVE",
    "plan": "FREE",
    "created_at": "2026-05-03T07:58:06.157348+00:00",
    "updated_at": "2026-05-03T07:58:06.157369+00:00"
  },
  "session": {
    "session_id": "sess_...",
    "customer_id": "cust_...",
    "status": "ACTIVE",
    "expires_at": 1780387086,
    "created_at": "2026-05-03T07:58:06.178210+00:00"
  },
  "refresh_token": "...",
  "api_key": "fep_...",
  "api_key_id": "key_..."
}
```

Example:

```bash
curl -sS -X POST "$API_URL/auth/signup" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

## Login

Creates a new login session for an existing customer.

```http
POST /auth/login
content-type: application/json
```

Request:

```json
{
  "email": "buyer@example.com",
  "password": "password123"
}
```

Response:

```json
{
  "customer": {
    "customer_id": "cust_...",
    "email": "buyer@example.com",
    "status": "ACTIVE",
    "plan": "FREE"
  },
  "session": {
    "session_id": "sess_...",
    "customer_id": "cust_...",
    "status": "ACTIVE",
    "expires_at": 1780387086
  },
  "refresh_token": "..."
}
```

Example:

```bash
curl -sS -X POST "$API_URL/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

## API Keys

Lists the customer's API keys. New keys include the retrievable `api_key` value. Older keys created before this field existed may have `api_key: null`.

```http
GET /auth/api-keys
authorization: Bearer <refresh_token>
```

Example:

```bash
curl -sS "$API_URL/auth/api-keys" \
  -H "authorization: Bearer $REFRESH_TOKEN"
```

Creates another API key for the logged-in customer and returns the raw key.

```http
POST /auth/api-keys
authorization: Bearer <refresh_token>
```

Example:

```bash
curl -sS -X POST "$API_URL/auth/api-keys" \
  -H "authorization: Bearer $REFRESH_TOKEN"
```

## Upload Catalog CSV

Uploads a catalog CSV, registers the collection's attribute list, and queues one enrichment message per accepted product row.

```http
POST /collections/{collection_id}/upload
x-api-key: fep_...
content-type: multipart/form-data
```

Form fields:

| Field | Required | Description |
| --- | --- | --- |
| `csv_file` | Yes | CSV file containing products |
| `attribute_list_id` | Yes for production | Attribute-list file name, for example `fashion-v1` or `fashion-v1.json`. The backend resolves this under the configured attribute-list bucket/prefix. |
| `attributes` | Local/dev fallback | Inline comma-separated or JSON array of attributes |
| `process_now` | Optional | Local/dev helper. In production use `false`; ECS worker processes SQS. |

Required CSV columns:

```text
product_id,collection_id,name,image_urls
```

Optional CSV columns:

```text
category,description,price,currency,brand,attributes,_deleted
```

Attribute-list example:

```json
{
  "attributes": [
    { "name": "occasion", "weight": 1.0 },
    { "name": "silhouette", "weight": 1.0 },
    { "name": "fabric_feel", "weight": 1.0 },
    { "name": "trend_vibe", "weight": 1.0 }
  ]
}
```

Example:

```bash
curl -sS -X POST "$API_URL/collections/coll_ss25/upload" \
  -H "x-api-key: $API_KEY" \
  -F attribute_list_id=fashion-v1 \
  -F process_now=false \
  -F csv_file=@catalog.csv
```

Response:

```json
{
  "job_id": "job_coll_ss25",
  "accepted_rows": 3,
  "rejected_rows": 0,
  "warnings": 0,
  "status": "QUEUED",
  "issues": [],
  "messages": [
    {
      "customer_id": "cust_...",
      "collection_id": "coll_ss25",
      "product_id": "prod_001",
      "image_urls": ["https://cdn.example.com/p1.jpg"],
      "metadata": {
        "name": "Linen Wide-Leg Trouser",
        "category": "bottoms",
        "description": "Relaxed cream linen trousers...",
        "price": 89.0,
        "currency": "USD",
        "brand": "Arket"
      },
      "attribute_schema": ["occasion", "silhouette", "fabric_feel"],
      "operation": "UPSERT",
      "attribute_list_uri": "s3://configured-attribute-bucket/attributes/fashion-v1.json",
      "version": 0
    }
  ]
}
```

Notes:

- Product images are not copied into our S3 bucket. The CSV supplies public image URLs.
- The worker downloads those URLs during enrichment.
- `image_urls` are stored on the product record and denormalized into the OpenSearch document so search can render product cards without a second lookup.
- Current implementation accepts the CSV row's `collection_id`; we should tighten validation so it must match the path `collection_id`.

## Search

Searches a collection and returns ranked product-card results.

```http
POST /search
x-api-key: fep_...
content-type: application/json
```

Request:

```json
{
  "collection_id": "coll_ss25",
  "query": "flowy beach vacation look",
  "top_k": 10
}
```

Parameters:

| Field | Required | Description |
| --- | --- | --- |
| `collection_id` | Yes | Collection to search. Must belong to the API-key customer. |
| `query` | Yes | Natural-language search query. |
| `top_k` | No | Number of results. Defaults to `20`; maximum is `100`. |

Response:

```json
{
  "results": [
    {
      "product_id": "prod_003",
      "name": "Printed Beach Dress",
      "image_urls": ["https://cdn.example.com/p3.jpg"],
      "score": 0.620096,
      "feature_scores": {
        "_retrieval_semantic": 0.620096,
        "_weight_total": 0.0
      },
      "enriched_attrs": {
        "occasion": null,
        "silhouette": null,
        "fabric_feel": null,
        "trend_vibe": null,
        "print_energy": "dress dresses bold red print midi dress made for"
      },
      "category": "dresses",
      "price": 129.0,
      "currency": "USD",
      "brand": "Arket"
    }
  ]
}
```

Example:

```bash
curl -sS -X POST "$API_URL/search" \
  -H "content-type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "collection_id": "coll_ss25",
    "query": "flowy beach vacation look",
    "top_k": 10
  }'
```

Search behavior:

- The API accepts only `collection_id`, `query`, and `top_k`.
- Search loads the collection's registered attribute list.
- The query parser LLM extracts query attributes and optional hard filters.
- Only `category`, `price_min`, and `price_max` can be hard filters.
- If no hard filter is extracted, nothing is excluded based on category or price.
- Attribute weights come from the attribute list.
- Results are reranked by weighted attribute similarity; if no query attributes are extracted, the result falls back to retrieval semantic score.

## Plan Limits

Plan limits are enforced for protected ingestion and search requests.

Current defaults:

| Plan | Search requests/month | Ingestion rows/month |
| --- | ---: | ---: |
| `FREE` | 100 | 1,000 |
| `PRO` | 10,000 | 100,000 |
| `ENTERPRISE` | 1,000,000 | 10,000,000 |

Limit error example:

```json
{
  "detail": "search_requests_per_month exceeded for FREE plan. Please upgrade your plan."
}
```

## Current Production Notes

- Public routing uses API Gateway HTTP API.
- Customer API auth is app-level via `x-api-key`.
- Product images remain hosted by the customer/CDN; we store and return their URLs.
- Attribute-list files are read from S3 in production.
- Current deployment uses `LLM_PROVIDER=openai` with `OPENAI_API_KEY` injected from AWS Secrets Manager.
- For low-cost infra-only smoke tests, Terraform can be applied with `llm_provider=fake`.
