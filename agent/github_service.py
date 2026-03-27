"""
GitHub Service
Handles all GitHub API interactions: issues, pull requests, etc.
"""

import httpx
from typing import Optional, List
from config import get_settings
from models import GitHubIssue

settings = get_settings()


class GitHubService:
    """
    GitHub API client for PatchPilot.
    Handles issue fetching, PR creation, and other GitHub operations.
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.GITHUB_TOKEN
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    async def get_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> GitHubIssue:
        """
        Fetch a GitHub issue by number.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            
        Returns:
            GitHubIssue object
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            return GitHubIssue(
                number=data["number"],
                title=data["title"],
                body=data.get("body", ""),
                html_url=data["html_url"],
                state=data["state"],
                author=data["user"]["login"] if data.get("user") else None,
                created_at=data.get("created_at")
            )

    async def list_issues(
        self, owner: str, repo: str, state: str = "open", limit: int = 20
    ) -> List[GitHubIssue]:
        """
        List issues for a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            state: Issue state (open, closed, all)
            limit: Maximum number of issues to return
            
        Returns:
            List of GitHubIssue objects
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/issues"
        params = {"state": state, "per_page": limit}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            return [
                GitHubIssue(
                    number=item["number"],
                    title=item["title"],
                    body=item.get("body", ""),
                    html_url=item["html_url"],
                    state=item["state"],
                    author=item["user"]["login"] if item.get("user") else None,
                    created_at=item.get("created_at")
                )
                for item in data
                if "pull_request" not in item  # Filter out PRs
            ]

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main"
    ) -> str:
        """
        Create a pull request.
        
        Args:
            owner: Repository owner
            repo: Repository name
            title: PR title
            body: PR description
            head_branch: Branch to merge from
            base_branch: Branch to merge into (default: main)
            
        Returns:
            URL of the created pull request
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            return data["html_url"]

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str = "main"
    ) -> Optional[str]:
        """
        Get the content of a file from a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path in the repository
            ref: Git reference (branch, tag, or commit)
            
        Returns:
            File content as string, or None if not found
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": ref}
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                # GitHub returns base64-encoded content
                import base64
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content
        except Exception:
            return None
