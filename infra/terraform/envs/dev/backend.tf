terraform {
  backend "s3" {
    bucket       = "wildlens-tfstate-895770859102"
    key          = "dev/terraform.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }
}