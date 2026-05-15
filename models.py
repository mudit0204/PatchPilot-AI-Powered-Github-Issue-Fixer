"""
PatchPilot Data Models
"""

from pydantic import BaseModel
from enum import Enum
from typing import Optional
from datetime import datetime


class StepType(str, Enum):
    """Types of steps in agent execution"""
    THOUGHT = "thought"
    ACTION = "action"
    RESULT = "result"
    PATCH = "patch"
    COMMIT = "commit"
    ERROR = "error"


class AgentStatus(str, Enum):
    """Status of agent run"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class GitHubIssue(BaseModel):
    """GitHub Issue representation"""
    number: int
    title: str
    body: Optional[str] = None
    html_url: str
    state: str = "open"
    author: Optional[str] = None
    created_at: Optional[datetime] = None


class AgentStep(BaseModel):
    """A single step in the agent's execution"""
    step_type: StepType
    content: str
    metadata: Optional[dict] = None
    timestamp: datetime = None
    
    def __init__(self, **data):
        if 'timestamp' not in data:
            data['timestamp'] = datetime.utcnow()
        super().__init__(**data)


class AgentRunRequest(BaseModel):
    """Request to run the PatchPilot agent on an issue"""
    repo_owner: str
    repo_name: str
    issue_number: int
    branch_name: Optional[str] = None
    dry_run: bool = False


class AgentRunResult(BaseModel):
    """Result of an agent run"""
    status: AgentStatus
    steps: list[AgentStep]
    pr_url: Optional[str] = None
    commit_sha: Optional[str] = None
    error: Optional[str] = None
