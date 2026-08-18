from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_cookie_import_migration_is_the_single_head() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(root / "alembic.ini")
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(root / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0019_registration_client_idx"]
    revision = scripts.get_revision("0015_account_cookie_imports")
    assert revision is not None
    assert revision.down_revision == "0014_task_model_index"
