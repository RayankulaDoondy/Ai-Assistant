#!/usr/bin/env python
"""
Jarvis Installation Verifier
Run this to check if everything is properly installed
"""

import sys
import subprocess
import shutil
from pathlib import Path

def check_python():
    """Check Python version"""
    version = sys.version_info
    if version.major == 3 and version.minor >= 10:
        print(f"✓ Python {version.major}.{version.minor} installed")
        return True
    else:
        print(f"✗ Python 3.10+ required (found {version.major}.{version.minor})")
        return False

def check_package(package_name):
    """Check if a package is installed"""
    try:
        __import__(package_name)
        print(f"✓ {package_name} installed")
        return True
    except ImportError:
        print(f"✗ {package_name} NOT installed")
        return False

def check_ollama():
    """Check if Ollama is accessible"""
    if shutil.which("ollama") is None:
        print("✗ Ollama CLI is not installed or not on PATH")
        print("  Install Ollama and add it to PATH, then start it with: ollama serve")
        return False

    try:
        import requests
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        if response.status_code == 200:
            print("✓ Ollama is running")
            return True
        else:
            print("✗ Ollama not responding properly")
            print("  Start with: ollama serve")
            return False
    except Exception:
        print("✗ Ollama is not running (or not accessible at http://localhost:11434)")
        print("  Start with: ollama serve")
        return False

def check_directories():
    """Check if required directories exist"""
    dirs = ["data", "logs", "config"]
    all_ok = True
    
    for d in dirs:
        path = Path(d)
        if path.exists():
            print(f"✓ {d}/ exists")
        else:
            print(f"✗ {d}/ missing (creating...)")
            path.mkdir(exist_ok=True)
            all_ok = False
    
    return all_ok

def main():
    print("\n" + "="*50)
    print("Jarvis Installation Verifier")
    print("="*50 + "\n")
    
    checks = [
        ("Python Version", check_python),
        ("Key Packages", lambda: all([
            check_package("fastapi"),
            check_package("langchain"),
            check_package("chromadb"),
            check_package("whisper"),
        ])),
        ("Directories", check_directories),
        ("Ollama Server", check_ollama),
    ]
    
    results = []
    for check_name, check_func in checks:
        print(f"\n{check_name}:")
        try:
            results.append(check_func())
        except Exception as e:
            print(f"✗ Error during check: {e}")
            results.append(False)
    
    print("\n" + "="*50)
    if all(results):
        print("✓ All checks passed! Ready to run Jarvis")
        print("\nStart with:")
        print("  python cli.py")
    else:
        print("✗ Some checks failed. See above for details")
        print("\nFix issues then run this script again")
    
    print("="*50 + "\n")
    
    return 0 if all(results) else 1

if __name__ == "__main__":
    sys.exit(main())
