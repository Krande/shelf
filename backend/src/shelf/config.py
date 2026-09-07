from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OIDCProvider(BaseModel):
    name: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: list[str] = ["openid", "email", "profile"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHELF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://shelf:shelf@localhost:5432/shelf"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "http://localhost:3900"
    s3_region: str = "us-east-1"
    s3_bucket: str = "shelf"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    gotenberg_url: str = "http://localhost:3000"

    cors_origins: list[str] = ["http://localhost:5173"]

    session_secret_key: str = "dev-secret-do-not-use-in-production"
    session_ttl_seconds: int = 24 * 3600
    session_cookie_name: str = "shelf_session"
    session_cookie_secure: bool = False

    dev_login_enabled: bool = True

    # Public origin for the API, used to construct OIDC redirect URIs.
    public_base_url: str = "http://localhost:8000"

    # Path to the built SPA. Empty / non-existent dir disables SPA hosting
    # (used in dev where vite serves the frontend on a separate port).
    frontend_dir: str = "/app/frontend"

    # JSON-encoded list of OIDC providers, e.g.
    #   SHELF_OIDC_PROVIDERS='[{"name":"authentik","issuer":"...",
    #     "client_id":"...","client_secret":"..."}]'
    oidc_providers: list[OIDCProvider] = Field(default_factory=list)

    # NATS JetStream URL used for the extraction job queue. Empty
    # disables publishing entirely (dev/tests stay clean) and the
    # worker is expected not to be running. The same env var feeds
    # both the API publisher and the worker subscriber so a stale
    # mismatch isn't possible.
    nats_url: str = ""

    # Container image tag, e.g. "sha-143a581". Set by the Dockerfile
    # at build time from DOCKER_IMAGE_TAG (CI sets it to the short SHA).
    # "dev" means a local pixi run rather than a baked image. Surfaced
    # via /api and written into attachment_processing.ocr_engine /
    # .outline_engine so the per-row UI can show which build handled
    # a given run — useful when an OCR job fails and you want to know
    # if the running pod matches the version where the bug was fixed.
    image_tag: str = "dev"


settings = Settings()
