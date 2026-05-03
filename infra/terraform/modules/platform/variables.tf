variable "name" { type = string }
variable "cluster_arn" { type = string }
variable "execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "image" { type = string }
variable "container_port" { type = number }
variable "private_subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "log_group_name" { type = string }
variable "desired_count" { type = number }
variable "environment" { type = map(string) }
variable "secrets" {
  type    = map(string)
  default = {}
}
variable "target_group_arn" {
  type    = string
  default = null
}
variable "assign_public_ip" {
  type    = bool
  default = false
}
