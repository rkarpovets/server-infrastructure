variable "cloudflare_account_id" {
  type        = string
  description = "Cloudflare account ID that owns the R2 bucket."
}

variable "r2_bucket_name" {
  type        = string
  description = "Name of the R2 bucket that holds the restic backups."
}
