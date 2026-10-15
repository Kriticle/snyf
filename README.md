# Snyf: AI Catalog Enrichment and Semantic Search MVP

CSV ingestion -> LLM enrichment -> OpenSearch kNN vector retrieval -> cached LLM-assisted reranking.

This repository is a portfolio MVP: it contains a runnable local implementation and Terraform describing an AWS-backed architecture. It is not a currently hosted or production service, and no public API endpoint is provided. The checked-in GitHub Actions workflows run tests and static Terraform validation only; they do not plan, apply, or deploy infrastructure.

For endpoint-level request/response details, see [docs/API.md](docs/API.md).

## What It Does

- Customers sign up and receive an API key.
- Customers upload/index a CSV for a `collection_id`.
- Each collection is mapped to an attribute-list file, for example `fashion-v1.json`.
- The enrichment worker reads product metadata/images and asks an LLM to fill exactly those attributes.
- Enriched attributes are indexed into a searchable product document.
- Search accepts only `collection_id`, `query`, and `top_k`.
- A query LLM extracts semantic attributes and optional hard filters from the query.
- Only `category`, `price_min`, and `price_max` can become hard filters.
- Attribute weights come from the attribute-list file.
- API keys and plan limits are enforced for ingestion and search.

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

Run the API services:

```bash
uvicorn services.ingestion_api.main:app --reload --port 8000
uvicorn services.search_api.main:app --reload --port 8001
```

Local data is written under `data/`.

## Local CLI Smoke Run

Index the sample catalog:

```bash
python -m services.ingestion_api.main examples/catalog.csv \
  --customer-id cust_arket \
  --data-dir data \
  --attribute-list-id fashion-v1 \
  --attribute-list-uri examples/attribute_list.json
```

Search the sample collection:

```bash
python -m services.search_api.main "flowy beach wedding look" \
  --collection-id coll_ss25 \
  --data-dir data \
  --top-k 10
```

## Attribute List

Attribute-list payloads may be simple:

```json
{
  "attributes": ["occasion", "style_mood", "fabric_feel"]
}
```

or weighted:

```json
{
  "attributes": [
    { "name": "occasion", "weight": 1.0 },
    { "name": "style_mood", "weight": 1.0 },
    { "name": "fabric_feel", "weight": 1.0 }
  ]
}
```

Each `collection_id` is registered with:

```json
{
  "collection_id": "coll_ss25",
  "attribute_list_id": "fashion-v1"
}
```

Customers send only `attribute_list_id`; the API resolves the backing URI from the configured attribute-list bucket/prefix and stores that internal URI with the collection. Locally this is stored in `data/collections.json`. In AWS it lives in the `Collections` DynamoDB table.

## API Curl Tests

Set local base URLs:

```bash
export INGESTION_URL=http://localhost:8000
export SEARCH_URL=http://localhost:8001
```

For commands that use a single reverse-proxied API base URL, set it explicitly:

```bash
export API_URL=http://localhost:8000
```

The repository does not publish a live API URL. When running the two development services directly, use `INGESTION_URL` for ingestion/auth routes and `SEARCH_URL` for search routes.

### Add Your Own CSV

Use the dashboard upload card or call the API directly. Your CSV needs these columns:

```text
product_id,name,image_urls
```

Helpful optional columns:

```text
category,description,price,currency,brand,_deleted
```

Rules:

- `image_urls` must contain public HTTPS image URLs.
- Pick one dashboard attribute list for the collection, for example `fashion-v1`.
- Send only the attribute-list file name, not an S3 URI.
- Do not include `collection_id` in the CSV; the upload URL/dashboard collection field supplies it.
- Upload one collection at a time: the URL collection is the collection being indexed.
- After upload, poll the returned `job_id` until status is `READY`, then search the same `collection_id`.

An expected search response includes ranked products and `image_urls`:

```json
{
  "results": [
    {
      "product_id": "prod_001",
      "name": "Flowy Resort Dress",
      "image_urls": [
        "https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=800"
      ],
      "score": 0.95,
      "enriched_attrs": {
        "occasion": "vacation",
        "silhouette": "flowy"
      },
      "category": "dresses",
      "price": 128.0,
      "currency": "USD",
      "brand": "TestBrand"
    }
  ]
}
```

### Health

```bash
curl "$INGESTION_URL/health"
curl "$SEARCH_URL/health"
```

Expected response:

```json
{ "status": "ok" }
```

These health checks describe local development only.

### Signup

```bash
curl -s -X POST "$INGESTION_URL/auth/signup" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

Example response:

```json
{
  "customer": {
    "customer_id": "cust_...",
    "email": "buyer@example.com",
    "status": "PENDING_EMAIL_VERIFICATION",
    "plan": "FREE"
  },
  "verification_required": true,
  "verification_expires_at": 1780387086
}
```

Verify the emailed code, then save the returned API key:

```bash
curl -s -X POST "$INGESTION_URL/auth/verify-email" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","code":"123456"}'
```

```bash
export API_KEY=fep_...
```

Duplicate signup response:

```json
{
  "detail": "Email already exists."
}
```

### Login

```bash
curl -s -X POST "$INGESTION_URL/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

Example response:

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
    "status": "ACTIVE"
  },
  "refresh_token": "..."
}
```

### Upload / Index CSV

Local upload:

```bash
curl -s -X POST "$INGESTION_URL/collections/coll_ss25/upload" \
  -H "x-api-key: $API_KEY" \
  -F attribute_list_id=fashion-v1 \
  -F csv_file=@examples/catalog.csv
```

Reverse-proxied API upload:

```bash
curl -s -X POST "$INGESTION_URL/collections/coll_ss25/upload" \
  -H "x-api-key: $API_KEY" \
  -F attribute_list_id=fashion-v1 \
  -F csv_file=@examples/catalog.csv
```

Example response:

```json
{
  "job_id": "job_coll_ss25_a1b2c3d4e5f6",
  "accepted_rows": 3,
  "rejected_rows": 0,
  "warnings": 0,
  "status": "QUEUED",
  "issues": []
}
```

### Check Upload Status

Use the `job_id` from the upload response to see when the catalog is searchable:

```bash
curl -s "$INGESTION_URL/ingestion-jobs/$JOB_ID" \
  -H "x-api-key: $API_KEY"
```

Example response:

```json
{
  "job_id": "job_coll_ss25_a1b2c3d4e5f6",
  "status": "READY",
  "accepted_rows": 3,
  "queued_rows": 0,
  "processing_rows": 0,
  "enriched_rows": 3,
  "failed_rows": 0,
  "deleted_rows": 0,
  "products": [
    {
      "collection_id": "coll_ss25",
      "product_id": "prod_001",
      "name": "Linen Wide-Leg Trouser",
      "status": "ENRICHED",
      "updated_at": "2026-05-10T00:00:00+00:00"
    }
  ]
}
```

Failed rows:

```bash
curl -s "$INGESTION_URL/ingestion-jobs/$JOB_ID/errors" \
  -H "x-api-key: $API_KEY"
```

Missing or invalid API key:

```json
{
  "detail": "Missing API key."
}
```

Plan limit exceeded:

```json
{
  "detail": "ingestion_rows_per_month exceeded for FREE plan. Please upgrade your plan."
}
```

### Search

```bash
curl -s -X POST "$SEARCH_URL/search" \
  -H "content-type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "collection_id": "coll_ss25",
    "query": "flowy beach wedding look",
    "top_k": 10
  }'
```

Example response:

```json
{
  "metrics": {
    "api_total_ms": 823.41,
    "api_steps_ms": {
      "authenticate_api_key": 12.3,
      "reserve_usage": 8.11,
      "load_collection": 6.44,
      "search_service": 790.02
    },
    "search": {
      "total_ms": 790.0,
      "steps_ms": {
        "load_attribute_list": 35.21,
        "embed_query": 96.31,
        "extract_query_attributes": 488.17,
        "vector_retrieve_documents": 42.89,
        "merge_weights": 0.01,
        "apply_hard_filters": 0.02,
        "embed_query_rerank_attributes": 126.48,
        "rerank_candidates": 0.42,
        "sort_results": 0.01
      },
      "document_count": 4,
      "filtered_count": 4,
      "candidate_count": 4,
      "result_count": 3,
      "embedding_calls": 2,
      "embedding_texts": 4,
      "embedding_cache_hits": 4
    }
  },
  "results": [
    {
      "product_id": "prod_003",
      "name": "Printed Beach Dress",
      "image_urls": [
        "https://cdn.example.com/p3.jpg"
      ],
      "score": 0.58,
      "feature_scores": {
        "_retrieval_semantic": 0.58,
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

Search request over plan limit:

```json
{
  "detail": "search_requests_per_month exceeded for FREE plan. Please upgrade your plan."
}
```

Invalid `top_k`:

```json
{
  "detail": "top_k cannot exceed 100."
}
```

### Public website demo search

The optional marketing-site demo must not include customer API keys in browser code. When configured against an API, it calls demo-only routes that are locked server-side to the configured demo collection:

```bash
curl -sS -X POST "$SEARCH_URL/search/demo" \
  -H "content-type: application/json" \
  -d '{"query":"flowy beach wedding look"}'

curl -sS -X POST "$SEARCH_URL/search/demo/keyword" \
  -H "content-type: application/json" \
  -d '{"query":"linen dress"}'
```

These endpoints do not accept `collection_id`, `top_k`, or `x-api-key`; protected customer apps should use `/search` and `/search/keyword`.
They are still rate limited per client and return `429` if the public demo is overused.

## Search Flow

1. Validate `top_k` is between `1` and `100`.
2. Validate `x-api-key`.
3. Reserve one search request against the customer's monthly plan.
4. Confirm the collection belongs to the authenticated customer.
5. Load the collection's registered attribute list.
6. Embed the query. Search checks in-memory cache first, then the shared DynamoDB search cache, then the configured embedding provider.
7. Ask the query LLM to extract attributes from the query and collection attribute list. The parsed JSON is also cached by query, parser model, and attribute-list fingerprint.

```json
{
  "attributes": {
    "occasion": "beach wedding",
    "style_mood": "flowy"
  },
  "hard_filters": {
    "price_max": 250
  }
}
```

8. Retrieve collection-scoped candidates from OpenSearch using kNN vector search.
9. Apply only LLM-returned `category`, `price_min`, and `price_max` hard filters.
10. Embed only the extracted query attribute values needed for reranking. Product-side attribute embeddings were precomputed during ingestion and stored in OpenSearch.
11. Rerank:

```text
attribute_score = normalize_cosine(embed(attribute + query_value), stored_document_attribute_embedding)
final_score = sum(weight * attribute_score) / sum(weights for query attributes)
```

If the query LLM extracts no attributes, search falls back to retrieval semantic score.

Latency instrumentation:

- API metrics report auth, usage, collection lookup, and total search-service time.
- Search metrics report `load_attribute_list`, `embed_query`, `extract_query_attributes`, `vector_retrieve_documents`, `embed_query_rerank_attributes`, `rerank_candidates`, and `sort_results`.
- `embedding_calls`, `embedding_texts`, and `embedding_cache_hits` show whether a request paid for model embeddings or hit cache.
- Persistent cache hits survive ECS task restarts because query embeddings, query-attribute embeddings, and query parser outputs are stored in DynamoDB with TTL.

## Ingestion Flow

1. Validate `x-api-key`.
2. Count CSV rows.
3. Reserve ingestion rows against the customer's monthly plan.
4. Load and validate the attribute list.
5. Register `collection_id -> attribute_list_id`, plus the backend-resolved internal S3 URI.
6. Validate CSV rows permissively.
7. Enqueue one product message per accepted row.
8. Worker downloads images from the public URLs supplied in the CSV.
9. Worker calls the enrichment LLM with product metadata, images, and the collection attribute list.
10. Worker parses and validates the returned JSON. Empty enriched attributes, missing images, or empty embeddings fail the product and send the message to the DLQ.
11. Worker embeds the enriched product text for retrieval.
12. Worker embeds each enriched attribute value once and stores those vectors as `attribute_embeddings`.
13. Worker writes product state to DynamoDB and a denormalized searchable product document to OpenSearch, including `image_urls`, metadata, full product embedding, enriched attrs, and per-attribute embeddings.

## Plan Limits

Plan limits are in [shared/plans.py](shared/plans.py).

Current defaults:

```text
FREE:
  search_requests_per_month = 100
  ingestion_rows_per_month = 1000
  active_api_keys = 1

PRO:
  search_requests_per_month = 10000
  ingestion_rows_per_month = 100000
  active_api_keys = 5

ENTERPRISE:
  search_requests_per_month = 1000000
  ingestion_rows_per_month = 10000000
  active_api_keys = 25
```

Ingestion row limits count product rows only. The CSV header is not counted. Customers can regenerate API keys, but each plan caps active keys. When a customer creates a new key at the active-key limit, the oldest active key is deactivated.

Usage is tracked monthly in `UsageCounters`.

## Infrastructure

Terraform lives in [infra/terraform](infra/terraform).

It provisions:

- API Gateway HTTP API
- API Gateway VPC Link
- Internal ALB
- ECS Fargate services:
  - `ingestion-api`
  - `search-api`
  - `enrichment-worker`
- ECR repositories for each service
- DynamoDB tables:
  - `Customers`
  - `Sessions`
  - `ApiKeys`
  - `UsageCounters`
  - `Collections`
  - `ProductEnrichments`
  - `SearchCache`
  - `IngestionJobs`
  - `IngestionJobErrors`
- SQS product jobs queue and DLQ
- S3 upload/artifact bucket
- OpenSearch domain
- IAM roles and policies
- CloudWatch log groups

Signup verification email:

- Set `email_from_address` to a verified SES sender address to send real verification emails.
- Set `email_verification_url_base` to the frontend signup URL, such as `https://example.com/signup.html`, to include one-click verification links.
- Leaving `email_from_address` empty logs verification codes from the ingestion API container for local or smoke-test deployments.

DynamoDB safety:

- Stable table names
- `deletion_protection_enabled = true`
- `lifecycle { prevent_destroy = true }`
- Point-in-time recovery enabled

## Terraform Commands

Format:

```bash
terraform -chdir=infra/terraform fmt -recursive
```

Initialize without a remote backend:

```bash
terraform -chdir=infra/terraform init -backend=false
```

For a private deployment, copy `infra/terraform/backend.tf.example` to `backend.tf`, replace every placeholder, and keep the account-specific file out of version control.

Validate:

```bash
terraform -chdir=infra/terraform validate
```

The configuration is retained as an architecture example. Before using it in an AWS account, supply your own backend configuration and variables, review every resource and cost implication, and create a separate deployment process. This public repository intentionally provides no automated plan or apply path.

## Validation Workflows

Workflows live in [.github/workflows](.github/workflows).

Workflows:

- `ci.yml`
  - Python tests
  - Terraform formatting, offline initialization, and validation

- `terraform.yml`
  - Terraform formatting, offline initialization, and validation when infrastructure files change

- `service-builds.yml`
  - Builds service images locally in GitHub Actions to verify the Dockerfiles
  - Does not authenticate to AWS, push images, or update ECS services

## Docker Build Commands

Build locally:

```bash
docker build -f services/ingestion_api/Dockerfile -t fashion-ingestion-api .
docker build -f services/search_api/Dockerfile -t fashion-search-api .
docker build -f services/enrichment_worker/Dockerfile -t fashion-enrichment-worker .
```

Build ECS-compatible images from Apple Silicon or any non-amd64 machine:

```bash
docker buildx build --platform linux/amd64 \
  -t snyf-ingestion-api:local \
  --load -f services/ingestion_api/Dockerfile .

docker buildx build --platform linux/amd64 \
  -t snyf-search-api:local \
  --load -f services/search_api/Dockerfile .

docker buildx build --platform linux/amd64 \
  -t snyf-enrichment-worker:local \
  --load -f services/enrichment_worker/Dockerfile .
```

## Production Readiness Gaps

- Harden the DynamoDB/SQS/OpenSearch adapters for pagination, conditional writes, and operational backfills.
- An OpenAI-backed provider is implemented in [functions/openai_llm.py](functions/openai_llm.py); any deployment must inject credentials through a secret manager.
- Keep [functions/fake_llm.py](functions/fake_llm.py) for deterministic local and infrastructure-only smoke tests.
- Add retry metadata and job-error persistence for failed worker rows.
- Add job status and job error endpoints backed by `IngestionJobs` and `IngestionJobErrors`.

## License

This project is available under the [MIT License](LICENSE).
