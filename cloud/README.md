# RJ–SP collection migration — parallel candidate

Status: implementation and offline tests only. No Google Cloud resources have been created, no production schedules changed, and no live cloud collection has been validated.

## Architecture

Cloud Scheduler -> IAM-authenticated Cloud Run HTTP service -> immutable Cloud Storage evidence. GitHub remains source control, offline tests and an optional asynchronous backup. The Python collectors are reused. The source station coordinates, models, horizons and measurement tolerance remain unchanged.

Each scheduled callback supplies a reference hour. The date is derived from `X-CloudScheduler-ScheduleTime`, which is stable across retries, not the service start time. A generation-checked lease serializes each collector/reference pair. Completed events are deduplicated; partial events remain retryable. A failed instance leaves a lease that expires after 35 minutes (collector subprocess timeout 840 seconds).

Forecast requests outside ±30 minutes do not query providers or become official forecasts. Actuals may be consulted later, but selected measurements must still be within ±30 minutes of the original reference, and may not be in the future. Main callbacks run at -20, -10, 0, +10, +20, +30. After nominal, existing complete readings suppress unnecessary queries; a nominal refresh remains required after early success. Actuals additionally recover at +1h20, +2h20 and the following morning.

Raw responses are written to Cloud Storage synchronously after each source response, before processing the remaining sources. Reports are uploaded before a callback can be acknowledged as complete. Cloud Storage uploads use generation preconditions, preventing silent overwrites; storage failures stop success acknowledgement. A final report may be missing after a crash even though the already-uploaded raw responses survive. This is an explicit recovery case, not a complete collection.

Parallel evidence uses the `parallel-v1/` prefix. Backup uses `data/cloud_parallel/parallel-v1/` in GitHub, not the official archive. Existing alert automation continues checking the existing official series until a separately reviewed cutover. It must not be pointed at parallel data without evaluating the validation results.

## Prerequisites and bootstrap

Need a Google Cloud project with billing enabled and permission to enable APIs, manage service accounts, Run, Scheduler, Storage and Secret Manager. Need the Meteoblue key to be entered directly into Secret Manager; do not send credentials in chat, put them in tfvars or commit them. There is no Google Cloud management connection in the current ChatGPT session.

Use an authenticated Cloud Shell terminal with gcloud, Terraform >=1.5 and git. Set task-specific variables, using your own project and a globally unique bucket name:

```bash
export RJSP_PROJECT="your-project-id"
export RJSP_REGION="southamerica-east1"
git clone -b codex/cloud-run-collection https://github.com/marcelobuarquemb2-a11y/estudo-meteorologico-rjsp.git
cd estudo-meteorologico-rjsp
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com storage.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com --project="$RJSP_PROJECT"
gcloud artifacts repositories create rjsp --repository-format=docker --location="$RJSP_REGION" --project="$RJSP_PROJECT"
gcloud builds submit --tag "$RJSP_REGION-docker.pkg.dev/$RJSP_PROJECT/rjsp/collector:parallel-v1" --project="$RJSP_PROJECT" .
```

Find the resulting immutable image digest in Artifact Registry. Copy `cloud/terraform.tfvars.example` to `cloud/terraform.tfvars`, fill in project, bucket, digest and region, and leave `scheduler_paused=true`.

Cloud Run cannot start a revision without a populated Meteoblue secret. Bootstrap only the secret resource, enter its value, then plan the full deployment:

```bash
terraform -chdir=cloud init
terraform -chdir=cloud apply -target=google_secret_manager_secret.meteoblue
read -r -s -p "Meteoblue API key: " RJSP_METEOBLUE_KEY
printf '%s' "$RJSP_METEOBLUE_KEY" | gcloud secrets versions add rjsp-meteoblue-api-key --data-file=- --project="$RJSP_PROJECT"
unset RJSP_METEOBLUE_KEY
terraform -chdir=cloud plan -out=deployment.plan
terraform -chdir=cloud apply deployment.plan
```

The deliberate targeted bootstrap is needed only before the first full deployment. The template uses Application Default Credentials, runtime service identity, private bucket access, Secret Manager and OIDC invocation. No service-account key files are needed. Keep Terraform state outside the public repository; migrate it to a private remote backend before ongoing operations. Limit permissions on state and plan files.

Review Cloud Run readiness and an authenticated test invocation before unpausing schedules. Test `/healthz` with a user identity token; perform provider smoke tests only within an actual forecast window. Then set `scheduler_paused=false`, review `terraform plan`, and apply. Do not disable the GitHub schedule yet.

## Seven-day validation and cutover

1. Run both collectors for seven complete days (28 references); distinguish origin and event IDs.
2. For each of 56 collector/reference combinations, record scheduled time, actual start, completion, coverage, provider failures and persistence failures. Forecast completeness is 288 numeric values plus coordinate/notation/target checks; actuals are 24 numeric values with real measurement timestamps.
3. Forecast goal: 28/28 complete eligible windows. Record the mandatory nominal refresh separately from availability, plus duplicate suppression and storage acknowledgement. Actuals: account for every gap with raw evidence and evaluate publication delay separately; station/source gaps must not masquerade as collector failures.
4. Perform retry, duplicate, process timeout, lease expiry and storage-unavailability checks. Offline coverage does not replace these cloud integration tests.
5. Only after review: set the canonical prefix/import policy, enable regular GitHub backup, adapt the monitoring task and disable the old acquisition scheduler. Preserve all parallel records and failed attempts. No retroactive forecast fabrication or tolerance extension.

`indicator_candidates` preserves available field values with their own times, without silently combining different hourly periods into one observation. Example: Ilha Fiscal had 159° at 15:00 but missing 14:10/14:15 hourly component samples; the selected 15:15 record has three fields and the 15:00 direction is now explicitly preserved as a candidate. Official scoring is not changed by this migration.

## GitHub backup

`cloud/export_archive.py --bucket BUCKET` exports only immutable raw/report/audit objects to the parallel directory, detecting conflicting existing content. `.github/workflows/cloud-backup.yml` is manually dispatched; it retries pushes and leaves the primary evidence intact in Cloud Storage if GitHub fails. To activate it, configure a Workload Identity Federation provider limited to this repository/branch, a backup service account with read-only bucket access, and repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_BACKUP_SERVICE_ACCOUNT`, `GCP_ARTIFACT_BUCKET`. These IAM resources are not created by this template. A schedule can be added after an authenticated backup has passed. Never add a long-lived cloud key to GitHub.

## Cost and limits

This explicit template creates 60 Scheduler jobs (48 main, 8 late recovery, 4 next-day). At the published US$0.10/job/month, Scheduler is approximately US$5.70/month if all three free jobs are available, or US$6 without that allowance. Paused jobs may still incur Scheduler charges. This excludes Cloud Run, Storage and operations, network egress, Secret Manager, build and image storage, taxes and exchange rates. No total cost cap is implied. Configure billing alerts before activation and measure actual duration/traffic during validation. Run scales to zero when idle, with at most two instances, two CPUs and 1GiB per instance. The parallel period also increases meteorological-provider requests; verify Meteoblue quota/contract first.

Reference documentation:
- https://docs.cloud.google.com/scheduler/docs/creating
- https://docs.cloud.google.com/scheduler/docs/http-target-auth
- https://cloud.google.com/scheduler/pricing
- https://cloud.google.com/run/pricing

## Validation performed locally

`python -m unittest -q test_forecast_retries test_observation_retries test_cloud_collection`: 42 tests passed. Includes an archived Ilha Fiscal replay and mocked cloud storage/HTTP callbacks. Python compilation passed. Terraform HCL parsed successfully with python-hcl2. Provider-level `terraform validate/plan`, container build, IAM, authenticated integration tests, billing, live source quota and the seven-day run have not been executed in this environment.
