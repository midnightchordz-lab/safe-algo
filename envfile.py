"""Load KEY=VALUE lines from .env (repo root) into the environment (if the file exists).

Scheduled jobs don't read ~/.zshrc, so install_mac.py saves the settings they need here.
Variables already set in the environment win.
"""
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load(path=PATH):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def save(values, path=PATH):
    with open(path, "w") as f:
        for key, value in values.items():
            f.write(f'{key}="{value}"\n')
    os.chmod(path, 0o600)
