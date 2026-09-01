import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    database_url: str
    ai_enabled: bool
    ai_api_key: str | None
    ai_api_base_url: str
    ai_model: str


def load_config() -> Config:
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./plants.db"),
        ai_enabled=os.getenv("AI_ENABLED", "false").lower() == "true",
        ai_api_key=os.getenv("AI_API_KEY"),
        ai_api_base_url=os.getenv("AI_API_BASE_URL", "https://api.openai.com/v1"),
        ai_model=os.getenv("AI_MODEL", "gpt-4o-mini"),
    )


config = load_config()
