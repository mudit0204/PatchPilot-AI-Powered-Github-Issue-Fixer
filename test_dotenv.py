from dotenv import load_dotenv
import os

load_dotenv()
token = os.getenv("GITHUB_TOKEN")
print(f"Token length: {len(token) if token else 0}")
print(f"Token prefix: {token[:20] if token else 'None'}")
print(f"Token suffix: ...{token[-15:] if token else 'None'}")
