locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket" "uploads" {
  bucket_prefix = "${local.name}-uploads-"
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "expire-soft-deleted-images"
    status = "Enabled"

    filter {
      tag {
        key   = "soft_deleted"
        value = "true"
      }
    }

    expiration {
      days = 30
    }
  }
}

resource "aws_dynamodb_table" "product_enrichments" {
  name                        = "${local.name}-product-enrichments"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "collection_id"
  range_key                   = "product_id"
  deletion_protection_enabled = true

  attribute {
    name = "collection_id"
    type = "S"
  }

  attribute {
    name = "product_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  attribute {
    name = "job_id"
    type = "S"
  }

  global_secondary_index {
    name            = "customer-collections"
    hash_key        = "customer_id"
    range_key       = "collection_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "job-products"
    hash_key        = "job_id"
    range_key       = "product_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "collections" {
  name                        = "${local.name}-collections"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "collection_id"
  deletion_protection_enabled = true

  attribute {
    name = "collection_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  global_secondary_index {
    name            = "customer-collections"
    hash_key        = "customer_id"
    range_key       = "collection_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "customers" {
  name                        = "${local.name}-customers"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "customer_id"
  deletion_protection_enabled = true

  attribute {
    name = "customer_id"
    type = "S"
  }

  attribute {
    name = "email"
    type = "S"
  }

  global_secondary_index {
    name            = "email-index"
    hash_key        = "email"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "sessions" {
  name                        = "${local.name}-sessions"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "session_id"
  deletion_protection_enabled = true

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  attribute {
    name = "refresh_token_hash"
    type = "S"
  }

  global_secondary_index {
    name            = "customer-sessions"
    hash_key        = "customer_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "refresh-token-index"
    hash_key        = "refresh_token_hash"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "api_keys" {
  name                        = "${local.name}-api-keys"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "api_key_id"
  deletion_protection_enabled = true

  attribute {
    name = "api_key_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  attribute {
    name = "api_key_hash"
    type = "S"
  }

  global_secondary_index {
    name            = "customer-api-keys"
    hash_key        = "customer_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "api-key-hash-index"
    hash_key        = "api_key_hash"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "usage_counters" {
  name                        = "${local.name}-usage-counters"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "usage_key"
  deletion_protection_enabled = true

  attribute {
    name = "usage_key"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  global_secondary_index {
    name            = "customer-usage"
    hash_key        = "customer_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "ingestion_jobs" {
  name                        = "${local.name}-ingestion-jobs"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "job_id"
  deletion_protection_enabled = true

  attribute {
    name = "job_id"
    type = "S"
  }

  attribute {
    name = "customer_id"
    type = "S"
  }

  attribute {
    name = "collection_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "customer-jobs"
    hash_key        = "customer_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "collection-jobs"
    hash_key        = "collection_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "ingestion_job_errors" {
  name                        = "${local.name}-ingestion-job-errors"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "job_id"
  range_key                   = "error_id"
  deletion_protection_enabled = true

  attribute {
    name = "job_id"
    type = "S"
  }

  attribute {
    name = "error_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_sqs_queue" "product_jobs_dlq" {
  name                      = "${local.name}-product-jobs-dlq"
  message_retention_seconds = 1209600
  tags                      = local.tags
}

resource "aws_sqs_queue" "product_jobs" {
  name                       = "${local.name}-product-jobs"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 1209600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.product_jobs_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.tags
}

resource "aws_opensearch_domain" "catalog" {
  domain_name    = replace(local.name, "_", "-")
  engine_version = "OpenSearch_2.13"

  cluster_config {
    instance_type  = var.opensearch_instance_type
    instance_count = 1
  }

  ebs_options {
    ebs_enabled = true
    volume_size = 20
    volume_type = "gp3"
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https = true
  }

  tags = local.tags
}

resource "aws_ecr_repository" "ingestion_api" {
  name = "${local.name}/ingestion-api"
  tags = local.tags
}

resource "aws_ecr_repository" "search_api" {
  name = "${local.name}/search-api"
  tags = local.tags
}

resource "aws_ecr_repository" "enrichment_worker" {
  name = "${local.name}/enrichment-worker"
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "services" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_security_group" "api_gateway_vpc_link" {
  name        = "${local.name}-apigw-vpc-link"
  description = "API Gateway VPC Link egress to internal ALB"
  vpc_id      = var.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_egress_rule" "api_gateway_to_alb" {
  security_group_id            = aws_security_group.api_gateway_vpc_link.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Internal API ALB"
  vpc_id      = var.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_api_gateway" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.api_gateway_vpc_link.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_services" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
}

resource "aws_lb" "api" {
  name               = substr("${local.name}-api", 0, 32)
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.private_subnet_ids
  tags               = local.tags
}

resource "aws_lb_target_group" "ingestion_api" {
  name        = substr("${local.name}-ingestion", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200-399"
  }

  tags = local.tags
}

resource "aws_lb_target_group" "search_api" {
  name        = substr("${local.name}-search", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200-399"
  }

  tags = local.tags
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = "{\"message\":\"not found\"}"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "search_api" {
  listener_arn = aws_lb_listener.api.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.search_api.arn
  }

  condition {
    path_pattern {
      values = ["/search", "/search/*"]
    }
  }
}

resource "aws_lb_listener_rule" "ingestion_api" {
  listener_arn = aws_lb_listener.api.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ingestion_api.arn
  }

  condition {
    path_pattern {
      values = ["/auth/*", "/collections/*", "/ingestion-jobs/*"]
    }
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  tags = local.tags
}

resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.name}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  count = var.openai_api_key_secret_arn == "" ? 0 : 1
  name  = "${local.name}-task-execution-secrets"
  role  = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.openai_api_key_secret_arn
    }]
  })
}

resource "aws_iam_role" "app_task" {
  name = "${local.name}-app-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "app_access" {
  name = "${local.name}-app-access"
  role = aws_iam_role.app_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.product_enrichments.arn,
          "${aws_dynamodb_table.product_enrichments.arn}/index/*",
          aws_dynamodb_table.collections.arn,
          "${aws_dynamodb_table.collections.arn}/index/*",
          aws_dynamodb_table.customers.arn,
          "${aws_dynamodb_table.customers.arn}/index/*",
          aws_dynamodb_table.sessions.arn,
          "${aws_dynamodb_table.sessions.arn}/index/*",
          aws_dynamodb_table.api_keys.arn,
          "${aws_dynamodb_table.api_keys.arn}/index/*",
          aws_dynamodb_table.usage_counters.arn,
          "${aws_dynamodb_table.usage_counters.arn}/index/*",
          aws_dynamodb_table.ingestion_jobs.arn,
          "${aws_dynamodb_table.ingestion_jobs.arn}/index/*",
          aws_dynamodb_table.ingestion_job_errors.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [
          aws_sqs_queue.product_jobs.arn,
          aws_sqs_queue.product_jobs_dlq.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:PutObjectTagging"
        ]
        Resource = "${aws_s3_bucket.uploads.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = length(var.attribute_list_object_arns) > 0 ? var.attribute_list_object_arns : ["arn:aws:s3:::no-attribute-list-bucket-configured/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "es:ESHttpGet",
          "es:ESHttpHead",
          "es:ESHttpPost",
          "es:ESHttpPut",
          "es:ESHttpDelete"
        ]
        Resource = "${aws_opensearch_domain.catalog.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.openai_api_key_secret_arn != "" ? var.openai_api_key_secret_arn : "arn:aws:secretsmanager:${var.aws_region}:*:secret:no-openai-secret-configured-*"
      }
    ]
  })
}

resource "aws_opensearch_domain_policy" "catalog_access" {
  domain_name = aws_opensearch_domain.catalog.domain_name

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.app_task.arn
        }
        Action = [
          "es:ESHttpGet",
          "es:ESHttpHead",
          "es:ESHttpPost",
          "es:ESHttpPut",
          "es:ESHttpDelete"
        ]
        Resource = "${aws_opensearch_domain.catalog.arn}/*"
      }
    ]
  })
}

module "ingestion_api" {
  source             = "./modules/platform"
  name               = "${local.name}-ingestion-api"
  cluster_arn        = aws_ecs_cluster.main.arn
  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.app_task.arn
  image              = "${aws_ecr_repository.ingestion_api.repository_url}:latest"
  container_port     = var.container_port
  private_subnet_ids = var.private_subnet_ids
  security_group_ids = [aws_security_group.service.id]
  log_group_name     = aws_cloudwatch_log_group.services.name
  desired_count      = var.service_desired_count
  environment        = local.service_environment
  secrets            = local.service_secrets
  target_group_arn   = aws_lb_target_group.ingestion_api.arn
  assign_public_ip   = var.assign_public_ip
}

module "search_api" {
  source             = "./modules/platform"
  name               = "${local.name}-search-api"
  cluster_arn        = aws_ecs_cluster.main.arn
  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.app_task.arn
  image              = "${aws_ecr_repository.search_api.repository_url}:latest"
  container_port     = var.container_port
  private_subnet_ids = var.private_subnet_ids
  security_group_ids = [aws_security_group.service.id]
  log_group_name     = aws_cloudwatch_log_group.services.name
  desired_count      = var.service_desired_count
  environment        = local.service_environment
  secrets            = local.service_secrets
  target_group_arn   = aws_lb_target_group.search_api.arn
  assign_public_ip   = var.assign_public_ip
}

module "enrichment_worker" {
  source             = "./modules/platform"
  name               = "${local.name}-enrichment-worker"
  cluster_arn        = aws_ecs_cluster.main.arn
  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.app_task.arn
  image              = "${aws_ecr_repository.enrichment_worker.repository_url}:latest"
  container_port     = var.container_port
  private_subnet_ids = var.private_subnet_ids
  security_group_ids = [aws_security_group.service.id]
  log_group_name     = aws_cloudwatch_log_group.services.name
  desired_count      = var.worker_desired_count
  environment        = local.service_environment
  secrets            = local.service_secrets
  assign_public_ip   = var.assign_public_ip
}

locals {
  service_environment = {
    PRODUCT_TABLE_NAME         = aws_dynamodb_table.product_enrichments.name
    COLLECTION_TABLE_NAME      = aws_dynamodb_table.collections.name
    CUSTOMERS_TABLE_NAME       = aws_dynamodb_table.customers.name
    SESSIONS_TABLE_NAME        = aws_dynamodb_table.sessions.name
    API_KEYS_TABLE_NAME        = aws_dynamodb_table.api_keys.name
    USAGE_TABLE_NAME           = aws_dynamodb_table.usage_counters.name
    JOBS_TABLE_NAME            = aws_dynamodb_table.ingestion_jobs.name
    JOB_ERRORS_TABLE_NAME      = aws_dynamodb_table.ingestion_job_errors.name
    PRODUCT_QUEUE_URL          = aws_sqs_queue.product_jobs.url
    UPLOAD_BUCKET              = aws_s3_bucket.uploads.bucket
    ATTRIBUTE_LIST_BUCKET      = aws_s3_bucket.uploads.bucket
    ATTRIBUTE_LIST_PREFIX      = "attributes"
    OPENSEARCH_ENDPOINT        = "https://${aws_opensearch_domain.catalog.endpoint}"
    DEFAULT_VECTOR_INDEX       = "${local.name}-products"
    LLM_PROVIDER               = var.llm_provider
    DEMO_RATE_LIMIT_PER_MINUTE = tostring(var.demo_search_rate_limit_per_minute)
    DEMO_TOP_K                 = tostring(var.demo_search_top_k)
  }

  service_secrets = var.openai_api_key_secret_arn == "" ? {} : {
    OPENAI_API_KEY = var.openai_api_key_secret_arn
  }
}

resource "aws_security_group" "service" {
  name        = "${local.name}-service"
  description = "ECS service egress"
  vpc_id      = var.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "service_all" {
  security_group_id = aws_security_group.service.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_headers = ["authorization", "content-type", "x-api-key"]
    allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_origins = var.api_cors_allowed_origins
    max_age       = 3600
  }

  tags = local.tags
}

resource "aws_apigatewayv2_vpc_link" "api" {
  name               = "${local.name}-api-vpc-link"
  security_group_ids = [aws_security_group.api_gateway_vpc_link.id]
  subnet_ids         = var.private_subnet_ids
  tags               = local.tags
}

resource "aws_apigatewayv2_integration" "alb" {
  api_id             = aws_apigatewayv2_api.http.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = aws_lb_listener.api.arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.api.id
}

resource "aws_apigatewayv2_route" "signup" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /auth/signup"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "login" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /auth/login"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "list_api_keys" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /auth/api-keys"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "create_api_key" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /auth/api-keys"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "upload" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /collections/{collection_id}/upload"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "get_collection" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /collections/{collection_id}"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "job_status" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /ingestion-jobs/{job_id}"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "job_errors" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /ingestion-jobs/{job_id}/errors"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "search" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /search"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "keyword_search" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /search/keyword"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "demo_search" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /search/demo"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "demo_keyword_search" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /search/demo/keyword"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = var.api_default_throttle_burst_limit
    throttling_rate_limit  = var.api_default_throttle_rate_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.demo_search.route_key
    throttling_burst_limit = var.demo_search_throttle_burst_limit
    throttling_rate_limit  = var.demo_search_throttle_rate_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.demo_keyword_search.route_key
    throttling_burst_limit = var.demo_search_throttle_burst_limit
    throttling_rate_limit  = var.demo_search_throttle_rate_limit
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
    })
  }

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 30
  tags              = local.tags
}
