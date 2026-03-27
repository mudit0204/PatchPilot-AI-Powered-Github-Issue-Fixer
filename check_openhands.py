"""
OpenHands Health Check Script
Run this to verify OpenHands installation and functionality
"""

import sys
import subprocess
import requests
import docker

def check_docker():
    """Check if Docker is installed and running"""
    print("=" * 60)
    print("1. Checking Docker...")
    print("=" * 60)
    
    try:
        client = docker.from_env()
        print("✅ Docker is running")
        
        # Check Docker version
        version = client.version()
        print(f"   Docker version: {version['Version']}")
        return True
    except Exception as e:
        print(f"❌ Docker is not running or not installed")
        print(f"   Error: {e}")
        print("\n   To fix:")
        print("   1. Install Docker Desktop from: https://www.docker.com/products/docker-desktop")
        print("   2. Start Docker Desktop")
        print("   3. Wait for Docker to fully start (whale icon in system tray)")
        return False


def check_openhands_image():
    """Check if OpenHands image is available"""
    print("\n" + "=" * 60)
    print("2. Checking OpenHands Image...")
    print("=" * 60)
    
    try:
        client = docker.from_env()
        image_name = "ghcr.io/all-hands-ai/openhands:main"
        
        try:
            image = client.images.get(image_name)
            print(f"✅ OpenHands image found")
            print(f"   Image ID: {image.id[:20]}...")
            print(f"   Size: {image.attrs['Size'] / (1024**3):.2f} GB")
            return True
        except docker.errors.ImageNotFound:
            print(f"❌ OpenHands image not found")
            print(f"\n   To pull the image, run:")
            print(f"   docker pull {image_name}")
            return False
            
    except Exception as e:
        print(f"❌ Could not check image: {e}")
        return False


def check_openhands_container():
    """Check if any OpenHands containers are running"""
    print("\n" + "=" * 60)
    print("3. Checking OpenHands Containers...")
    print("=" * 60)
    
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True, filters={"name": "patchpilot-openhands"})
        
        if containers:
            print(f"✅ Found {len(containers)} OpenHands container(s):")
            for container in containers:
                status = "🟢 Running" if container.status == "running" else f"🔴 {container.status}"
                print(f"   - {container.name}: {status}")
            return True
        else:
            print("ℹ️  No OpenHands containers found")
            print("   (This is normal - containers are created when needed)")
            return True
            
    except Exception as e:
        print(f"❌ Could not check containers: {e}")
        return False


def test_openhands_manually():
    """Instructions for manual testing"""
    print("\n" + "=" * 60)
    print("4. Manual OpenHands Test")
    print("=" * 60)
    
    print("""
To test OpenHands manually:

Option A - Quick Test (via PatchPilot API):
    1. Make sure PatchPilot backend is running:
       uvicorn main:app --reload --port 8000
    
    2. Open browser to: http://localhost:8000/docs
    
    3. Try the /api/agent/run endpoint with test data:
       {
         "repo_owner": "test",
         "repo_name": "test",
         "issue_number": 1,
         "dry_run": true
       }

Option B - Direct OpenHands Test:
    1. Run OpenHands container:
       docker run -it --rm -p 3000:3000 ghcr.io/all-hands-ai/openhands:main
    
    2. Open browser to: http://localhost:3000
    
    3. You should see OpenHands UI
    
    4. Press Ctrl+C to stop when done
""")


def check_patchpilot_backend():
    """Check if PatchPilot backend is running"""
    print("\n" + "=" * 60)
    print("5. Checking PatchPilot Backend...")
    print("=" * 60)
    
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        if response.status_code == 200:
            print("✅ PatchPilot backend is running")
            data = response.json()
            print(f"   Status: {data.get('status')}")
            print(f"   Service: {data.get('service')}")
            print(f"   Docs: http://localhost:8000/docs")
            return True
        else:
            print(f"⚠️  PatchPilot backend responded with status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ PatchPilot backend is not running")
        print("\n   To start:")
        print("   uvicorn main:app --reload --port 8000")
        return False
    except Exception as e:
        print(f"❌ Could not reach backend: {e}")
        return False


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║         PatchPilot / OpenHands Health Check              ║
╚══════════════════════════════════════════════════════════╝
""")
    
    results = {
        "docker": check_docker(),
        "image": check_openhands_image(),
        "container": check_openhands_container(),
        "backend": check_patchpilot_backend(),
    }
    
    test_openhands_manually()
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = all(results.values())
    
    if all_passed:
        print("✅ All checks passed! OpenHands is ready to use.")
    else:
        print("⚠️  Some checks failed. Review the output above for fixes.")
        
    print("\nStatus:")
    for check, passed in results.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {check.capitalize()}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
