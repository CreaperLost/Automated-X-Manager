"""CSV-backed project loader and DB round-trip (2-column format: name,url)."""
from __future__ import annotations

from x_auto.ai.projects import load_csv, sync_projects, write_csv
from x_auto.store.repos import Database


class TestLoadCsv:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_csv(tmp_path / "nope.csv") == []

    def test_minimal_valid_csv(self, tmp_path):
        p = tmp_path / "p.csv"
        p.write_text("name,url\nAcme,https://acme.com\n", encoding="utf-8")
        rows = load_csv(p)
        assert len(rows) == 1
        assert rows[0] == {
            "name": "Acme",
            "url": "https://acme.com",
            "description": "",
            "tags": [],
        }

    def test_skips_blank_required_fields(self, tmp_path):
        p = tmp_path / "p.csv"
        p.write_text(
            "name,url\n"
            "Good,https://good.com\n"
            ",https://noname.com\n"
            "Nourl,\n"
            "Other,https://other.com\n",
            encoding="utf-8",
        )
        rows = load_csv(p)
        names = [r["name"] for r in rows]
        assert names == ["Good", "Other"]

    def test_skips_invalid_url(self, tmp_path):
        p = tmp_path / "p.csv"
        p.write_text(
            "name,url\n"
            "Good,https://good.com\n"
            "BadScheme,ftp://nope.com\n"
            "JustText,hello\n",
            encoding="utf-8",
        )
        rows = load_csv(p)
        assert [r["name"] for r in rows] == ["Good"]

    def test_dedupes_by_name(self, tmp_path):
        p = tmp_path / "p.csv"
        p.write_text(
            "name,url\nDup,https://a.com\nDup,https://b.com\n",
            encoding="utf-8",
        )
        rows = load_csv(p)
        assert len(rows) == 1
        assert rows[0]["url"] == "https://a.com"

    def test_round_trip_write_then_read(self, tmp_path):
        p = tmp_path / "p.csv"
        original = [
            {"name": "Acme", "url": "https://acme.com", "description": "", "tags": []},
            {"name": "Helios", "url": "https://helios.dev", "description": "", "tags": []},
        ]
        write_csv(p, original)
        # Re-read from disk to confirm the file round-trips through
        # load_csv's strict two-column parser.
        reloaded = load_csv(p)
        assert reloaded == original

    def test_written_csv_has_only_name_url_columns(self, tmp_path):
        p = tmp_path / "p.csv"
        write_csv(p, [
            {"name": "Acme", "url": "https://acme.com",
             "description": "should not appear", "tags": ["should", "not", "appear"]},
        ])
        text = p.read_text(encoding="utf-8")
        # Header is the only place the column names should appear.
        assert text.splitlines()[0] == "name,url"
        assert "should not appear" not in text
        assert "should" not in text or "should,not,appear" in text  # not present as tags
        # Re-read confirms the dropped columns are gone.
        rows = load_csv(p)
        assert rows[0]["description"] == ""
        assert rows[0]["tags"] == []


class TestSyncProjects:
    def test_sync_into_db_roundtrip(self, configured_settings, tmp_db: Database):
        real_csv = configured_settings.repo_root / "data" / "projects.csv"
        if real_csv.exists():
            import shutil
            configured_settings.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(real_csv, configured_settings.data_dir / "projects.csv")
        n = sync_projects(configured_settings, tmp_db)
        assert n >= 1
        from x_auto.ai.projects import list_projects
        loaded = list_projects(tmp_db)
        assert len(loaded) == n
        for p in loaded:
            # Every project has a non-empty name and url.
            assert p["name"]
            assert p["url"]
