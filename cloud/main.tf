terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}
variable "project_id" { type = string }
variable "region" {
  type = string
  default = "southamerica-east1"
}
variable "image" {
  type = string
  description = "Immutable Artifact Registry image URL (prefer @sha256 digest)."
}
variable "bucket_name" { type = string }
variable "scheduler_paused" {
  type = bool
  default = true
  description = "Leave paused until secrets, IAM and smoke checks have been verified."
}
provider "google" {
  project = var.project_id
  region = var.region
}
locals {
  services = toset(["run.googleapis.com", "cloudscheduler.googleapis.com", "secretmanager.googleapis.com", "storage.googleapis.com", "artifactregistry.googleapis.com"])
  ticks = flatten([for hour in [9, 12, 15, 18] : [for kind in ["forecast", "observations"] : [for offset in [-20, -10, 0, 10, 20, 30] : {
    name = "${kind}-${hour}-${offset < 0 ? "before" : "after"}-${abs(offset)}"
    kind = kind
    hour = hour
    cron = "${offset < 0 ? 60 + offset : offset} ${offset < 0 ? hour - 1 : hour} * * *"
    day_offset = 0
  }]]])
  recovery = flatten([for hour in [9, 12, 15, 18] : [for delay in [1, 2] : {
    name = "observations-${hour}-recover-${delay}"
    kind = "observations"
    hour = hour
    cron = "20 ${hour + delay} * * *"
    day_offset = 0
  }]])
  yesterday = [for hour in [9, 12, 15, 18] : {
    name = "observations-${hour}-yesterday"
    kind = "observations"
    hour = hour
    cron = "40 8 * * *"
    day_offset = -1
  }]
  schedules = { for tick in concat(local.ticks, local.recovery, local.yesterday) : tick.name => tick }
}
resource "google_project_service" "apis" {
  for_each = local.services
  service = each.value
  disable_on_destroy = false
}
resource "google_service_account" "collector" { account_id = "rjsp-collector" }
resource "google_service_account" "scheduler" { account_id = "rjsp-scheduler" }
resource "google_storage_bucket" "archive" {
  name = var.bucket_name
  location = var.region
  uniform_bucket_level_access = true
  public_access_prevention = "enforced"
  force_destroy = false
  versioning { enabled = true }
  depends_on = [google_project_service.apis]
}
resource "google_storage_bucket_iam_member" "collector" {
  bucket = google_storage_bucket.archive.name
  role = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.collector.email}"
}
resource "google_secret_manager_secret" "meteoblue" {
  secret_id = "rjsp-meteoblue-api-key"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret_iam_member" "collector" {
  secret_id = google_secret_manager_secret.meteoblue.id
  role = "roles/secretmanager.secretAccessor"
  member = "serviceAccount:${google_service_account.collector.email}"
}
resource "google_cloud_run_v2_service" "collector" {
  name = "rjsp-collector-parallel"
  location = var.region
  deletion_protection = true
  template {
    service_account = google_service_account.collector.email
    timeout = "900s"
    max_instance_request_concurrency = 4
    scaling { max_instance_count = 2 }
    containers {
      image = var.image
      resources { limits = { cpu = "2", memory = "1Gi" } }
      env {
        name = "ARTIFACT_BUCKET"
        value = google_storage_bucket.archive.name
      }
      env {
        name = "ARTIFACT_PREFIX"
        value = "parallel-v1"
      }
      env {
        name = "METEOBLUE_API_KEY"
        value_source {
          secret_key_ref {
            secret = google_secret_manager_secret.meteoblue.secret_id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [google_project_service.apis, google_storage_bucket_iam_member.collector, google_secret_manager_secret_iam_member.collector]
}
resource "google_cloud_run_v2_service_iam_member" "invoke" {
  project = var.project_id
  location = var.region
  name = google_cloud_run_v2_service.collector.name
  role = "roles/run.invoker"
  member = "serviceAccount:${google_service_account.scheduler.email}"
}
resource "google_cloud_scheduler_job" "collect" {
  for_each = local.schedules
  name = "rjsp-${each.key}"
  region = var.region
  schedule = each.value.cron
  time_zone = "America/Sao_Paulo"
  paused = var.scheduler_paused
  attempt_deadline = "900s"
  retry_config {
    retry_count = 2
    max_retry_duration = "240s"
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
    max_doublings = 2
  }
  http_target {
    uri = "${google_cloud_run_v2_service.collector.uri}/collect/${each.value.kind}"
    http_method = "POST"
    headers = { "Content-Type" = "application/json" }
    body = base64encode(jsonencode({ hour = each.value.hour, day_offset = each.value.day_offset }))
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience = google_cloud_run_v2_service.collector.uri
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.invoke]
}
output "service_url" { value = google_cloud_run_v2_service.collector.uri }
output "archive_bucket" { value = google_storage_bucket.archive.name }
