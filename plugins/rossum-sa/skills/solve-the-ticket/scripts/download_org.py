#!/usr/bin/env python3
"""Download a Rossum organization into a ticketsolver branch via prd2.

Safe to re-run for the same ticket: if the work dir already exists and the
repo is clean, the script reuses it and re-pulls idempotently. Pass --force
to wipe the work dir and start fresh.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SUBDIR_NAME = "default"
ORG_DIR_NAME = "organization"
DEFAULT_GIT_URL = "git@gitlab.rossum.cloud:solution-engineering/customers/ticket-solver.git"
GITIGNORE_PATTERNS = [
    "**/credentials.json",
    "**/credentials.yaml",
    "**/deploy_secrets/",
    "**/non_versioned_object_attributes.json",
    "**/hook_sync_configs/",
    "**/__pycache__/",
]


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command. Raises on non-zero exit when check=True with full output."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"Exit code: {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result


def require_tool(name: str, install_hint: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"`{name}` not found on PATH. {install_hint}")


def require_git_identity() -> None:
    email = run(["git", "config", "--get", "user.email"], check=False).stdout.strip()
    name = run(["git", "config", "--get", "user.name"], check=False).stdout.strip()
    if not email or not name:
        raise RuntimeError(
            "Git identity is not configured. Run:\n"
            "  git config --global user.email 'you@rossum.ai'\n"
            "  git config --global user.name 'Your Name'"
        )


def normalize_org_url(url: str) -> str:
    """Strip any trailing /api/v1[/] and trailing slashes."""
    url = url.rstrip("/")
    url = re.sub(r"/api/v1$", "", url)
    return url


def resolve_token(cli_token: str | None) -> str:
    if cli_token:
        return cli_token
    env_token = os.environ.get("ROSSUM_TOKEN")
    if env_token:
        return env_token
    if sys.stdin.isatty():
        raise RuntimeError(
            "No token provided. Pass --token, set ROSSUM_TOKEN, or pipe the token on stdin."
        )
    token = sys.stdin.readline().strip()
    if not token:
        raise RuntimeError("Token read from stdin was empty.")
    return token


def write_prd_config(ticket_dir: Path, org_id: str, api_base: str) -> None:
    prd_config = (
        f"directories:\n"
        f"  {ORG_DIR_NAME}:\n"
        f"    org_id: '{org_id}'\n"
        f"    api_base: {api_base}\n"
        f"    subdirectories:\n"
        f"      {SUBDIR_NAME}:\n"
        f"        regex: ''\n"
    )
    (ticket_dir / "prd_config.yaml").write_text(prd_config)


def write_credentials(ticket_dir: Path, token: str) -> None:
    org_path = ticket_dir / ORG_DIR_NAME
    org_path.mkdir(exist_ok=True)
    (org_path / "credentials.yaml").write_text(f"token: {token}\n")


def merge_gitignore(ticket_dir: Path) -> None:
    """Ensure our patterns are in .gitignore without clobbering existing lines."""
    gitignore = ticket_dir / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    existing_set = set(line.strip() for line in existing)
    added = [p for p in GITIGNORE_PATTERNS if p not in existing_set]
    if not added and existing:
        return
    lines = list(existing)
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(added)
    gitignore.write_text("\n".join(lines) + "\n")


def ensure_branch(repo_dir: Path, branch: str) -> None:
    """Check out <branch>. Create from origin/<branch> if only remote has it; create new otherwise."""
    run(["git", "fetch", "origin"], cwd=repo_dir, check=False)
    local = run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo_dir, check=False)
    if local.returncode == 0:
        run(["git", "checkout", branch], cwd=repo_dir)
        return
    remote = run(
        ["git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"],
        cwd=repo_dir,
        check=False,
    )
    if remote.returncode == 0:
        run(["git", "checkout", "-b", branch, f"origin/{branch}"], cwd=repo_dir)
        return
    run(["git", "checkout", "-b", branch], cwd=repo_dir)


def repo_is_dirty(repo_dir: Path) -> bool:
    result = run(["git", "status", "--porcelain"], cwd=repo_dir)
    return bool(result.stdout.strip())


def summarize_pull(repo_dir: Path, ticket: str) -> str:
    """One-line summary of what prd2 pull produced (via git ls-files)."""
    new_files = run(
        ["git", "ls-files", "--others", "--exclude-standard", f"{ticket}/"],
        cwd=repo_dir,
    ).stdout.splitlines()
    modified = run(
        ["git", "diff", "--name-only", f"{ticket}/"],
        cwd=repo_dir,
    ).stdout.splitlines()
    all_changes = new_files + modified
    if not all_changes:
        return "no changes from pull"
    counts = {
        "workspaces": sum(1 for p in all_changes if "/workspaces/" in p and p.endswith("workspace.json")),
        "queues": sum(1 for p in all_changes if p.endswith("queue.json")),
        "schemas": sum(1 for p in all_changes if p.endswith("schema.json")),
        "hooks": sum(1 for p in all_changes if "/hooks/" in p and p.endswith(".json")),
        "rules": sum(1 for p in all_changes if "/rules/" in p and p.endswith(".json")),
        "labels": sum(1 for p in all_changes if "/labels/" in p and p.endswith(".json")),
    }
    counts = {k: v for k, v in counts.items() if v}
    summary = f"pulled {len(all_changes)} files"
    if counts:
        summary += " (" + ", ".join(f"{v} {k}" for k, v in counts.items()) + ")"
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--org-url",
        required=True,
        help="Rossum organization URL (origin or with /api/v1 suffix; both accepted)",
    )
    parser.add_argument("--org-id", required=True, help="Rossum organization ID")
    parser.add_argument(
        "--token",
        default=None,
        help="Rossum API bearer token. Prefer ROSSUM_TOKEN env var or stdin.",
    )
    parser.add_argument(
        "--git-url",
        default=DEFAULT_GIT_URL,
        help=f"Remote git URL for the ticketsolver repository (default: {DEFAULT_GIT_URL})",
    )
    parser.add_argument("--ticket", required=True, help="Ticket number (branch and folder name)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe an existing work directory and start fresh.",
    )
    args = parser.parse_args()

    require_tool("git", "Install with your OS package manager.")
    require_tool("prd2", "Install with: pipx install project-rossum-deploy")
    require_git_identity()

    token = resolve_token(args.token)
    org_url = normalize_org_url(args.org_url)
    api_base = f"{org_url}/api/v1"

    tmp_base = Path(tempfile.gettempdir())
    work_dir = tmp_base / f"ticketsolver-{args.ticket}"
    repo_dir = work_dir / "repo"
    ticket_dir = repo_dir / args.ticket

    if args.force and work_dir.exists():
        print(f"--force: removing existing {work_dir}")
        shutil.rmtree(work_dir)

    resuming = work_dir.exists()

    if resuming:
        if not repo_dir.exists():
            raise RuntimeError(
                f"Work dir {work_dir} exists but repo subdir is missing. "
                "Pass --force to wipe and start over."
            )
        if repo_is_dirty(repo_dir):
            status = run(["git", "status", "--short"], cwd=repo_dir).stdout
            raise RuntimeError(
                "Resume detected, but repo has uncommitted changes:\n"
                f"{status}\n"
                "Commit, stash, or resolve before rerunning. Pass --force to wipe."
            )
        print(f"Resuming from existing work dir: {work_dir}")
        ensure_branch(repo_dir, args.ticket)
    else:
        work_dir.mkdir(parents=True)
        print(f"Created work directory: {work_dir}")
        print(f"Cloning {args.git_url} ...")
        run(["git", "clone", args.git_url, str(repo_dir)])
        ensure_branch(repo_dir, args.ticket)
        ticket_dir.mkdir(exist_ok=True)

    # Write / refresh config + credentials (idempotent).
    ticket_dir.mkdir(exist_ok=True)
    write_prd_config(ticket_dir, args.org_id, api_base)
    write_credentials(ticket_dir, token)
    merge_gitignore(ticket_dir)

    print("Running prd2 pull ...")
    run(["prd2", "pull", ORG_DIR_NAME, "-a"], cwd=ticket_dir)
    print(summarize_pull(repo_dir, args.ticket))

    # Ensure .gitignore is still complete after prd2 may have touched it.
    merge_gitignore(ticket_dir)

    # Stage only the ticket dir (never `git add .`).
    run(["git", "add", f"{args.ticket}/"], cwd=repo_dir)
    staged = run(["git", "diff", "--cached", "--name-only"], cwd=repo_dir).stdout.strip()
    if not staged:
        print("No new changes to commit. Branch is already up to date.")
    else:
        run(
            ["git", "commit", "-m", f"Download org {args.org_id} for ticket {args.ticket}"],
            cwd=repo_dir,
        )
        print("Pushing branch ...")
        run(["git", "push", "-u", "origin", args.ticket], cwd=repo_dir)

    print(f"\nDone. Branch '{args.ticket}' is ready.")
    print(f"Local path: {repo_dir}")
    print(f"Ticket dir: {ticket_dir}")


if __name__ == "__main__":
    main()
