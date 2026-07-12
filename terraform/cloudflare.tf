# The R2 bucket restic writes the off-site backups to. Imported, not created
# here - see README.md.
resource "cloudflare_r2_bucket" "backups" {
  account_id = var.cloudflare_account_id
  name       = var.r2_bucket_name
  location   = "EEUR" # must match the existing bucket's region
}
