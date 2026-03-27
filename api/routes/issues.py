"""
GitHub Issues Route
"""

from fastapi import APIRouter, HTTPException
from typing import Optional
from agent.github_service import GitHubService
from config import get_settings

router = APIRouter()
settings = get_settings()


@router.get("/{owner}/{repo}")
async def list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 20
):
    """
    List issues for a GitHub repository.
    
    Args:
        owner: Repository owner
        repo: Repository name
        state: Issue state (open, closed, all)
        limit: Maximum number of issues to return
    """
    try:
        github = GitHubService(settings.GITHUB_TOKEN)
        issues = await github.list_issues(owner, repo, state=state, limit=limit)
        
        return {
            "issues": issues,
            "count": len(issues),
            "repository": f"{owner}/{repo}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{owner}/{repo}/{issue_number}")
async def get_issue(owner: str, repo: str, issue_number: int):
    """
    Get details of a specific GitHub issue.
    
    Args:
        owner: Repository owner
        repo: Repository name
        issue_number: Issue number
    """
    try:
        github = GitHubService(settings.GITHUB_TOKEN)
        issue = await github.get_issue(owner, repo, issue_number)
        return issue
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Issue not found: {str(e)}")
