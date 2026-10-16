output "uploads_bucket" {
  value = aws_s3_bucket.uploads.bucket
}

output "product_table_name" {
  value = aws_dynamodb_table.product_enrichments.name
}

output "collections_table_name" {
  value = aws_dynamodb_table.collections.name
}

output "customers_table_name" {
  value = aws_dynamodb_table.customers.name
}

output "sessions_table_name" {
  value = aws_dynamodb_table.sessions.name
}

output "api_keys_table_name" {
  value = aws_dynamodb_table.api_keys.name
}

output "usage_counters_table_name" {
  value = aws_dynamodb_table.usage_counters.name
}

output "rate_limits_table_name" {
  value = aws_dynamodb_table.rate_limits.name
}

output "search_cache_table_name" {
  value = aws_dynamodb_table.search_cache.name
}

output "ingestion_jobs_table_name" {
  value = aws_dynamodb_table.ingestion_jobs.name
}

output "ingestion_job_errors_table_name" {
  value = aws_dynamodb_table.ingestion_job_errors.name
}

output "product_queue_url" {
  value = aws_sqs_queue.product_jobs.url
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.catalog.endpoint
}

output "api_gateway_endpoint" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "internal_alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "operations_dashboard_name" {
  value = aws_cloudwatch_dashboard.operations.dashboard_name
}

output "ecr_repositories" {
  value = {
    ingestion_api     = aws_ecr_repository.ingestion_api.repository_url
    search_api        = aws_ecr_repository.search_api.repository_url
    enrichment_worker = aws_ecr_repository.enrichment_worker.repository_url
  }
}
