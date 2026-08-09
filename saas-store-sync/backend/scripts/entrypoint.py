#!/usr/bin/env python3
"""Run migrations then exec the container CMD. Avoids shell script CRLF issues on Windows."""
import os
import subprocess
import sys

if __name__ == "__main__":
    print("Running migrations...")
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
    if (os.getenv("PLATFORM_ADMIN_EMAIL") or "").strip() and (os.getenv("PLATFORM_ADMIN_PASSWORD") or ""):
        print("Ensuring platform admin...")
        subprocess.run([sys.executable, "manage.py", "ensure_platform_admin"], check=False)
    print("Starting", sys.argv[1:])
    os.execvp(sys.argv[1], sys.argv[1:])
