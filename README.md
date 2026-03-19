# Central Pipeline

Centralized CI/CD pipeline for deploying Databricks Asset Bundles (DAB) across multiple project repositories.

## Structure

```
central-pipeline/
├── scripts/
│   ├── export_jobs_v2.py        # Exports tagged jobs from the dev workspace
│   └── organize_bundle_v3.py   # Organizes source files into DAB format
├── templates/
│   └── deploy-bundle.yml        # Reusable Azure DevOps pipeline template
└── README.md
```

---

## How It Works

1. A push to the `prod` branch of any project repo triggers its pipeline.
2. The pipeline references `templates/deploy-bundle.yml` from this repo.
3. The template:
   - Exports all jobs tagged with the project name from the **dev** workspace.
   - Organizes them into a valid DAB bundle.
   - Validates and deploys the bundle to the **prod** workspace.

---

## Onboarding a New Project Repo

### Step 1 — Add `azure-pipelines.yml` to the project repo

```yaml
trigger:
  branches:
    include:
      - prod
  paths:
    exclude:
      - README.md

resources:
  repositories:
    - repository: central
      type: git
      name: <AzureDevOpsProject>/central-pipeline
      ref: main

stages:
  - template: templates/deploy-bundle.yml@central
    parameters:
      repoName: <repo-name-as-tagged-in-databricks>
```

Replace:
- `<AzureDevOpsProject>` — the Azure DevOps project name (e.g. `DataEngineering`)
- `<repo-name-as-tagged-in-databricks>` — the tag used to mark jobs in the dev workspace (e.g. `family-connections`)

### Step 2 — Create a Pipeline in Azure DevOps

- Point the pipeline to the `azure-pipelines.yml` file in the project repo.
- On the first run, Azure DevOps will prompt for permission to access `central-pipeline` — approve it once.

### Step 3 — Configure the Variable Group

Create a Variable Group named `databricks-prod-vars` in **Pipelines → Library** with the following variables:

| Variable | Description |
|---|---|
| `DATABRICKS_HOST` | Prod workspace URL (e.g. `https://adb-xxxx.azuredatabricks.net`) |
| `DATABRICKS_TOKEN` | PAT for the prod workspace |
| `DEV_DATABRICKS_HOST` | Dev workspace URL |
| `DEV_DATABRICKS_TOKEN` | PAT for the dev workspace |
| `RUN_AS_USER` | (Optional) Email of the user to run jobs as |
| `RUN_AS_SERVICE_PRINCIPAL` | (Optional) Application ID of the service principal to run jobs as |

> `RUN_AS_SERVICE_PRINCIPAL` takes priority over `RUN_AS_USER`. If neither is set, no `run_as` is injected.
> The service principal must be registered in the prod Databricks workspace before deploying.

### Step 4 — Tag Jobs in Dev

Every job that should be deployed must have a tag matching `repoName` in the dev workspace.
Jobs without the tag are ignored during export.

---

## Template Parameters

| Parameter | Required | Default | Description |
|---|---|---|---|
| `repoName` | ✅ | — | Project name. Must match the job tag in dev. |
| `workspaceRoot` | ❌ | `/Workspace/projects` | Root path for notebooks in the prod workspace. |
| `prodVarsGroup` | ❌ | `databricks-prod-vars` | Name of the Azure DevOps Variable Group. |
