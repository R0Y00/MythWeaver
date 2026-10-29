from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg://mythweaver@127.0.0.1:55432/mythweaver")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    onebot_token: str = os.getenv("ONEBOT_TOKEN", "")
    bot_id: str = os.getenv("ONEBOT_BOT_ID", "")
    public_origin: str = os.getenv("PUBLIC_ORIGIN", "auto")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")

    @property
    def agent_configured(self):
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    def validate(self):
        if not self.database_url.startswith("postgresql"):
            raise RuntimeError("PostgreSQL is required; no fallback database is used")
        if len(self.admin_password) < 12 or len(self.session_secret) < 32 or len(self.onebot_token) < 32:
            raise RuntimeError("Run scripts/setup.ps1 to create local credentials")
        if not self.bot_id.isdecimal():
            raise RuntimeError("ONEBOT_BOT_ID must identify the permitted NapCat account")

