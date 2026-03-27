"""
PatchPilot Git Manager
Handles all git operations: cloning, patching, committing, pushing.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional
from config import get_settings

settings = get_settings()


class GitManager:
    """Manages all git operations for PatchPilot."""

    def __init__(self, repo_owner: str, repo_name: str, github_token: str):
        self.owner = repo_owner
        self.name = repo_name
        self.token = github_token
        self.local_path = Path(settings.REPO_CLONE_DIR) / f"{repo_owner}_{repo_name}"

    # ── Clone ─────────────────────────────────────────────────────────────

    async def clone_or_pull(self, branch: str = "main") -> Path:
        """
        Clone the repository if not present, otherwise pull latest changes.
        Returns the local path to the repository.
        """
        os.makedirs(settings.REPO_CLONE_DIR, exist_ok=True)

        # Authenticated HTTPS clone URL
        clone_url = (
            f"https://x-access-token:{self.token}@github.com/{self.owner}/{self.name}.git"
        )

        if self.local_path.exists():
            print(f"📥 Pulling latest changes for {self.owner}/{self.name}...")
            await self._run_git(["git", "checkout", branch], cwd=self.local_path)
            await self._run_git(["git", "pull", "origin", branch], cwd=self.local_path)
        else:
            print(f"📦 Cloning {self.owner}/{self.name}...")
            await self._run_git(["git", "clone", clone_url, str(self.local_path)])

        return self.local_path

    # ── Branch ────────────────────────────────────────────────────────────

    async def create_branch(self, branch_name: str, from_branch: str = "main"):
        """Create and checkout a new branch for the fix."""
        print(f"🌿 Creating branch: {branch_name}")
        await self._run_git(
            ["git", "checkout", "-b", branch_name, f"origin/{from_branch}"],
            cwd=self.local_path,
        )

    # ── Patch Application ─────────────────────────────────────────────────

    async def apply_patch(self, patch_content: str) -> bool:
        """
        Apply a unified diff patch to the repository.
        Returns True if successful.
        """
        patch_file = Path(settings.PATCH_OUTPUT_DIR) / f"{self.owner}_{self.name}.patch"
        os.makedirs(settings.PATCH_OUTPUT_DIR, exist_ok=True)

        # Write patch to file
        patch_file.write_text(patch_content, encoding="utf-8")
        print(f"🩹 Applying patch from {patch_file}...")

        try:
            # --check first (dry run)
            await self._run_git(
                ["git", "apply", "--check", str(patch_file)],
                cwd=self.local_path,
            )
            # Actually apply
            await self._run_git(
                ["git", "apply", str(patch_file)],
                cwd=self.local_path,
            )
            print("✅ Patch applied successfully.")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Patch failed: {e.stderr}")
            return False

    # ── Commit & Push ─────────────────────────────────────────────────────

    async def commit(self, message: str, author_name: str = "PatchPilot", author_email: str = "patchpilot@ai") -> str:
        """
        Stage all changes and create a commit.
        Returns the commit SHA.
        """
        print(f"💾 Committing: {message[:60]}...")

        # Configure git author
        await self._run_git(["git", "config", "user.name", author_name], cwd=self.local_path)
        await self._run_git(["git", "config", "user.email", author_email], cwd=self.local_path)

        # Stage all changes
        await self._run_git(["git", "add", "-A"], cwd=self.local_path)

        # Commit
        await self._run_git(
            ["git", "commit", "-m", message],
            cwd=self.local_path,
        )

        # Get commit SHA
        result = await self._run_git(
            ["git", "rev-parse", "HEAD"],
            cwd=self.local_path,
            capture_output=True,
        )
        sha = result.strip()
        print(f"✅ Committed: {sha[:8]}")
        return sha

    async def push(self, branch_name: str):
        """Push the fix branch to GitHub."""
        print(f"🚀 Pushing branch {branch_name} to GitHub...")
        await self._run_git(
            ["git", "push", "origin", branch_name],
            cwd=self.local_path,
        )
        print("✅ Push complete.")

    # ── Utilities ─────────────────────────────────────────────────────────

    async def get_file_tree(self, max_files: int = 100) -> str:
        """Return a string listing of repository files for LLM context."""
        result = await self._run_git(
            ["git", "ls-files"],
            cwd=self.local_path,
            capture_output=True,
        )
        files = result.strip().split("\n")[:max_files]
        return "\n".join(files)

    async def read_file(self, relative_path: str) -> Optional[str]:
        """Read a file from the cloned repo."""
        full_path = self.local_path / relative_path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8", errors="replace")
        return None

    async def get_diff(self) -> str:
        """Return the current unstaged diff."""
        result = await self._run_git(
            ["git", "diff"],
            cwd=self.local_path,
            capture_output=True,
        )
        return result

    # ── Internal ──────────────────────────────────────────────────────────

    @staticmethod
    async def _run_git(
        cmd: list[str],
        cwd: Optional[Path] = None,
        capture_output: bool = False,
    ) -> str:
        """Run a git command asynchronously."""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(
                process.returncode, cmd, stdout, stderr.decode()
            )

        return stdout.decode().strip() if capture_output else ""
