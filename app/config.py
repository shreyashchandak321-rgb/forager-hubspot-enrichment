"""Application settings — everything sensitive comes from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    forager_api_key: str = ""
    forager_account_id: str = ""          # from app.forager.ai/profile
    forager_base_url: str = "https://api-v2.forager.ai"
    hubspot_access_token: str = ""
    # Job-title keywords allowed through the contact-discovery filter.
    title_filter: str = "CEO,CTO,Chief,Founder,VP,Vice President,Director,Head of,Manager,Lead"
    webhook_secret: str = ""

    @property
    def title_keywords(self) -> list[str]:
        return [t.strip().lower() for t in self.title_filter.split(",") if t.strip()]


settings = Settings()
