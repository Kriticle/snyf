# Fashion Attribute Enrichment Platform

CSV ingestion -> LLM enrichment -> OpenSearch vector retrieval -> LLM-assisted reranking.

This repo contains a runnable local MVP plus Terraform and deployment workflows for the AWS production shape.

For endpoint-level request/response details, see [docs/API.md](/Users/kriti/Documents/New%20project/docs/API.md).

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

Set the deployed API Gateway URL:

```bash
export API_URL=https://xydj4rm9eg.execute-api.us-east-1.amazonaws.com
```

### Deployed End-to-End Smoke Test

This uses the currently deployed AWS stack, OpenAI-backed enrichment, and the shared smoke-test attribute list in S3.

```bash
export API_URL=https://xydj4rm9eg.execute-api.us-east-1.amazonaws.com
export COLLECTION_ID="readme_smoke_$(date +%s)"

SIGNUP_RESPONSE=$(curl -sS -X POST "$API_URL/auth/signup" \
  -H "content-type: application/json" \
  -d '{"email":"readme-smoke-'"$(date +%s)"'@example.com","password":"password123"}')

export API_KEY=$(printf '%s' "$SIGNUP_RESPONSE" | jq -r '.api_key')
export REFRESH_TOKEN=$(printf '%s' "$SIGNUP_RESPONSE" | jq -r '.refresh_token')

curl -sS "$API_URL/auth/api-keys" \
  -H "authorization: Bearer $REFRESH_TOKEN" | jq

cat > /tmp/fashion_catalog.csv <<EOF
product_id,name,image_urls,category,description,price,currency,brand
prod_001,Flowy Resort Dress,https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=800,dresses,"Flowy ivory linen midi dress for beach vacation dinners",128,USD,TestBrand
prod_002,Structured Office Blazer,https://images.unsplash.com/photo-1591369822096-ffd140ec948f?w=800,jacket,"Tailored navy blazer with structured office energy",188,USD,TestBrand
EOF

curl -sS -X POST "$API_URL/collections/$COLLECTION_ID/upload" \
  -H "x-api-key: $API_KEY" \
  -F attribute_list_id=smoke-fashion-v1 \
  -F process_now=false \
  -F csv_file=@/tmp/fashion_catalog.csv | jq

sleep 45

curl -sS -X POST "$API_URL/search" \
  -H "content-type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "collection_id": "'"$COLLECTION_ID"'",
    "query": "flowy beach vacation look",
    "top_k": 5
  }' | jq
```

Expected search response includes ranked products and `image_urls`:

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

The deployed API Gateway currently exposes the product API routes, not a public `/health` route. Use signup, upload, or search smoke tests to verify the deployed stack from outside the VPC.

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
    "status": "ACTIVE",
    "plan": "FREE"
  },
  "session": {
    "session_id": "sess_...",
    "customer_id": "cust_...",
    "status": "ACTIVE"
  },
  "refresh_token": "...",
  "api_key": "fep_...",
  "api_key_id": "key_..."
}
```

Save the API key:

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
  -F process_now=true \
  -F csv_file=@examples/catalog.csv
```

Production upload:

```bash
curl -s -X POST "$INGESTION_URL/collections/coll_ss25/upload" \
  -H "x-api-key: $API_KEY" \
  -F attribute_list_id=fashion-v1 \
  -F process_now=false \
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
        "load_documents": 42.89,
        "extract_query_attributes": 488.17,
        "apply_hard_filters": 0.02,
        "vector_candidate_sort": 0.05,
        "embed_rerank_attributes": 126.48,
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

The deployed marketing-site demo must not include customer API keys in browser code. It calls demo-only routes that are locked server-side to the configured demo collection:

```bash
curl -sS -X POST "$SEARCH_URL/search/demo" \
  -H "content-type: application/json" \
  -d '{"query":"flowy beach wedding look","top_k":5}'

curl -sS -X POST "$SEARCH_URL/search/demo/keyword" \
  -H "content-type: application/json" \
  -d '{"query":"linen dress","top_k":5}'
```

These endpoints do not accept `collection_id` or `x-api-key`; protected customer apps should use `/search` and `/search/keyword`.

## Search Flow

1. Validate `top_k` is between `1` and `100`.
2. Validate `x-api-key`.
3. Reserve one search request against the customer's monthly plan.
4. Confirm the collection belongs to the authenticated customer.
5. Load the collection's registered attribute list.
6. Embed the query.
7. Ask query LLM to return:

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

8. Apply only LLM-returned `category`, `price_min`, and `price_max` hard filters.
9. Retrieve vector candidates.
10. Rerank:

```text
attribute_score = normalize_cosine(embed(attribute + query_value), embed(attribute + document_value))
final_score = sum(weight * attribute_score) / sum(weights for query attributes)
```

If the query LLM extracts no attributes, search falls back to retrieval semantic score.

## Ingestion Flow

1. Validate `x-api-key`.
2. Count CSV rows.
3. Reserve ingestion rows against the customer's monthly plan.
4. Load and validate the attribute list.
5. Register `collection_id -> attribute_list_id`, plus the backend-resolved internal S3 URI.
6. Validate CSV rows permissively.
7. Enqueue one product message per accepted row.
8. Worker downloads images, calls enrichment LLM, embeds enriched text, and writes product state plus OpenSearch document.

## Plan Limits

Plan limits are in [shared/plans.py](/Users/kriti/Documents/New%20project/shared/plans.py).

Current defaults:

```text
FREE:
  search_requests_per_month = 100
  ingestion_rows_per_month = 1000

PRO:
  search_requests_per_month = 10000
  ingestion_rows_per_month = 100000

ENTERPRISE:
  search_requests_per_month = 1000000
  ingestion_rows_per_month = 10000000
```

Usage is tracked monthly in `UsageCounters`.

## Infrastructure

Terraform lives in [infra/terraform](/Users/kriti/Documents/New%20project/infra/terraform).

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
  - `IngestionJobs`
  - `IngestionJobErrors`
- SQS product jobs queue and DLQ
- S3 upload/artifact bucket
- OpenSearch domain
- IAM roles and policies
- CloudWatch log groups

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

Initialize:

```bash
terraform -chdir=infra/terraform init
```

Validate:

```bash
terraform -chdir=infra/terraform validate
```

Plan:

```bash
terraform -chdir=infra/terraform plan \
  -var='aws_region=us-east-1' \
  -var='vpc_id=vpc-xxxxxxxx' \
  -var='private_subnet_ids=["subnet-private-a","subnet-private-b"]' \
  -var='public_subnet_ids=["subnet-public-a","subnet-public-b"]' \
  -var='attribute_list_object_arns=["arn:aws:s3:::customer-config-bucket/attributes/*"]'
```

Apply:

```bash
terraform -chdir=infra/terraform apply
```

Local note: `terraform init` succeeded in this workspace, but `terraform validate` is blocked locally by an AWS provider plugin handshake issue on this Mac runtime. CI on Ubuntu should run validation normally.

## Deployment Pipeline

Workflows live in [.github/workflows](/Users/kriti/Documents/New%20project/.github/workflows).

Required GitHub secret:

```text
AWS_ROLE_ARN
```

Required GitHub variables:

```text
AWS_REGION=us-east-1
ECS_CLUSTER=fashion-enrichment-dev
PROJECT_ENV_PREFIX=fashion-enrichment-dev
```

Workflows:

- `ci.yml`
  - Python tests
  - Terraform formatting

- `terraform.yml`
  - PR: init, validate, plan
  - main: apply behind `production` environment approval

- `deploy-services.yml`
  - Builds changed service images
  - Pushes to ECR
  - Forces ECS service deployment
  - Manual input: `all`, `ingestion_api`, `search_api`, `enrichment_worker`

## Docker Build Commands

Build locally:

```bash
docker build -f services/ingestion_api/Dockerfile -t fashion-ingestion-api .
docker build -f services/search_api/Dockerfile -t fashion-search-api .
docker build -f services/enrichment_worker/Dockerfile -t fashion-enrichment-worker .
```

Build and push ECS-compatible images from Apple Silicon or any non-amd64 machine:

```bash
AWS_ACCOUNT_ID=311549965729
AWS_REGION=us-east-1
ECR_BASE="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/fashion-enrichment-dev"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker buildx build --platform linux/amd64 \
  -t "$ECR_BASE/ingestion-api:latest" \
  --push -f services/ingestion_api/Dockerfile .

docker buildx build --platform linux/amd64 \
  -t "$ECR_BASE/search-api:latest" \
  --push -f services/search_api/Dockerfile .

docker buildx build --platform linux/amd64 \
  -t "$ECR_BASE/enrichment-worker:latest" \
  --push -f services/enrichment_worker/Dockerfile .

aws ecs update-service --region "$AWS_REGION" \
  --cluster fashion-enrichment-dev \
  --service fashion-enrichment-dev-ingestion-api \
  --force-new-deployment

aws ecs update-service --region "$AWS_REGION" \
  --cluster fashion-enrichment-dev \
  --service fashion-enrichment-dev-search-api \
  --force-new-deployment

aws ecs update-service --region "$AWS_REGION" \
  --cluster fashion-enrichment-dev \
  --service fashion-enrichment-dev-enrichment-worker \
  --force-new-deployment

aws ecs wait services-stable --region "$AWS_REGION" \
  --cluster fashion-enrichment-dev \
  --services fashion-enrichment-dev-ingestion-api fashion-enrichment-dev-search-api fashion-enrichment-dev-enrichment-worker
```

## Production Extension Points

- Harden the DynamoDB/SQS/OpenSearch adapters for pagination, conditional writes, and operational backfills.
- Production LLM mode uses [functions/openai_llm.py](/Users/kriti/Documents/New%20project/functions/openai_llm.py) with `LLM_PROVIDER=openai` and `OPENAI_API_KEY` from Secrets Manager.
- Keep [functions/fake_llm.py](/Users/kriti/Documents/New%20project/functions/fake_llm.py) for local/infra-only smoke tests.
- Add retry metadata and job-error persistence for failed worker rows.
- Add job status and job error endpoints backed by `IngestionJobs` and `IngestionJobErrors`.
