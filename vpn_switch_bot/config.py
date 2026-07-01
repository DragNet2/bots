import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    KEENETIC_HOST: str = os.getenv("KEENETIC_HOST", "bredeshok.keenetic.pro")
    KEENETIC_PORT: int = int(os.getenv("KEENETIC_PORT", "443"))
    KEENETIC_USER: str = os.getenv("KEENETIC_USER", "tgbot")
    KEENETIC_PASSWORD: str = os.getenv("KEENETIC_PASSWORD", "")

    VPN_ON_POLICY: str = "VPN_ON"
    VPN_OFF_POLICY: str = "VPN_OFF"

    ALLOWED_USER_IDS: list = field(default_factory=lambda: [233590599])

config = Config()
