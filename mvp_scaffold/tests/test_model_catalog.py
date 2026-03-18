from pathlib import Path

from src.model_catalog import ModelCatalogService


def test_model_catalog_reads_codex_cache_slugs(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "models_cache.json").write_text(
        """
        {
          "fetched_at": "2026-03-18T03:00:00Z",
          "models": [
            {"slug": "gpt-5.4"},
            {"slug": "gpt-5.1-codex-mini"},
            {"slug": "gpt-5.4"}
          ]
        }
        """,
        encoding="utf-8",
    )
    service = ModelCatalogService(codex_home=codex_home, fallback_models=("gpt-5", "o3"))

    assert service.list_models() == ["gpt-5.4", "gpt-5.1-codex-mini"]


def test_model_catalog_falls_back_when_cache_missing(tmp_path: Path) -> None:
    service = ModelCatalogService(codex_home=tmp_path / ".codex", fallback_models=("gpt-5", "o3"))

    assert service.list_models() == ["gpt-5", "o3"]
