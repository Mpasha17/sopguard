"""Assemble and upload the Hugging Face Space: python space/deploy.py <user>/<space-name>

Needs HF_TOKEN (write) in the environment or a prior `hf auth login`. Uploads only what the demo needs.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    repo_id = sys.argv[1]
    api = HfApi()
    api.create_repo(repo_id, repo_type="space", space_sdk="gradio", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copy(ROOT / "app.py", t / "app.py")
        shutil.copy(ROOT / "space" / "README.md", t / "README.md")
        shutil.copy(ROOT / "space" / "requirements.txt", t / "requirements.txt")
        shutil.copytree(ROOT / "src" / "sopguard", t / "src" / "sopguard", ignore=shutil.ignore_patterns("__pycache__"))
        (t / "space").mkdir()
        if (ROOT / "space" / "replays.json").exists():
            shutil.copy(ROOT / "space" / "replays.json", t / "space" / "replays.json")
        (t / "docs").mkdir()
        if (ROOT / "docs" / "RESULTS.md").exists():
            shutil.copy(ROOT / "docs" / "RESULTS.md", t / "docs" / "RESULTS.md")
        api.upload_folder(folder_path=str(t), repo_id=repo_id, repo_type="space", commit_message="Deploy SOPGuard demo")
    print(f"https://huggingface.co/spaces/{repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
