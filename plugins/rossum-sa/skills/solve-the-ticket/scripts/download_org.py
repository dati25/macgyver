#!/usr/bin/env python3
"""Download a Rossum organization into a ticketsolver branch via prd2."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a shell command, raising on failure with full output."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"Exit code: {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-url", required=True, help="Rossum organization URL (e.g. https://my-org.rossum.app)")
    parser.add_argument("--org-id", required=True, help="Rossum organization ID")
    parser.add_argument("--token", required=True, help="Rossum API bearer token")
    parser.add_argument("--git-url", required=True, help="Remote git URL for the ticketsolver repository")
    parser.add_argument("--ticket", required=True, help="Ticket number (used for branch and folder name)")
    args = parser.parse_args()

    api_base = f"{args.org_url.rstrip('/')}/api/v1"
    org_dir_name = "organization"

    # 1. Create a working directory in the user's tmp folder
    tmp_base = Path(tempfile.gettempdir())
    work_dir = tmp_base / f"ticketsolver-{args.ticket}"
    if work_dir.exists():
        raise RuntimeError(f"Work directory already exists: {work_dir}")
    work_dir.mkdir(parents=True)
    print(f"Created work directory: {work_dir}")

    try:
        # 2. Clone the ticketsolver repository
        print(f"Cloning {args.git_url} ...")
        run(["git", "clone", args.git_url, str(work_dir / "repo")])
        repo_dir = work_dir / "repo"

        # 3. Create a new branch named with the ticket number
        branch_name = args.ticket
        print(f"Creating branch: {branch_name}")
        run(["git", "checkout", "-b", branch_name], cwd=repo_dir)

        # 4. Create a new folder with the ticket name
        ticket_dir = repo_dir / args.ticket
        ticket_dir.mkdir()
        print(f"Created ticket folder: {ticket_dir}")

        # 5. Create prd_config.yaml and credentials.yaml
        prd_config = (
            f"directories:\n"
            f"  {org_dir_name}:\n"
            f"    org_id: '{args.org_id}'\n"
            f"    api_base: {api_base}\n"
            f"    subdirectories:\n"
            f"      {org_dir_name}:\n"
            f"        regex: ''\n"
        )
        (ticket_dir / "prd_config.yaml").write_text(prd_config)
        print("Created prd_config.yaml")

        # Create the org directory and credentials.yaml for prd2
        org_path = ticket_dir / org_dir_name
        org_path.mkdir()
        (org_path / "credentials.yaml").write_text(f"token: {args.token}\n")
        print("Created credentials.yaml")

        # 6. Run prd2 pull organization -a
        print("Running prd2 pull organization -a ...")
        result = run(["prd2", "pull", org_dir_name, "-a"], cwd=ticket_dir)
        print(result.stdout)

        # 7. Ensure credentials.yaml is never committed
        gitignore_path = ticket_dir / ".gitignore"
        gitignore_path.write_text(
            "**/credentials.json\n"
            "**/credentials.yaml\n"
            "**/deploy_secrets/\n"
            "**/**/non_versioned_object_attributes.json\n"
            "**/hook_sync_configs/\n"
        )

        # 8. Commit and push all downloaded files
        print("Committing and pushing ...")
        run(["git", "add", "."], cwd=repo_dir)
        run(
            ["git", "commit", "-m", f"Download org {args.org_id} for ticket {args.ticket}"],
            cwd=repo_dir,
        )
        run(["git", "push", "-u", "origin", branch_name], cwd=repo_dir)

        print(f"\nDone. Branch '{branch_name}' pushed to origin.")
        print(f"Local path: {repo_dir}")

    except Exception:
        # Re-raise so the calling agent sees the full traceback
        raise


if __name__ == "__main__":
    main()
