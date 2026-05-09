terraform {
  backend "s3" {
    bucket         = "fashion-enrichment-dev-terraform-state-311549965729"
    key            = "fashion-enrichment/dev/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    use_lockfile   = true
  }
}
