"""
PatchPilot Git Manager
Handles all git operations: cloning, patching, committing, pushing.
"""

import asyncio
import os
import subprocess
import shutil
import re
import difflib
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
        self.default_branch = "main"
        self.last_patch_error: str = ""

    # ── Clone ─────────────────────────────────────────────────────────────

    async def clone_or_pull(self, branch: Optional[str] = None) -> Path:
        """
        Clone the repository if not present, otherwise pull latest changes.
        Returns the local path to the repository.
        """
        os.makedirs(settings.REPO_CLONE_DIR, exist_ok=True)

        # Authenticated HTTPS clone URL
        clone_url = (
            f"https://x-access-token:{self.token}@github.com/{self.owner}/{self.name}.git"
        )

        # Check if it's a valid git repository
        is_valid_repo = self.local_path.exists() and (self.local_path / ".git").exists()

        if is_valid_repo:
            print(f"📥 Pulling latest changes for {self.owner}/{self.name}...")
            try:
                await self._run_git(["git", "fetch", "origin", "--prune"], cwd=self.local_path)
                self.default_branch = branch or await self._detect_default_branch()
                await self._run_git(["git", "checkout", self.default_branch], cwd=self.local_path)
                await self._run_git(["git", "pull", "origin", self.default_branch], cwd=self.local_path)
            except subprocess.CalledProcessError as e:
                print(f"⚠️ Pull failed ({e}). Re-cloning repository...")
                shutil.rmtree(self.local_path, ignore_errors=True)
                await self._run_git(["git", "clone", clone_url, str(self.local_path)])
                self.default_branch = branch or await self._detect_default_branch()
        else:
            # If directory exists but is not a valid repo, clean it up
            if self.local_path.exists():
                print(f"🧹 Cleaning up partial/invalid repository at {self.local_path}...")
                shutil.rmtree(self.local_path, ignore_errors=True)
            
            print(f"📦 Cloning {self.owner}/{self.name}...")
            await self._run_git(["git", "clone", clone_url, str(self.local_path)])
            self.default_branch = branch or await self._detect_default_branch()

        return self.local_path

    # ── Branch ────────────────────────────────────────────────────────────

    async def create_branch(self, branch_name: str, from_branch: Optional[str] = None):
        """Create and checkout a new branch for the fix."""
        base_branch = from_branch or self.default_branch
        print(f"🌿 Creating branch: {branch_name}")
        await self._run_git(
            ["git", "checkout", "-B", branch_name, f"origin/{base_branch}"],
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

        normalized_patch = self._normalize_patch_content(patch_content)

        if not self._patch_has_effective_changes(normalized_patch):
            self.last_patch_error = "Patch has no effective code changes."
            print(f"❌ {self.last_patch_error}")
            return False

        # Write patch to file
        patch_file.write_text(normalized_patch, encoding="utf-8")
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
            self.last_patch_error = ""
            return True
        except subprocess.CalledProcessError as e:
            self.last_patch_error = (e.stderr or e.output or str(e)).strip()
            print(f"❌ git apply failed: {self.last_patch_error}")

            # Try with --3way for better merge handling
            try:
                await self._run_git(
                    ["git", "apply", "--3way", str(patch_file)],
                    cwd=self.local_path,
                )
                print("✅ Patch applied with --3way merge.")
                self.last_patch_error = ""
                return True
            except subprocess.CalledProcessError:
                pass

            return False

    async def apply_patch_direct(self, patch_content: str) -> bool:
        """
        Direct file-modification fallback when `git apply` fails.
        Parses the patch to extract old/new content and applies changes
        directly by reading/writing the target file.
        Returns True if at least one file was modified.
        """
        print("🔧 Attempting direct file modification...")
        normalized = self._normalize_patch_content(patch_content)
        files_modified = 0

        # Parse the patch into per-file sections
        sections = self._parse_patch_sections(normalized)

        for section in sections:
            target_file = section.get("target")
            if not target_file:
                continue

            full_path = self.local_path / target_file
            if not full_path.exists():
                # Try to infer the correct path
                inferred = self._infer_target_from_section(section, normalized)
                if inferred:
                    full_path = self.local_path / inferred
                    target_file = inferred
                if not full_path.exists():
                    print(f"  ⚠️ File not found: {target_file}")
                    continue

            try:
                original = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                print(f"  ⚠️ Cannot read {target_file}: {e}")
                continue

            updated = self._apply_hunks_to_text(original, section.get("hunks", []))

            if updated is None:
                # Fallback: try line-by-line replacement
                updated = self._apply_replacements(original, section.get("removed", []), section.get("added", []))

            if updated and updated != original:
                full_path.write_text(updated, encoding="utf-8")
                print(f"  ✅ Modified: {target_file}")
                files_modified += 1
            else:
                print(f"  ⚠️ No changes applied to: {target_file}")

        if files_modified > 0:
            print(f"✅ Direct modification complete: {files_modified} file(s) modified.")
            self.last_patch_error = ""
            return True

        print("❌ Direct file modification could not apply any changes.")
        return False

    def _infer_target_from_section(self, section: dict, patch_text: str) -> Optional[str]:
        """Infer a target file from section content, then fallback to generic heuristics."""
        inferred = self._infer_patch_target_file(patch_text)
        if inferred:
            return inferred

        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.local_path),
                capture_output=True,
                text=True,
            )
            files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            files = []

        if not files:
            return None

        probe_lines = [line.strip() for line in section.get("removed", []) if line.strip()]
        if not probe_lines:
            for hunk in section.get("hunks", []):
                for line in hunk.get("lines", []):
                    if line.startswith(" "):
                        candidate = line[1:].strip()
                        if candidate:
                            probe_lines.append(candidate)

        if not probe_lines:
            return None

        best_file = None
        best_score = 0

        for rel in files:
            full = self.local_path / rel
            if not full.exists() or full.is_dir():
                continue

            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            score = 0
            for probe in probe_lines[:20]:
                if probe and probe in text:
                    score += 1

            if score > best_score:
                best_score = score
                best_file = rel

        return best_file if best_score > 0 else None

    def _parse_patch_sections(self, patch_text: str) -> list[dict]:
        """Parse a unified diff into per-file sections with hunks."""
        sections = []
        current = None
        current_hunk = None

        for line in patch_text.split("\n"):
            if line.startswith("diff --git "):
                if current:
                    if current_hunk:
                        current.setdefault("hunks", []).append(current_hunk)
                    sections.append(current)
                current = {"target": None, "hunks": [], "removed": [], "added": []}
                current_hunk = None
                continue

            if line.startswith("+++ "):
                path = line[4:].strip()
                path = re.sub(r"^b/", "", path)
                if current is None:
                    current = {"target": None, "hunks": [], "removed": [], "added": []}
                current["target"] = path
                continue

            if line.startswith("--- "):
                if current is None:
                    current = {"target": None, "hunks": [], "removed": [], "added": []}
                continue

            if line.startswith("@@ "):
                if current_hunk:
                    current.setdefault("hunks", []).append(current_hunk)
                current_hunk = {"header": line, "lines": []}
                continue

            if current_hunk is not None:
                current_hunk["lines"].append(line)

            if current is not None:
                if line.startswith("-") and not line.startswith("--- "):
                    current.setdefault("removed", []).append(line[1:])
                elif line.startswith("+") and not line.startswith("+++ "):
                    current.setdefault("added", []).append(line[1:])

        if current:
            if current_hunk:
                current.setdefault("hunks", []).append(current_hunk)
            sections.append(current)

        return sections

    def _apply_hunks_to_text(self, original: str, hunks: list[dict]) -> Optional[str]:
        """Apply parsed hunks to original text. Returns modified text or None on failure."""
        if not hunks:
            return None

        result = original

        for hunk in hunks:
            # Build the old and new blocks from hunk lines
            old_lines = []
            new_lines = []
            for line in hunk.get("lines", []):
                if line.startswith("-"):
                    old_lines.append(line[1:])
                elif line.startswith("+"):
                    new_lines.append(line[1:])
                elif line.startswith(" ") or not line.startswith(("\\",)):
                    # Context line
                    ctx = line[1:] if line.startswith(" ") else line
                    old_lines.append(ctx)
                    new_lines.append(ctx)

            if not old_lines:
                continue

            old_block = "\n".join(old_lines)
            new_block = "\n".join(new_lines)

            if old_block in result:
                result = result.replace(old_block, new_block, 1)
            else:
                # Try with stripped whitespace matching
                stripped = self._fuzzy_replace(result, old_block, new_block)
                if stripped:
                    result = stripped
                else:
                    return None

        return result if result != original else None

    @staticmethod
    def _fuzzy_replace(text: str, old_block: str, new_block: str) -> Optional[str]:
        """Try to replace old_block in text using fuzzy matching."""
        old_lines = old_block.split("\n")
        text_lines = text.split("\n")

        # Sliding window search
        window_size = len(old_lines)
        best_idx = -1
        best_score = 0.0

        for i in range(len(text_lines) - window_size + 1):
            window = "\n".join(text_lines[i : i + window_size])
            score = difflib.SequenceMatcher(a=old_block.strip(), b=window.strip()).ratio()
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx >= 0 and best_score >= 0.6:
            new_lines = new_block.split("\n")
            result_lines = text_lines[:best_idx] + new_lines + text_lines[best_idx + window_size:]
            return "\n".join(result_lines)

        return None

    def _apply_replacements(self, original: str, removed: list[str], added: list[str]) -> Optional[str]:
        """Apply simple line-by-line replacements."""
        if not removed or not added:
            return None

        result = original

        # Try replacing the entire removed block with the added block
        old_block = "\n".join(removed)
        new_block = "\n".join(added)

        if old_block in result:
            result = result.replace(old_block, new_block, 1)
        elif len(removed) == 1 and len(added) == 1:
            if removed[0] in result:
                result = result.replace(removed[0], added[0], 1)
            else:
                result = self._replace_best_matching_line(result, removed[0], added[0])
                if result is None:
                    return None
        else:
            return None

        return result if result != original else None

    def _normalize_patch_content(self, patch_content: str) -> str:
        """
        Normalize model-generated patch text into a format that git apply accepts.
        """
        raw_content = (patch_content or "").replace("\r\n", "\n").strip()

        # Remove markdown code fences if present.
        content = re.sub(r"^```(?:diff|patch)?\s*", "", raw_content)
        content = re.sub(r"\s*```$", "", content)

        # Keep only the diff-like body when models prepend reasoning text.
        lines = content.split("\n")
        for idx, line in enumerate(lines):
            if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("@@ "):
                content = "\n".join(lines[idx:]).strip()
                break

        # Recover hunk-only patches by inferring the target file and adding headers.
        if "@@ " in content and "--- " not in content and "+++ " not in content:
            target = self._infer_patch_target_file(raw_content)
            if target:
                hunk_lines = content.split("\n")
                for idx, line in enumerate(hunk_lines):
                    if line.startswith("@@ "):
                        content = (
                            f"--- a/{target}\n"
                            f"+++ b/{target}\n"
                            + "\n".join(hunk_lines[idx:]).strip()
                        )
                        break

        # Recover patches that contain file headers but no hunk lines.
        if "--- " in content and "+++ " in content and "@@ " not in content:
            repaired = self._rebuild_patch_without_hunks(content)
            if repaired:
                content = repaired

        lines = content.split("\n")
        normalized: list[str] = []
        in_hunk = False

        for line in lines:
            # Models sometimes indent control lines (e.g. ' @@ ...').
            # git apply rejects these unless normalized.
            if re.match(r"^\s+(diff --git |--- |\+\+\+ |@@ )", line):
                line = line.lstrip()

            if line.startswith("@@ "):
                in_hunk = True
            elif line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
                in_hunk = False

            if line.startswith("--- /") and not line.startswith("--- /dev/null"):
                normalized.append("--- a/" + line[4:].lstrip("/"))
                continue

            if line.startswith("+++ /") and not line.startswith("+++ /dev/null"):
                normalized.append("+++ b/" + line[4:].lstrip("/"))
                continue

            if line.startswith("+++ ") and not line.startswith("+++ b/") and not line.startswith("+++ /dev/null"):
                normalized.append("+++ b/" + line[4:].lstrip("/"))
                continue

            if line.startswith("--- ") and not line.startswith("--- a/") and not line.startswith("--- /dev/null"):
                normalized.append("--- a/" + line[4:].lstrip("/"))
                continue

            # LLM output sometimes emits context lines inside hunks without a leading
            # marker. git apply treats those as corrupt. Convert bare lines to context.
            if in_hunk:
                if line.strip() in {"...", "…"}:
                    # Drop truncation placeholders occasionally emitted by LLMs.
                    continue
                if line == "":
                    normalized.append(" ")
                    continue
                if not line.startswith((" ", "+", "-", "\\")):
                    normalized.append(" " + line)
                    continue

            normalized.append(line)

        return "\n".join(normalized).strip() + "\n"

    @staticmethod
    def _patch_has_effective_changes(patch_text: str) -> bool:
        """Return True when the patch contains at least one real add/remove change."""
        removed = []
        added = []

        for line in (patch_text or "").split("\n"):
            if line.startswith("-") and not line.startswith("--- "):
                removed.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++ "):
                added.append(line[1:])

        if not removed and not added:
            return False

        if removed == added:
            return False

        return True

    def _rebuild_patch_without_hunks(self, patch_text: str) -> Optional[str]:
        """Rebuild a valid unified diff when model output omits @@ hunks."""
        lines = patch_text.split("\n")

        old_path = None
        new_path = None
        removed: list[str] = []
        added: list[str] = []

        for line in lines:
            if line.startswith("--- ") and not line.startswith("--- /dev/null"):
                old_path = line[4:].strip()
            elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
                new_path = line[4:].strip()
            elif line.startswith("-") and not line.startswith("--- "):
                removed.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++ "):
                added.append(line[1:])

        target = (new_path or old_path or "").strip()
        target = re.sub(r"^[ab]/", "", target)
        target = target.lstrip("/")

        if not target or not removed or not added:
            return None

        full_path = self.local_path / target
        if not full_path.exists():
            inferred = self._infer_patch_target_file(patch_text)
            if not inferred:
                return None
            target = inferred
            full_path = self.local_path / target
            if not full_path.exists():
                return None

        try:
            original = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

        old_block = "\n".join(removed)
        new_block = "\n".join(added)

        if old_block not in original:
            # Fall back to single-line replacement when multiline block cannot be found.
            if len(removed) == 1 and len(added) == 1 and removed[0] in original:
                updated = original.replace(removed[0], added[0], 1)
            elif len(removed) == 1 and len(added) == 1:
                updated = self._replace_best_matching_line(original, removed[0], added[0])
                if updated is None:
                    return None
            else:
                return None
        else:
            updated = original.replace(old_block, new_block, 1)

        if updated == original:
            return None

        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{target}",
                tofile=f"b/{target}",
            )
        )
        patch = "".join(diff_lines).strip()
        return patch if patch else None

    @staticmethod
    def _replace_best_matching_line(original: str, old_line: str, new_line: str) -> Optional[str]:
        """Replace the most similar line when exact old-line match is unavailable."""
        src_lines = original.splitlines(keepends=True)
        if not src_lines:
            return None

        best_idx = -1
        best_score = 0.0
        target = (old_line or "").strip()

        for idx, line in enumerate(src_lines):
            score = difflib.SequenceMatcher(a=target, b=line.strip()).ratio()
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx < 0 or best_score < 0.45:
            return None

        replacement = new_line
        if src_lines[best_idx].endswith("\n") and not replacement.endswith("\n"):
            replacement += "\n"
        src_lines[best_idx] = replacement
        return "".join(src_lines)

    def _infer_patch_target_file(self, patch_text: str) -> Optional[str]:
        """Infer a likely target file for malformed hunk-only patches."""
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.local_path),
                capture_output=True,
                text=True,
            )
            files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            files = []

        if not files:
            return None

        lower_to_file = {f.lower(): f for f in files}

        # Prefer file paths explicitly mentioned in model output.
        mentioned = re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)", patch_text or "")
        for candidate in mentioned:
            direct = lower_to_file.get(candidate.lower())
            if direct:
                return direct

            for existing in files:
                if existing.lower().endswith(candidate.lower()):
                    return existing

        if len(files) == 1:
            return files[0]

        source_like = [
            f for f in files
            if Path(f).suffix.lower() in {
                ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cs", ".md"
            }
        ]
        if len(source_like) == 1:
            return source_like[0]

        return None

    # ── Commit & Push ─────────────────────────────────────────────────────

    async def has_changes(self) -> bool:
        """Check if there are any uncommitted changes in the working tree."""
        try:
            result = await self._run_git(
                ["git", "status", "--porcelain"],
                cwd=self.local_path,
                capture_output=True,
            )
            return bool(result.strip())
        except Exception:
            return False

    async def get_current_diff(self) -> str:
        """Return the current diff of all changes (staged + unstaged)."""
        try:
            # Stage everything first to capture all changes
            await self._run_git(["git", "add", "-A"], cwd=self.local_path)
            result = await self._run_git(
                ["git", "diff", "--cached"],
                cwd=self.local_path,
                capture_output=True,
            )
            return result
        except Exception:
            return ""

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
        
        # Use authenticated URL with token embedded for push
        authenticated_url = (
            f"https://x-access-token:{self.token}@github.com/{self.owner}/{self.name}.git"
        )
        
        await self._run_git(
            ["git", "push", authenticated_url, branch_name, "--force"],
            cwd=self.local_path,
        )
        print("✅ Push complete.")

    @staticmethod
    def _redact_sensitive_text(value: str) -> str:
        """Redact tokens in command strings and error output."""
        if not value:
            return value

        redacted = value
        redacted = re.sub(
            r"https://x-access-token:[^@\s]+@github\.com",
            "https://x-access-token:***@github.com",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_***", redacted)
        return redacted

    # ── Utilities ─────────────────────────────────────────────────────────

    async def get_all_files(self) -> list[str]:
        """
        Returns a list of all file paths tracked by git in the repository,
        relative to the repo root.
        """
        try:
            result = await self._run_git(
                ["git", "ls-files"],
                cwd=self.local_path,
                capture_output=True,
            )
            files = result.strip().splitlines()
            return [f.strip() for f in files if f.strip()]
        except Exception:
            return []

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

    async def _detect_default_branch(self) -> str:
        """Detect origin's default branch, falling back to main/master."""
        try:
            head_ref = await self._run_git(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=self.local_path,
                capture_output=True,
            )
            # refs/remotes/origin/main -> main
            branch = head_ref.rsplit("/", 1)[-1].strip()
            if branch:
                return branch
        except subprocess.CalledProcessError:
            pass

        for candidate in ("main", "master"):
            try:
                await self._run_git(
                    ["git", "rev-parse", "--verify", f"origin/{candidate}"],
                    cwd=self.local_path,
                )
                return candidate
            except subprocess.CalledProcessError:
                continue

        return "main"

    # ── Internal ──────────────────────────────────────────────────────────

    @staticmethod
    async def _run_git(
        cmd: list[str],
        cwd: Optional[Path] = None,
        capture_output: bool = False,
    ) -> str:
        """Run a git command asynchronously."""
        def run_cmd() -> str:
            result = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                safe_cmd = [GitManager._redact_sensitive_text(part) for part in cmd]
                safe_output = GitManager._redact_sensitive_text(result.stdout or "")
                safe_stderr = GitManager._redact_sensitive_text(result.stderr or "")
                raise subprocess.CalledProcessError(
                    result.returncode,
                    safe_cmd,
                    output=safe_output,
                    stderr=safe_stderr,
                )

            return result.stdout.strip() if capture_output else ""

        return await asyncio.to_thread(run_cmd)