variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "fashion-enrichment"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "service_desired_count" {
  description = "Desired count for API services. Use 0 for first infra apply before images are pushed."
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Desired count for enrichment worker. Use 0 for first infra apply before images are pushed."
  type        = number
  default     = 1
}

variable "assign_public_ip" {
  description = "Assign public IPs to ECS tasks. Useful for default-VPC deployments without NAT."
  type        = bool
  default     = false
}

variable "opensearch_instance_type" {
  type    = string
  default = "t3.small.search"
}

variable "attribute_list_object_arns" {
  description = "S3 object ARNs the ingestion service may read for customer-defined attribute lists."
  type        = list(string)
  default     = []
}

variable "api_cors_allowed_origins" {
  description = "Allowed origins for the public HTTP API."
  type        = list(string)
  default     = ["*"]
}

variable "api_default_throttle_burst_limit" {
  description = "Default API Gateway burst throttle. Per-plan limits are enforced by the app using ApiKeys and UsageCounters."
  type        = number
  default     = 100
}

variable "api_default_throttle_rate_limit" {
  description = "Default API Gateway steady-state requests per second throttle."
  type        = number
  default     = 50
}

variable "llm_provider" {
  description = "LLM provider used by ECS tasks. Use fake for smoke tests or openai for production enrichment/search parsing."
  type        = string
  default     = "fake"
}

variable "openai_api_key_secret_arn" {
  description = "Optional Secrets Manager secret ARN containing OPENAI_API_KEY."
  type        = string
  default     = ""
}
