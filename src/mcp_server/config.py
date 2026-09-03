from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"

    valkey_url: str = "redis://:changeme@valkey:6379/1"
    postgres_dsn: str = "postgresql+asyncpg://ebm_user:changeme@postgres:5432/ebm_db"

    mcp_secret: str = "changeme_mcp_secret"
    mcp_port: int = 8001
    log_level: str = "INFO"

    # Für analyze_clinical_text_for_ebm Tool
    app_internal_url: str = "http://app:8000"
    internal_api_key: str = "changeme_internal_ebm_key_min32"


@lru_cache
def get_mcp_settings() -> MCPSettings:
    return MCPSettings()
