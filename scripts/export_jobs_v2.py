"""
Export Jobs v2 - Harel DAB CI/CD Pipeline

Exports jobs from Databricks dev workspace by project tag.
Uses the SDK's .as_dict() for complete, clean serialization.

Replaces the original export_jobs_from_dev.py (~420 lines manual serialization)
with a simplified approach (~50 lines).

Requires: DATABRICKS_HOST, DATABRICKS_TOKEN env vars pointing to DEV workspace.

Version: 2.0.0
"""

import os
import re
import sys
import argparse
import yaml
from databricks.sdk import WorkspaceClient


def clean_nulls(obj):
    """Recursively remove None values and empty dicts.

    DAB validates YAML strictly — null values (e.g., run_as.user_name: null)
    cause deploy failures. Checks the CLEANED value, not the original,
    so {on_failure: null} -> {} -> removed entirely.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            cv = clean_nulls(v)
            if cv is not None and cv != {}:
                cleaned[k] = cv
        return cleaned
    if isinstance(obj, list):
        cleaned = [clean_nulls(i) for i in obj]
        return [i for i in cleaned if i is not None]
    return obj


def job_to_dab(job) -> tuple[str, dict]:
    """Convert a Databricks SDK job object to a DAB-ready YAML dict."""
    settings = job.settings.as_dict()
    settings.pop("format", None)   # DAB adds this automatically
    settings.pop("run_as", None)   # Dev user doesn't exist in prod — DAB sets this
    settings = clean_nulls(settings)
    key = re.sub(r"[^a-z0-9]+", "_", settings["name"].lower()).strip("_")
    return key, {"resources": {"jobs": {key: settings}}}


def export_jobs(repo_name: str, output_dir: str, verbose: bool = False):
    """Export all jobs tagged with project=<repo_name> to individual YAML files."""
    w = WorkspaceClient(
        host=os.getenv("DATABRICKS_HOST"),
        token=os.getenv("DATABRICKS_TOKEN"),
    )

    if verbose:
        print(f"Connected to: {os.getenv('DATABRICKS_HOST')}")
    print(f"Searching jobs with tag project={repo_name}...")

    all_jobs = [w.jobs.get(job_id=j.job_id) for j in w.jobs.list()]
    matching = [
        j for j in all_jobs
        if (j.settings.tags or {}).get("project") == repo_name
    ]
    print(f"Found {len(matching)} matching jobs")

    if not matching:
        print("No jobs found — nothing to export.")
        return

    os.makedirs(output_dir, exist_ok=True)

    for job in matching:
        key, dab_config = job_to_dab(job)
        filepath = os.path.join(output_dir, f"{key}.yml")
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(dab_config, f, sort_keys=False, allow_unicode=True)
        print(f"  Exported: {job.settings.name} -> {key}.yml")

    print(f"\nDone: {len(matching)} jobs exported to {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Export jobs from Databricks dev workspace by project tag (v2)",
    )
    parser.add_argument(
        "--repo-name", "-r", required=True,
        help="Project tag to filter jobs by (e.g., family-connections-mock)",
    )
    parser.add_argument(
        "--output-dir", "-o", required=True,
        help="Directory to write exported YAML files",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()
    export_jobs(
        repo_name=args.repo_name,
        output_dir=args.output_dir,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
