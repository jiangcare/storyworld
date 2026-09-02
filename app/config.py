"""全局配置：从 .env / 环境变量读取。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "StoryWorld"
    debug: bool = False

    # Telegram
    telegram_bot_token: str = ""

    # DeepSeek (OpenAI 兼容)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.85
    llm_max_tokens: int = 1600
    llm_timeout: float = 90.0

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "storyworld"
    mysql_password: str = "storyworld123"
    mysql_db: str = "storyworld"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # 后台管理
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # 游戏节奏
    push_hour: int = 20
    push_minute: int = 0
    max_action_points: int = 3
    tick_scan_seconds: int = 60

    @property
    def mysql_dsn(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
