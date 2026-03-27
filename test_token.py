from config import Settings

# Force reload without cache
settings = Settings()
print(f"GITHUB_TOKEN length: {len(settings.GITHUB_TOKEN)}")
print(f"Token prefix: {settings.GITHUB_TOKEN[:20]}")
print(f"Token ends with: ...{settings.GITHUB_TOKEN[-10:]}")
print(f"Is valid: {settings.GITHUB_TOKEN.startswith('github_pat_')}")
