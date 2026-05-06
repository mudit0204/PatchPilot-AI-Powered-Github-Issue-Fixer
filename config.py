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

    # LLM provider: ollama | gemini
    LLM_PROVIDER: str = "ollama"
    
    # OpenHands Configuration
    OPENHANDS_ENABLED: bool = False  # Set True when Docker + OpenHands is available
    OPENHANDS_IMAGE: str = "ghcr.io/all-hands-ai/openhands:main"
    OPENHANDS_PORT: int = 3000
    OPENHANDS_WORKSPACE: str = "/workspace"
    
    # Repository Management
    REPO_CLONE_DIR: str = "./repos"
    PATCH_OUTPUT_DIR: str = "./patches"
    
    # Gemini Model
    GEMINI_MODEL: str = "gemini-2.0-flash-exp"

    # Ollama Configuration
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_BASE_URL_DOCKER: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "llama3.2:latest"
    OLLAMA_TIMEOUT_SEC: int = 180
    
    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
