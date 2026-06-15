from __future__ import annotations

import argparse
import hashlib
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
RELEASE_DIRECTORIES = ["assets", "config", "docs", "prompts", "src"]
RELEASE_FILES = [
    ".env.example",
    "AutoHeadlines.command",
    "启动 AutoHeadlines.command",
    "安装 AutoHeadlines.command",
    "安装本地模型（可选）.command",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
    "requirements.txt",
]
DATA_DIRECTORIES = ["input", "output", "processed", "raw", "reference", "settings"]
EXECUTABLE_FILES = {
    "AutoHeadlines.command",
    "启动 AutoHeadlines.command",
    "安装 AutoHeadlines.command",
    "安装本地模型（可选）.command",
}


def build_release(version: str) -> Path:
    normalized_version = version.removeprefix("v")
    package_name = f"AutoHeadlines-macOS-{normalized_version}"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = DIST_DIR / f"{package_name}.zip"

    with tempfile.TemporaryDirectory(prefix="autoheadlines-release-") as temp:
        package_root = Path(temp) / package_name
        package_root.mkdir()
        for directory in RELEASE_DIRECTORIES:
            shutil.copytree(
                PROJECT_ROOT / directory,
                package_root / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )
        for filename in RELEASE_FILES:
            shutil.copy2(PROJECT_ROOT / filename, package_root / filename)
        for directory in DATA_DIRECTORIES:
            target = package_root / "data" / directory
            target.mkdir(parents=True, exist_ok=True)
            (target / ".gitkeep").write_text("", encoding="utf-8")
        (package_root / "logs").mkdir()
        (package_root / "logs" / ".gitkeep").write_text("", encoding="utf-8")

        for filename in EXECUTABLE_FILES:
            path = package_root / filename
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        _write_zip(package_root, archive_path)

    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (DIST_DIR / "SHA256SUMS.txt").write_text(
        f"{checksum}  {archive_path.name}\n",
        encoding="utf-8",
    )
    return archive_path


def _write_zip(package_root: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_dir():
                continue
            arcname = path.relative_to(package_root.parent)
            info = zipfile.ZipInfo.from_file(path, arcname=str(arcname))
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as file:
                archive.writestr(info, file.read(), compress_type=zipfile.ZIP_DEFLATED)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the macOS AutoHeadlines release.")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    archive = build_release(args.version)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
