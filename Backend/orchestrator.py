import uuid
import asyncio
import re
from datetime import datetime
from typing import AsyncGenerator, Optional

from config import get_settings
from models import (
    AgentRunRequest, AgentRunResult, AgentStep, AgentStatus,
    GitHubIssue, StepType
)
from agent.github_service import GitHubService
from agent.llm_reasoner import create_reasoner
from git_manager.git_ops import GitManager
from openhands.runner import OpenHandsRunner

settings = get_settings()
OPENHANDS_HARD_DISABLED = True


class PatchPilotOrchestrator:
    """
    Coordinates all PatchPilot components for a single issue fix run.
    Streams AgentStep objects back to callers in real time.
    """

    def __init__(self):
        self.github   = GitHubService()
        self.reasoner = create_reasoner()
        self.emit = None # Will be set in run()

    async def run(
        self, request: AgentRunRequest
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Execute the full PatchPilot pipeline for an issue.
        Yields AgentStep objects as progress is made — callers can stream
        these directly to the frontend via SSE.
        """
        run_id = uuid.uuid4().hex[:8]
        steps: list[AgentStep] = []

        def emit_local(step_type: StepType, content: str, **meta) -> AgentStep:
            s = AgentStep(step_type=step_type, content=content, metadata=meta or None)
            steps.append(s)
            return s
        self.emit = emit_local

        # ── Step 1: Fetch Issue ───────────────────────────────────────────
        yield self.emit(StepType.THOUGHT, f"🔍 Fetching issue #{request.issue_number} from {request.repo_owner}/{request.repo_name}...")

        issue: GitHubIssue = await self.github.get_issue(
            request.repo_owner, request.repo_name, request.issue_number
        )

        yield self.emit(
            StepType.RESULT,
            f"📋 Issue found: **{issue.title}**\n\n{issue.body[:500]}...",
            issue_url=issue.html_url,
        )

        # ── Step 2: Clone Repository ──────────────────────────────────────
        yield self.emit(StepType.ACTION, f"📦 Cloning repository {request.repo_owner}/{request.repo_name}...")

        git = GitManager(request.repo_owner, request.repo_name, settings.GITHUB_TOKEN)
        repo_path = await git.clone_or_pull()

        branch_name = request.branch_name or f"patchpilot/fix-issue-{issue.number}"
        await git.create_branch(branch_name)

        yield self.emit(StepType.RESULT, f"✅ Repository cloned. Working on branch: `{branch_name}`")

        # ── Step 3: Build File Context for LLM ───────────────────────────
        yield self.emit(StepType.THOUGHT, "📂 Scanning repository structure for relevant files...")

        file_tree = await git.get_file_tree(max_files=150)
        # Try to find and read files mentioned in the issue title/body
        relevant_content = await self._gather_relevant_files(issue, git, file_tree)

        yield self.emit(StepType.RESULT, f"📁 Repository file tree:\n```\n{file_tree[:2000]}\n```")

        # ── Step 4: Run OpenHands Agent (if enabled) ─────────────────────
        patch_content = ""

        use_openhands = settings.OPENHANDS_ENABLED and not OPENHANDS_HARD_DISABLED

        if use_openhands:
            yield self.emit(StepType.THOUGHT, "🤖 Starting OpenHands AI agent...")

            openhands_task = self._build_openhands_task(issue, file_tree)
            runner = OpenHandsRunner()

            try:
                runner.start_container(workspace_path=str(repo_path))

                async for step in runner.run_task(openhands_task, workspace_path=str(repo_path)):
                    steps.append(step)
                    yield step

                    # Capture patch if emitted
                    if step.step_type == StepType.PATCH and step.content:
                        patch_content = step.content

                # After OpenHands finishes, check if files were modified directly
                if not patch_content:
                    has_changes = await git.has_changes()
                    if has_changes:
                        yield self.emit(StepType.THOUGHT, "🔎 OpenHands modified files directly. Capturing diff...")
                        patch_content = await git.get_current_diff()
                        if patch_content:
                            yield self.emit(StepType.PATCH, patch_content)

            except Exception as e:
                yield self.emit(StepType.ERROR, f"OpenHands agent error: {e}. Falling back to LLM mode.")
            finally:
                runner.stop_container()
        else:
            if settings.OPENHANDS_ENABLED and OPENHANDS_HARD_DISABLED:
                yield self.emit(StepType.THOUGHT, "⚙️ OpenHands is force-disabled. Using direct LLM patch generation...")
            else:
                yield self.emit(StepType.THOUGHT, "⚙️ OpenHands is disabled. Using direct LLM patch generation...")

        # ── Step 5: Direct LLM Fallback / Patch Generation ───────────────
        if not patch_content:
            yield self.emit(StepType.THOUGHT, "🧠 Running LLM for patch generation...")
            try:
                async for step in self.reasoner.analyze_issue(issue, relevant_content):
                    steps.append(step)
                    yield step

                    if step.step_type == StepType.PATCH and step.content:
                        patch_content = step.content

                if not patch_content and hasattr(self.reasoner, "generate_patch_only"):
                    yield self.emit(StepType.THOUGHT, "🔁 Trying strict patch-only generation...")
                    patch_content = await self.reasoner.generate_patch_only(issue, relevant_content)
                    if patch_content:
                        yield self.emit(StepType.PATCH, patch_content)
                        yield self.emit(StepType.RESULT, "✅ Recovered patch from patch-only generation.")
            except Exception as e:
                yield self.emit(StepType.ERROR, f"❌ LLM patch generation failed ({type(e).__name__}): {str(e)}")
                return

        if not patch_content:
            yield self.emit(StepType.ERROR, "❌ Could not generate a patch for this issue.")
            return

        # Emit patch if not already emitted
        if not any(s.step_type == StepType.PATCH and s.content == patch_content for s in steps):
            yield self.emit(StepType.PATCH, patch_content)

        # ── Step 6: Apply Patch ───────────────────────────────────────────
        if request.dry_run:
            yield self.emit(StepType.RESULT, "🔍 Dry run mode — patch generated but not applied.")
            return

        # OpenHands can modify files directly; if changes already exist,
        # avoid re-applying the same patch.
        if await git.has_changes():
            yield self.emit(
                StepType.THOUGHT,
                "🧩 Repository already has uncommitted changes. Skipping patch apply step.",
            )
            yield self.emit(StepType.RESULT, "✅ Existing file changes detected and ready to commit.")
        else:
            yield self.emit(StepType.ACTION, "🩹 Applying patch to repository...")
            # Normalize paths in the patch before applying
            patch_content = await self._normalize_patch_paths(patch_content, git)
            success = await git.apply_patch(patch_content)

            if not success:
                # Fallback: try direct file modification
                yield self.emit(StepType.THOUGHT, "🔄 git apply failed. Trying direct file modification...")
                success = await git.apply_patch_direct(patch_content)

            if not success and hasattr(self.reasoner, "generate_patch_only"):
                # Try one strict regeneration pass when model output is malformed/no-op.
                yield self.emit(
                    StepType.THOUGHT,
                    "🔁 Initial patch was invalid or produced no changes. Regenerating a strict unified diff...",
                )
                try:
                    regenerated_patch = await self.reasoner.generate_patch_only(issue, relevant_content)
                except Exception as e:
                    regenerated_patch = ""
                    yield self.emit(StepType.ERROR, f"Strict patch regeneration failed: {type(e).__name__}: {str(e)}")

                if regenerated_patch:
                    patch_content = regenerated_patch
                    yield self.emit(StepType.PATCH, patch_content)

                    if await git.has_changes():
                        success = True
                    else:
                        patch_content = await self._normalize_patch_paths(patch_content, git) # Normalize again after regeneration
                        success = await git.apply_patch(patch_content)
                        if not success:
                            yield self.emit(
                                StepType.THOUGHT,
                                "🔄 Regenerated patch still failed git apply. Trying direct file modification...",
                            )
                            success = await git.apply_patch_direct(patch_content)

            if not success:
                # Last-chance recovery: continue if files changed outside git-apply flow.
                if await git.has_changes():
                    yield self.emit(
                        StepType.THOUGHT,
                        "⚠️ Patch application failed, but repository contains changes. Continuing with commit.",
                    )
                else:
                    details = f" ({git.last_patch_error})" if getattr(git, "last_patch_error", "") else ""
                    yield self.emit(StepType.ERROR, f"❌ Patch application failed. Please review the diff manually.{details}")
                    return

        yield self.emit(StepType.RESULT, "✅ Changes are ready to commit.")

        # Verify files were actually changed
        has_changes = await git.has_changes()
        if not has_changes:
            yield self.emit(StepType.ERROR, "❌ Patch produced no actual changes in the repository.")
            return

        # ── Step 7: Commit & Push ─────────────────────────────────────────
        yield self.emit(StepType.ACTION, "💾 Committing fix...")

        commit_message = (
            f"fix: resolve issue #{issue.number} — {issue.title}\n\n"
            f"Automated fix generated by PatchPilot AI agent.\n"
            f"Issue: {issue.html_url}"
        )
        sha = await git.commit(commit_message)

        yield self.emit(StepType.COMMIT, f"✅ Committed: `{sha[:8]}`\n\nMessage: {commit_message}")

        yield self.emit(StepType.ACTION, f"🚀 Pushing branch `{branch_name}` to GitHub...")
        await git.push(branch_name)

        yield self.emit(StepType.RESULT, f"✅ Branch `{branch_name}` pushed to GitHub.")

        # ── Step 8: Open Pull Request ─────────────────────────────────────
        yield self.emit(StepType.ACTION, "📬 Opening Pull Request...")

        # Get the actual diff for the PR body
        actual_diff = patch_content[:2000]

        pr_url = await self.github.create_pull_request(
            owner=request.repo_owner,
            repo=request.repo_name,
            title=f"[PatchPilot] Fix for issue #{issue.number}: {issue.title}",
            body=(
                f"## 🤖 Automated fix by PatchPilot\n\n"
                f"This PR was automatically generated to resolve #{issue.number}.\n\n"
                f"### Patch Summary\n```diff\n{actual_diff}\n```\n\n"
                f"*Please review before merging.*"
            ),
            head_branch=branch_name,
        )

        yield self.emit(
            StepType.RESULT,
            f"🎉 **Done!** Pull Request opened: {pr_url}",
            pr_url=pr_url,
            commit_sha=sha,
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _gather_relevant_files(
        self, issue: GitHubIssue, git: GitManager, file_tree: str
    ) -> str:
        """
        Heuristically find files relevant to the issue and return their contents
        as a combined string for LLM context.
        """
        import re

        # Extract words from issue that look like file references
        issue_text = f"{issue.title} {issue.body}"
        mentioned = re.findall(r"[\w/]+\.\w{2,4}", issue_text)

        all_files = file_tree.split("\n")
        relevant = []

        for mention in mentioned[:5]:
            for f in all_files:
                if mention in f or f.endswith(mention):
                    content = await git.read_file(f)
                    if content:
                        relevant.append(f"### {f}\n```\n{content[:1500]}\n```")
                    break

        # Always include README if present
        readme = await git.read_file("README.md") or await git.read_file("readme.md")
        if readme:
            relevant.insert(0, f"### README.md\n```\n{readme[:1000]}\n```")

        return "\n\n".join(relevant)

    async def _normalize_patch_paths(self, patch_content: str, git: GitManager) -> str:
        """
        Normalizes file paths within a patch by attempting to map hallucinated paths
        (e.g., LLM-generated paths like 'src/main/java/File.java') to actual file paths
        present in the repository (e.g., 'File.java' or 'path/to/File.java').
        """
        if not patch_content:
            return ""

        normalized_lines = []
        repo_files = await git.get_all_files() # Get all files in the repo
        repo_files_lower = {f.lower(): f for f in repo_files}

        for line in patch_content.splitlines():
            if line.startswith("--- a/") or line.startswith("+++ b/"):
                original_path_in_patch = line[6:].strip()
                # Attempt to find a matching file in the repository
                matched_path = None

                # 1. Direct match (case-insensitive)
                if original_path_in_patch.lower() in repo_files_lower:
                    matched_path = repo_files_lower[original_path_in_patch.lower()]
                else:
                    # 2. Try matching by filename only (e.g., 'HelloWorld.java' -> 'helloworld.java')
                    filename_in_patch = original_path_in_patch.split('/')[-1]
                    for actual_file in repo_files:
                        if actual_file.lower().endswith(filename_in_patch.lower()):
                            matched_path = actual_file
                            break

                if matched_path:
                    # Replace the hallucinated path with the actual path
                    normalized_lines.append(f"{line[:6]}{matched_path}")
                    await self.emit(StepType.THOUGHT, f"Path corrected: {original_path_in_patch} -> {matched_path}")
                else:
                    normalized_lines.append(line) # No match, keep original
            else:
                normalized_lines.append(line)

        return "\n".join(normalized_lines)

    @staticmethod
    def _build_openhands_task(issue: GitHubIssue, file_tree: str) -> str:
        return (
            f"You are an expert software engineer. Fix the following GitHub issue:\n\n"
            f"## Issue #{issue.number}: {issue.title}\n\n"
            f"{issue.body}\n\n"
            f"## Repository Files\n```\n{file_tree[:3000]}\n```\n\n"
            f"Instructions:\n"
            f"1. Read the relevant source files.\n"
            f"2. Understand the root cause of the bug.\n"
            f"3. Apply minimal, precise code changes to fix it.\n"
            f"4. Verify your fix makes sense.\n"
            f"Do NOT commit or push — only edit the files."
        )
