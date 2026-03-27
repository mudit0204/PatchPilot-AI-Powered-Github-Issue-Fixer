"""
PatchPilot Agent Orchestrator
The heart of PatchPilot — orchestrates the full issue-to-patch-to-commit pipeline.

Flow:
  GitHub Issue → Clone Repo → OpenHands + Gemini → Patch → Git Commit → Push → PR
"""

import uuid
import asyncio
from datetime import datetime
from typing import AsyncGenerator, Optional

from config import get_settings
from models import (
    AgentRunRequest, AgentRunResult, AgentStep, AgentStatus,
    GitHubIssue, StepType
)
from agent.github_service import GitHubService
from agent.llm_reasoner import GeminiReasoner
from git_manager.git_ops import GitManager
from openhands.runner import OpenHandsRunner

settings = get_settings()


class PatchPilotOrchestrator:
    """
    Coordinates all PatchPilot components for a single issue fix run.
    Streams AgentSteps back to callers in real time.
    """

    def __init__(self):
        self.github   = GitHubService()
        self.reasoner = GeminiReasoner()

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

        def emit(step_type: StepType, content: str, **meta) -> AgentStep:
            s = AgentStep(step_type=step_type, content=content, metadata=meta or None)
            steps.append(s)
            return s

        # ── Step 1: Fetch Issue ───────────────────────────────────────────
        yield emit(StepType.THOUGHT, f"🔍 Fetching issue #{request.issue_number} from {request.repo_owner}/{request.repo_name}...")

        issue: GitHubIssue = await self.github.get_issue(
            request.repo_owner, request.repo_name, request.issue_number
        )

        yield emit(
            StepType.RESULT,
            f"📋 Issue found: **{issue.title}**\n\n{issue.body[:500]}...",
            issue_url=issue.html_url,
        )

        # ── Step 2: Clone Repository ──────────────────────────────────────
        yield emit(StepType.ACTION, f"📦 Cloning repository {request.repo_owner}/{request.repo_name}...")

        git = GitManager(request.repo_owner, request.repo_name, settings.GITHUB_TOKEN)
        repo_path = await git.clone_or_pull()

        branch_name = request.branch_name or f"patchpilot/fix-issue-{issue.number}"
        await git.create_branch(branch_name)

        yield emit(StepType.RESULT, f"✅ Repository cloned. Working on branch: `{branch_name}`")

        # ── Step 3: Build File Context for LLM ───────────────────────────
        yield emit(StepType.THOUGHT, "📂 Scanning repository structure for relevant files...")

        file_tree = await git.get_file_tree(max_files=150)
        # Try to find and read files mentioned in the issue title/body
        relevant_content = await self._gather_relevant_files(issue, git, file_tree)

        yield emit(StepType.RESULT, f"📁 Repository file tree:\n```\n{file_tree[:2000]}\n```")

        # ── Step 4: Run OpenHands Agent ───────────────────────────────────
        yield emit(StepType.THOUGHT, "🤖 Starting OpenHands AI agent...")

        openhands_task = self._build_openhands_task(issue, file_tree)
        runner = OpenHandsRunner()
        patch_content = ""

        try:
            runner.start_container(workspace_path=str(repo_path))

            async for step in runner.run_task(openhands_task, workspace_path=str(repo_path)):
                steps.append(step)
                yield step

                # Capture patch if emitted
                if step.step_type == StepType.PATCH and step.content:
                    patch_content = step.content

        except Exception as e:
            yield emit(StepType.ERROR, f"OpenHands agent error: {e}. Falling back to Gemini-only mode.")
        finally:
            runner.stop_container()

        # ── Step 5: Gemini Fallback / Patch Refinement ───────────────────
        if not patch_content:
            yield emit(StepType.THOUGHT, "🧠 Running Gemini LLM for patch generation...")

            async for step in self.reasoner.analyze_issue(issue, relevant_content):
                steps.append(step)
                yield step

                if step.step_type == StepType.PATCH and step.content:
                    patch_content = step.content

        if not patch_content:
            yield emit(StepType.ERROR, "❌ Could not generate a patch for this issue.")
            return

        yield emit(StepType.PATCH, patch_content)

        # ── Step 6: Apply Patch ───────────────────────────────────────────
        if request.dry_run:
            yield emit(StepType.RESULT, "🔍 Dry run mode — patch generated but not applied.")
            return

        yield emit(StepType.ACTION, "🩹 Applying patch to repository...")
        success = await git.apply_patch(patch_content)

        if not success:
            yield emit(StepType.ERROR, "❌ Patch application failed. Please review the diff manually.")
            return

        yield emit(StepType.RESULT, "✅ Patch applied successfully.")

        # ── Step 7: Commit & Push ─────────────────────────────────────────
        yield emit(StepType.ACTION, "💾 Committing fix...")

        commit_message = (
            f"fix: resolve issue #{issue.number} — {issue.title}\n\n"
            f"Automated fix generated by PatchPilot AI agent.\n"
            f"Issue: {issue.html_url}"
        )
        sha = await git.commit(commit_message)

        yield emit(StepType.COMMIT, f"✅ Committed: `{sha[:8]}`\n\nMessage: {commit_message}")

        yield emit(StepType.ACTION, f"🚀 Pushing branch `{branch_name}` to GitHub...")
        await git.push(branch_name)

        # ── Step 8: Open Pull Request ─────────────────────────────────────
        yield emit(StepType.ACTION, "📬 Opening Pull Request...")

        pr_url = await self.github.create_pull_request(
            owner=request.repo_owner,
            repo=request.repo_name,
            title=f"[PatchPilot] Fix for issue #{issue.number}: {issue.title}",
            body=(
                f"## 🤖 Automated fix by PatchPilot\n\n"
                f"This PR was automatically generated to resolve #{issue.number}.\n\n"
                f"### Patch Summary\n```diff\n{patch_content[:2000]}\n```\n\n"
                f"*Please review before merging.*"
            ),
            head_branch=branch_name,
        )

        yield emit(
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
