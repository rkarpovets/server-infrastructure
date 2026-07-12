output "r2_bucket" {
  value       = cloudflare_r2_bucket.backups.name
  description = "restic R2 backup bucket."
}
