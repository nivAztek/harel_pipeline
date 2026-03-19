"""
Organize Bundle v3 - Harel DAB CI/CD Pipeline

Organizes source repo files into Databricks Asset Bundle (DAB) format.

Source repo structure:
    repo/
    ├── data_sources/   # Notebooks and scripts (.py, .ipynb, .sql)
    ├── files/          # General scripts (.py)
    ├── notebooks/      # Jupyter notebooks (.ipynb)
    ├── data/           # SQL files for Unity Catalog
    └── jobs/           # Job YAML definitions (DAB format)

Output DAB structure:
    bundle/
    ├── databricks.yml
    ├── src/
    │   ├── data_sources/
    │   ├── files/
    │   └── notebooks/
    ├── resources/
    │   └── jobs/
    └── sql/
        └── catalog/

Version: 3.0.0
"""

import os
import sys
import shutil
import argparse
import yaml
import json
import re
from pathlib import Path
from typing import Optional


# Notebook headers required by DAB
NOTEBOOK_HEADERS = {
    '.py': '# Databricks notebook source',
    '.sql': '-- Databricks notebook source',
}

# Source dir -> bundle target dir
DIR_MAP = {
    'data_sources': 'src/data_sources',
    'files':        'src/files',
    'notebooks':    'src/notebooks',
    'data':         'sql/catalog',
}

# File extensions to copy
COPY_EXTENSIONS = {'.py', '.ipynb', '.sql'}

# Directories to skip
IGNORE = {
    '.git', '.github', '__pycache__', '.venv', 'venv',
    '.env', 'node_modules', '.idea', '.vscode', '.databricks',
}


def clean_nulls(obj):
    """Recursively remove None values and empty dicts.

    DAB validates YAML strictly — null values (e.g., run_as.user_name: null)
    cause deploy failures. This cleans them so deploys succeed.

    Important: we check the CLEANED value, not the original.
    e.g. {on_failure: null} -> {} -> removed entirely.
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


def ensure_notebook_header(path: Path):
    """Add Databricks notebook source header if missing."""
    header = NOTEBOOK_HEADERS.get(path.suffix.lower())
    if not header:
        return
    content = path.read_text(encoding='utf-8')
    if not content.lstrip().startswith(header):
        path.write_text(header + '\n' + content, encoding='utf-8')


class BundleOrganizer:
    """Organizes a source repository into DAB format."""

    def __init__(self, source: str, output: str, name: str,
                 workspace_root: str = '/Workspace/projects'):
        self.source = Path(source).resolve()
        self.output = Path(output).resolve()
        self.name = name
        self.workspace_root = workspace_root
        # stem -> relative bundle path  (e.g. "haker_new" -> "src/data_sources/haker_new.py")
        self.file_index: dict[str, str] = {}
        self.errors: list[str] = []

    # ── public ───────────────────────────────────────────────

    def organize(self) -> dict:
        """Main entry point. Returns summary dict."""
        print(f"Bundle: {self.name}")
        print(f"Source: {self.source}")
        print(f"Output: {self.output}\n")

        self._create_dirs()
        copied = self._copy_source_files()
        sql_files = self._copy_sql_files()
        jobs = self._process_jobs()
        self._write_databricks_yml()
        self._write_path_mapping()

        summary = {
            'bundle_name': self.name,
            'files': len(copied),
            'sql': len(sql_files),
            'jobs': len(jobs),
            'errors': self.errors,
        }
        print(f"\nDone: {summary['files']} files, {summary['jobs']} jobs, "
              f"{summary['sql']} SQL, {len(self.errors)} errors")
        for e in self.errors:
            print(f"  ERROR: {e}")
        return summary

    # ── copy files ───────────────────────────────────────────

    def _create_dirs(self):
        for d in ['src/data_sources', 'src/files', 'src/notebooks',
                   'resources/jobs', 'sql/catalog']:
            (self.output / d).mkdir(parents=True, exist_ok=True)

    def _copy_source_files(self) -> list[str]:
        """Copy py/ipynb/sql files from source dirs and build file index."""
        copied = []
        for src_name, tgt_name in DIR_MAP.items():
            src_path = self.source / src_name
            if not src_path.exists():
                continue
            for f in src_path.rglob('*'):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in COPY_EXTENSIONS:
                    continue
                if any(p in IGNORE for p in f.parts):
                    continue

                rel = f.relative_to(src_path)
                target = self.output / tgt_name / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                ensure_notebook_header(target)

                bundle_rel = f"{tgt_name}/{rel}".replace('\\', '/')
                self.file_index[f.stem] = bundle_rel
                copied.append(bundle_rel)
                print(f"  {f.name} -> {bundle_rel}")
        return copied

    def _copy_sql_files(self) -> list[str]:
        """Copy SQL from data/ for Unity Catalog."""
        copied = []
        data_path = self.source / 'data'
        if not data_path.exists():
            return copied
        for f in data_path.rglob('*.sql'):
            if any(p in IGNORE for p in f.parts):
                continue
            rel = f.relative_to(data_path)
            target = self.output / 'sql' / 'catalog' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            self.file_index[f.stem] = f"sql/catalog/{rel}".replace('\\', '/')
            copied.append(str(rel))
            print(f"  SQL: {f.name}")
        return copied

    # ── process jobs ─────────────────────────────────────────

    def _process_jobs(self) -> list[str]:
        """Load job YAMLs, fix paths / nulls / tags, write to bundle."""
        jobs_path = self.source / 'jobs'
        if not jobs_path.exists():
            return []
        processed = []
        for yml in jobs_path.rglob('*.y*ml'):
            if any(p in IGNORE for p in yml.parts):
                continue
            try:
                config = yaml.safe_load(yml.read_text(encoding='utf-8'))
                self._fix_job_config(config)
                config = clean_nulls(config)

                target = self.output / 'resources' / 'jobs' / yml.name
                target.write_text(
                    yaml.dump(config, default_flow_style=False,
                              allow_unicode=True, sort_keys=False),
                    encoding='utf-8',
                )
                processed.append(yml.name)
                print(f"  Job: {yml.name}")
            except Exception as e:
                self.errors.append(f"{yml.name}: {e}")
        return processed

    def _fix_job_config(self, config: dict):
        """Fix everything in job config: paths, git_source, tags."""
        jobs = config.get('resources', {}).get('jobs', {})
        for job_key, job in jobs.items():
            if not isinstance(job, dict):
                continue
            # Remove git_source — DAB manages code deployment
            job.pop('git_source', None)
            # Tag with project name for identification
            job.setdefault('tags', {})['project'] = self.name
        # Recursively fix all file paths in the entire config tree
        self._resolve_paths_recursive(config)

    def _resolve_paths_recursive(self, obj):
        """Walk the entire config and fix notebook_path / python_file wherever found.

        This handles ANY job structure — standard tasks, for_each_task,
        nested task groups, etc. — without assuming a fixed depth.
        """
        if isinstance(obj, dict):
            # Fix notebook_path (notebook_task)
            if 'notebook_path' in obj:
                obj.pop('source', None)  # Remove source: GIT/WORKSPACE
                path = obj['notebook_path']
                if path and not path.startswith('../../'):
                    obj['notebook_path'] = self._resolve_path(path)
            # Fix python_file (spark_python_task)
            if 'python_file' in obj:
                path = obj['python_file']
                if path and not path.startswith('../../'):
                    obj['python_file'] = self._resolve_path(path)
            # Recurse into all values
            for v in obj.values():
                self._resolve_paths_recursive(v)
        elif isinstance(obj, list):
            for item in obj:
                self._resolve_paths_recursive(item)

    def _resolve_path(self, path: str) -> str:
        """Resolve any workspace/relative path to a bundle-relative path (../../src/...)."""
        # Extract filename from the path
        filename = path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
        stem = filename.rsplit('.', 1)[0] if '.' in filename else filename

        # Fast lookup in file index
        if stem in self.file_index:
            return f"../../{self.file_index[stem]}"

        # Fallback: search output dirs on disk (handles files indexed after this job)
        for search_dir in ['src/data_sources', 'src/files', 'src/notebooks']:
            search_path = self.output / search_dir
            if search_path.exists():
                for f in search_path.iterdir():
                    if f.is_file() and f.stem == stem:
                        return f"../../{search_dir}/{f.name}"

        # Last resort: use directory hints from original path
        if 'data_sources' in path:
            return f"../../src/data_sources/{filename}"
        if 'files' in path:
            return f"../../src/files/{filename}"
        return f"../../src/{filename}"

    # ── generate configs ─────────────────────────────────────

    def _write_databricks_yml(self):
        """Generate the main databricks.yml.

        If RUN_AS_SERVICE_PRINCIPAL or RUN_AS_USER env vars are set,
        injects run_as into each target so all jobs inherit it.
        This lets the client control run_as from the Variable Group
        without touching any code or job YAML.
        """
        run_as = self._get_run_as()

        dev_target = {
            'default': True,
            'workspace': {
                'root_path': f"{self.workspace_root}/{self.name}",
            },
        }
        prod_target = {
            'mode': 'production',
            'workspace': {
                'root_path': f"{self.workspace_root}/{self.name}",
            },
        }

        if run_as:
            dev_target['run_as'] = dict(run_as)
            prod_target['run_as'] = dict(run_as)

        config = {
            'bundle': {'name': self.name},
            'include': ['resources/jobs/*.yml'],
            'sync': {
                'include': [
                    'src/**/*.py', 'src/**/*.ipynb',
                    'src/**/*.sql', 'sql/**/*.sql',
                ],
            },
            'targets': {
                'dev': dev_target,
                'prod': prod_target,
            },
        }
        (self.output / 'databricks.yml').write_text(
            yaml.dump(config, default_flow_style=False,
                      allow_unicode=True, sort_keys=False),
            encoding='utf-8',
        )

    @staticmethod
    def _get_run_as() -> Optional[dict]:
        """Read run_as identity from env vars. SP takes priority over user.

        Azure DevOps passes literal '$(VAR_NAME)' when a variable is not
        defined in the Variable Group — we treat that as empty.
        """
        def _val(key: str) -> str:
            v = os.getenv(key, '')
            return '' if v.startswith('$(') else v

        sp = _val('RUN_AS_SERVICE_PRINCIPAL')
        user = _val('RUN_AS_USER')
        if sp:
            print(f"  run_as: service_principal_name={sp}")
            return {'service_principal_name': sp}
        if user:
            print(f"  run_as: user_name={user}")
            return {'user_name': user}
        return None

    def _write_path_mapping(self):
        """Write file index as JSON for debugging reference."""
        mapping = {stem: f"../../{bundle_path}"
                   for stem, bundle_path in self.file_index.items()}
        (self.output / 'path_mapping.json').write_text(
            json.dumps(mapping, indent=2), encoding='utf-8',
        )


def main():
    parser = argparse.ArgumentParser(
        description='Organize source repo into DAB format (v3)',
    )
    parser.add_argument('--source', '-s', required=True,
                        help='Path to source repository')
    parser.add_argument('--output', '-o', required=True,
                        help='Path to output bundle directory')
    parser.add_argument('--name', '-n', required=True,
                        help='Bundle name')
    parser.add_argument('--workspace-root', '-w', default='/Workspace/projects',
                        help='Workspace root path (default: /Workspace/projects)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose output (currently always verbose)')

    args = parser.parse_args()
    organizer = BundleOrganizer(
        source=args.source, output=args.output,
        name=args.name, workspace_root=args.workspace_root,
    )
    summary = organizer.organize()
    sys.exit(1 if summary['errors'] else 0)


if __name__ == '__main__':
    main()
