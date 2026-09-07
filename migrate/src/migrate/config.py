"""Settings for the one-shot legacy importer.

Reuses ``shelf.config.settings`` for the *destination* (Postgres + the
``shelf`` bucket) so we don't duplicate connection details. The
``MIGRATE_*`` env vars cover the *source* — legacy MariaDB and the
``zotero`` Garage bucket.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrateSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MIGRATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Legacy MariaDB. Connect to ``zotero_master`` — the importer
    # switches to ``zotero_shard_<n>`` per-library as needed.
    legacy_db_host: str = "127.0.0.1"
    legacy_db_port: int = 3306
    legacy_db_user: str = "root"
    legacy_db_password: str = ""
    legacy_db_name: str = "zotero_master"

    # Legacy user to import. There is exactly one in the personal
    # stack, so the default just picks user 1; override if needed.
    legacy_user_id: int = 1

    # Legacy Garage S3 (the ``zotero`` bucket).
    legacy_s3_endpoint: str = ""
    legacy_s3_region: str = "us-east-1"
    legacy_s3_bucket: str = "zotero"
    legacy_s3_access_key_id: str = ""
    legacy_s3_secret_access_key: str = ""

    # Target user resolution in Shelf. Either an OIDC identity
    # ``<idp>:<sub>`` or an email — whichever is set. If both are
    # empty and exactly one user exists, that user is picked.
    target_identity: str = ""
    target_email: str = ""


settings = MigrateSettings()
