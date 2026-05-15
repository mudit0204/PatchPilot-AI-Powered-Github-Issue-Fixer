"""
PatchPilot Configuration
Environment variables and app settings
"""

import os
from pathlib import Path
from pydantic import model_validator
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
    LLM_MODEL: str = "openhands/gemini-2.0-flash"
    
    # OpenHands Configuration
    OPENHANDS_ENABLED: bool = False  # Set True when Docker + OpenHands is available
    OPENHANDS_IMAGE: str = "ghcr.io/all-hands-ai/openhands:main"
    OPENHANDS_PORT: int = 3000
    OPENHANDS_WORKSPACE: str = "/workspace"
    PATCHPILOT_HOST_ROOT: str = ""
    
    # Repository Management
    REPO_CLONE_DIR: str = "./repos"
    PATCH_OUTPUT_DIR: str = "./patches"
    
    # Gemini Model
    GEMINI_MODEL: str = "gemini-2.0-flash-exp"

    @model_validator(mode="after")
    def sync_model_fields(self):
        """Keep `LLM_MODEL` and `GEMINI_MODEL` compatible across old/new config styles.
        When LLM_PROVIDER is 'ollama', enforce Ollama-only mode."""
        
        provider = (self.LLM_PROVIDER or "").strip().lower()
        
        # If Ollama mode is explicitly set, ensure we don't fall back to Gemini
        if provider == "ollama":
            self.GEMINI_API_KEY = ""  # Disable Gemini fallback
            self.LLM_MODEL = f"ollama/{self.OLLAMA_MODEL}"
            return self
        
        llm_model = (self.LLM_MODEL or "").strip()
        gemini_model = (self.GEMINI_MODEL or "").strip()

        if llm_model.startswith("openhands/"):
            provider_model = llm_model.split("/", 1)[1]
            if provider_model.startswith("gemini"):
                self.GEMINI_MODEL = provider_model
        elif not llm_model and gemini_model:
            self.LLM_MODEL = f"openhands/{gemini_model}"

        return self

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
