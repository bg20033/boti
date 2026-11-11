"""Create a Windows source bundle without runtime data or credentials."""

import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "boti-windows.zip"
destination.parent.mkdir(exist_ok=True)
files = [root / name for name in ("README.md", "pyproject.toml", "uv.lock", ".python-version", ".gitignore")]
files += [
    path
    for path in (root / "boti").rglob("*")
    if path.is_file() and path.suffix in {".py", ".html", ".css", ".js"}
]
files += list((root / "scripts").glob("*.ps1"))
files += list((root / "scripts").glob("*.py"))
files += list((root / "tests").glob("*.py"))
files += list((root / "docs").glob("*.md"))
files += list((root / ".github/workflows").glob("*.yml"))
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        relative = path.relative_to(root)
        assert "secrets" not in relative.parts and "data" not in relative.parts
        archive.write(path, Path("boti-studio") / relative)
print(
    f"Created {destination.name}: {len(files)} source files, {destination.stat().st_size:,} bytes. No runtime data."
)
