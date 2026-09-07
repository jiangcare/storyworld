"""全局配置：从 .env / 环境变量读取。"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "StoryWorld"
    debug: bool = False

    # Telegram
    telegram_bot_token: str = ""
    telegram_proxy: str = ""

    # DeepSeek (OpenAI 兼容)
    ai_backend: Literal["harness", "direct"] = "harness"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.85
    llm_max_tokens: int = 1600
    llm_timeout: float = 90.0
    harness_work_dir: str = "data/harness"

    # 相对路径始终以项目根目录为基准，独立启动 Web/Bot/后台也共享同一存档。
    database_url: str = "sqlite:///data/storyworld.db"

    # 服务监听地址（0.0.0.0 = 局域网可访问；127.0.0.1 = 仅本机）
    bind_host: str = "0.0.0.0"

    # 后台管理
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # 游戏节奏
    push_hour: int = 20
    push_minute: int = 0
    max_action_points: int = 3
    tick_scan_seconds: int = 60

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
