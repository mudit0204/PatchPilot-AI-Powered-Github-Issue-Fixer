"""
PatchPilot Configuration
Environment variables and app settings
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
    
    # API Keys
    GEMINI_API_KEY: str = ""
    GITHUB_TOKEN: str = ""
    
    # OpenHands Configuration
    OPENHANDS_IMAGE: str = "ghcr.io/all-hands-ai/openhands:main"
    OPENHANDS_PORT: int = 3000
    OPENHANDS_WORKSPACE: str = "/workspace"
    
    # Repository Management
    REPO_CLONE_DIR: str = "./repos"
    PATCH_OUTPUT_DIR: str = "./patches"
    
    # Gemini Model
    GEMINI_MODEL: str = "gemini-2.0-flash-exp"
    
    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
