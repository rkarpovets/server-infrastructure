# Terraform

Manages the Cloudflare R2 bucket that holds the off-site backups, as code.

## Scope

Deliberately small. Terraform owns the **lifecycle of the bucket resource** - its
existence, name and region - so the bucket can be drift-checked and reproduced
from code instead of a console click. It is **not** in the backup data path:
restic writes to R2 with its own S3 credentials from Ansible Vault (the `backup`
role). State is local (`terraform.tfstate`, gitignored), which is fine for a
single operator.

## Files

| File | Purpose |
|------|---------|
| `versions.tf`   | Terraform + provider version pins |
| `providers.tf`  | Cloudflare provider |
| `variables.tf`  | account id + bucket name |
| `cloudflare.tf` | the R2 bucket resource |
| `outputs.tf`    | bucket name |

`terraform.tfvars` and `terraform.tfstate*` are gitignored; `.terraform.lock.hcl`
is committed to pin provider versions.

## Prerequisites

A Cloudflare API token with **Account -> Workers R2 Storage -> Edit** (created at
My Profile -> API Tokens; this is not the R2 S3 access key restic uses):

```sh
cp terraform.tfvars.example terraform.tfvars   # fill in account id + bucket name
export CLOUDFLARE_API_TOKEN=...
```

## Use

```sh
terraform init
terraform plan     # expected: "No changes" - the bucket already exists and matches
terraform apply
```

The bucket predates this config, so it was brought under management with a
one-time import (state records it afterwards):

```sh
terraform import cloudflare_r2_bucket.backups <account_id>/<bucket_name>
```

Because state is local and gitignored, a fresh checkout on another machine needs
that import once more before `plan`/`apply`.

## Cost

Nothing here bills: it only describes an existing bucket. Cloudflare R2's free
tier (10 GB storage, no egress fees) covers the few MB of backup data.
