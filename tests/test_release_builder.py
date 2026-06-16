from __future__ import annotations

import zipfile

from scripts import build_release


def test_release_builder_excludes_private_runtime_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(build_release, "DIST_DIR", tmp_path)

    archive = build_release.build_release("test")

    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert any(name.endswith("/安装 XAutoHeadlines.command") for name in names)
        assert any(name.endswith("/安装本地模型（可选）.command") for name in names)
        assert any(name.endswith("/assets/icons/XAutoHeadlines.png") for name in names)
        assert any(name.endswith("/data/settings/.gitkeep") for name in names)
        assert not any(name.endswith("/.env") for name in names)
        assert not any("user_settings.json" in name for name in names)
        assert not any("reference_news.jsonl" in name for name in names)
        assert not any("historical_digest.docx" in name for name in names)
        assert not any("/tests/" in name for name in names)
