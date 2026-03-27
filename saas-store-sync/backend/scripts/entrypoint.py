#!/usr/bin/env python3
"""Run migrations then exec the container CMD. Avoids shell script CRLF issues on Windows."""
import subprocess
import sys

if __name__ == "__main__":
    print("Running migrations...")
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
    print("Starting", sys.argv[1:])
    os = __import__("os")
    os.execvp(sys.argv[1], sys.argv[1:])
