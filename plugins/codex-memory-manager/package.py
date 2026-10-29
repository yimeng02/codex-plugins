"""Create a distributable ZIP without copying user data or caches."""

from pathlib import Path
import zipfile
import json


def main():
    root = Path(__file__).resolve().parent
    manifest = root / "plugin.json"
    if not manifest.is_file():
        manifest = root / ".codex-plugin" / "plugin.json"
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    output = root / "dist" / ("codex-memory-manager-" + version + ".zip")
    output.parent.mkdir(exist_ok=True)
    included = ["mcp.json", ".mcp.json", ".codex-plugin/plugin.json", "README.md", "run.py", "start.sh", "start.cmd", "install.py", "install.cmd", "install.ps1", "install.sh", "doctor.py", "doctor.cmd", "package.py", "CHANGELOG.md", ".gitignore"]
    folders = {"memory_manager": {".py"}, "web": {".html", ".js", ".css"}, "skills": {".md", ".yaml"}, "tests": {".py"}, "docs": {".md"}, "hooks": {".py", ".json"}}
    files = [root / name for name in included]
    for folder, extensions in folders.items():
        files.extend(path for path in (root / folder).rglob("*") if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix in extensions)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, Path(root.name) / path.relative_to(root))
    print(output)


if __name__ == "__main__":
    main()
