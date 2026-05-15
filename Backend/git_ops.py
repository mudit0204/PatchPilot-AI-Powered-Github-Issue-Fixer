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
        self.last_patch_error = ""

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
            error_msg = e.stderr if hasattr(e, 'stderr') else str(e)
            self.last_patch_error = error_msg
            print(f"❌ Patch failed: {error_msg}")
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

    async def get_current_diff(self) -> str:
        """Get the diff of all uncommitted changes (staged + unstaged)."""
        result = await self._run_git(
            ["git", "diff", "HEAD"],
            cwd=self.local_path,
            capture_output=True,
        )
        return result

    async def has_changes(self) -> bool:
        """Check if there are uncommitted changes in the repository."""
        try:
            result = await self._run_git(
                ["git", "status", "--porcelain"],
                cwd=self.local_path,
                capture_output=True,
            )
            return bool(result.strip())
        except Exception:
            return False

    async def get_all_files(self) -> list[str]:
        """Get list of all files tracked by git in the repository."""
        try:
            result = await self._run_git(
                ["git", "ls-files"],
                cwd=self.local_path,
                capture_output=True,
            )
            return [f for f in result.strip().split("\n") if f]
        except Exception:
            return []

    async def apply_patch_direct(self, patch_content: str) -> bool:
        """
        Apply patch by directly modifying files.
        This is a fallback when git apply fails.
        Returns True if successful.
        """
        import difflib
        import re

        try:
            # Parse patch lines
            lines = patch_content.split("\n")
            i = 0
            while i < len(lines):
                line = lines[i]

                # Look for diff headers (--- a/path/to/file or +++ b/path/to/file)
                if line.startswith("--- a/"):
                    file_path = line[6:].strip()
                    i += 1

                    # Skip to the actual content changes
                    while i < len(lines) and not lines[i].startswith("@@"):
                        i += 1

                    if i >= len(lines):
                        break

                    # Extract hunk header to understand the context
                    hunk_header = lines[i]
                    i += 1

                    # Collect patch lines for this file
                    patch_lines = []
                    while i < len(lines) and not lines[i].startswith("--- "):
                        if not lines[i].startswith("+++"):
                            patch_lines.append(lines[i])
                        i += 1

                    # Try to apply the patch to the file
                    full_path = self.local_path / file_path
                    if full_path.exists():
                        try:
                            content = full_path.read_text(encoding="utf-8", errors="replace")
                            original_lines = content.split("\n")

                            # Apply diff logic
                            new_lines = original_lines.copy()
                            offset = 0

                            for patch_line in patch_lines:
                                if patch_line.startswith("-"):
                                    # Remove line
                                    line_to_remove = patch_line[1:]
                                    for idx, orig_line in enumerate(new_lines):
                                        if orig_line == line_to_remove:
                                            del new_lines[idx]
                                            break
                                elif patch_line.startswith("+"):
                                    # Add line
                                    line_to_add = patch_line[1:]
                                    # Find the context and insert after
                                    if idx < len(new_lines):
                                        new_lines.insert(idx + 1, line_to_add)

                            # Write back the modified content
                            full_path.write_text("\n".join(new_lines), encoding="utf-8")
                            print(f"✅ Directly patched: {file_path}")
                        except Exception as e:
                            print(f"⚠️ Failed to patch {file_path}: {e}")
                else:
                    i += 1

            print("✅ Direct patch application completed.")
            return True
        except Exception as e:
            self.last_patch_error = str(e)
            print(f"❌ Direct patch failed: {e}")
            return False

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
