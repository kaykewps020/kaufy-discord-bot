"""Central configuration for the Kaufy Discord Bot."""
import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

class Config:
    # Discord
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
    # SUPER_OWNER — acesso TOTAL a TUDO (eval, shell, shutdown, config, etc.)
    SUPER_OWNER_ID = 1519459793876680844
    # OWNER_IDS — owners normais (adicionados via .setowner), têm muitos poderes
    # mas NÃO os mais críticos (eval, exec, shutdown, reload, maintenance)
    OWNER_IDS = [int(x) for x in os.getenv("OWNER_IDS", "1519459793876680844").split(",") if x]
    GUILD_ID = int(os.getenv("GUILD_ID", "1521670438042730557"))

    # Data paths
    DATA_DIR = BASE_DIR / "data"
    BACKUP_DIR = BASE_DIR / "backups"
    DB_DIR = DATA_DIR / "users"
    KAUFY_CMD = os.getenv("KAUFY_CMD", "opencode")

    # Run mode: "local" (Termux/start.sh) or "github_actions"
    RUN_MODE = os.getenv("KAUFY_RUN_MODE", "local")

    # ── Owner anti-impersonation ──
    # OWNER_IDS = Discord IDs of owners (Discord-verified).
    # OWNER_SECRET = additional second factor. Even a true owner must send
    # this secret once (via .ownerauth) to unlock owner-only powers.
    OWNER_SECRET = os.getenv("OWNER_SECRET", "")

    # ── AI runner ──
    # Per-response timeout for model. 0 / negative = no timeout.
    AI_TIMEOUT = int(os.getenv("KAUFY_AI_TIMEOUT", "0"))
    # Directory where the model writes deliverable files (attached to Discord)
    OUTPUT_DIR = os.getenv("KAUFY_OUTPUT_DIR", "output")

    # Session
    SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "600"))  # 10 min
    MAX_MESSAGES_PER_USER = int(os.getenv("MAX_MESSAGES", "800"))
    RESTART_INTERVAL = int(os.getenv("RESTART_INTERVAL", "18000"))  # 5h

    # Concurrency
    MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "10"))
    MAX_MSG_PER_CHANNEL_PER_MIN = int(os.getenv("MAX_MSG_PER_CHANNEL_PER_MIN", "15"))

    # Channels
    CHANNEL_MSG = "msg"
    CHANNEL_CONFIG = "config"
    CHANNEL_PLANS = "plans"
    CHANNEL_THINKING = "thinking"
    CHANNEL_BOOSTERS = "boosters"

    # Roles (hierarchy: lowest → highest)
    ROLE_PATRON = "Patron"       # Any plan purchased
    ROLE_RICH = "Rich"           # 7d plan bought 2+ times OR gifted 3+ times
    ROLE_BOOSTER = "Booster"     # Server booster

    # Role perks
    ROLE_PERKS = {
        "Patron": {
            "priority_queue": False,       # Normal queue
            "extra_daily": 0,              # No extra messages
            "temp_max": 1.2,               # Max temperature
            "thinking_access": True,       # Access to #thinking
        },
        "Rich": {
            "priority_queue": True,        # Priority in queue
            "extra_daily": 20,             # +20 extra messages/day
            "temp_max": 1.5,               # Higher temperature
            "thinking_access": True,
        },
        "Booster": {
            "priority_queue": True,
            "extra_daily": 50,             # +50 extra messages/day
            "temp_max": 2.0,               # Maximum temperature
            "thinking_access": True,
        },
    }

    # OpenAI / Model
    MODEL = os.getenv("MODEL", "opencode/big-pickle")
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.8"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

    # Plans (prices in USD, crypto only)
    FREE_DAILY_MESSAGES = 10  # Free users get 10 messages per day
    PLANS = {
        "free": {
            "price_usd": 0,
            "duration_days": 0,
            "max_messages": 999999,
            "daily_messages": FREE_DAILY_MESSAGES,
            "thinking": False,
            "context_messages": 10,
            "max_tokens_allowed": 4096,
            "web_search": False,
            "priority_queue": False,
            "file_upload": True,         # Pode enviar arquivos pro AI
            "file_storage": False,       # Sem armazenamento de arquivos
            "custom_prompt": False,      # Sem prompt personalizado
            "export_chat": False,        # Sem exportar conversa
            "api_access": False,         # Sem API access
            "premium_support": False,    # Sem suporte premium
            "early_access": False,       # Sem early access
            "unlimited_daily": False,    # Limite diário
            "no_watermark": False,       # Tem watermark
            "screenshot_gen": True,      # Screenshot generation via AI
            "description": "Free - 10 msgs/day",
            "badge": "FREE",
            "color": "#6b7280",
        },
        "7d": {
            "price_usd": 3.99,
            "duration_days": 7,
            "max_messages": 999999,
            "daily_messages": 999999,
            "thinking": True,
            "context_messages": 50,
            "max_tokens_allowed": 8192,
            "web_search": True,
            "priority_queue": True,
            "file_upload": True,
            "file_storage": True,        # 100MB storage
            "file_storage_mb": 100,
            "custom_prompt": False,
            "export_chat": True,         # Pode exportar conversa
            "api_access": False,
            "premium_support": False,
            "early_access": False,
            "unlimited_daily": True,
            "no_watermark": True,
            "screenshot_gen": True,
            "description": "7 Days • Unlimited",
            "badge": "BASIC",
            "color": "#3b82f6",
        },
        "14d": {
            "price_usd": 6.99,
            "duration_days": 14,
            "max_messages": 999999,
            "daily_messages": 999999,
            "thinking": True,
            "context_messages": 50,
            "max_tokens_allowed": 8192,
            "web_search": True,
            "priority_queue": True,
            "file_upload": True,
            "file_storage": True,
            "file_storage_mb": 100,
            "custom_prompt": True,       # Prompt personalizado
            "export_chat": True,
            "api_access": False,
            "premium_support": False,
            "early_access": False,
            "unlimited_daily": True,
            "no_watermark": True,
            "screenshot_gen": True,
            "description": "14 Days • Unlimited",
            "badge": "STANDARD",
            "color": "#8b5cf6",
        },
        "30d": {
            "price_usd": 9.99,
            "duration_days": 30,
            "max_messages": 999999,
            "daily_messages": 999999,
            "thinking": True,
            "context_messages": 100,
            "max_tokens_allowed": 16384,
            "web_search": True,
            "priority_queue": True,
            "file_upload": True,
            "file_storage": True,
            "file_storage_mb": 500,
            "custom_prompt": True,
            "export_chat": True,
            "api_access": True,          # API access
            "premium_support": True,     # Suporte premium
            "early_access": True,        # Early access a features
            "unlimited_daily": True,
            "no_watermark": True,
            "screenshot_gen": True,
            "description": "30 Days • Premium",
            "badge": "PREMIUM",
            "color": "#a855f7",
        },
        "lifetime": {
            "price_usd": 17.00,
            "duration_days": 36500,
            "max_messages": 999999,
            "daily_messages": 999999,
            "thinking": True,
            "context_messages": 100,
            "max_tokens_allowed": 16384,
            "web_search": True,
            "priority_queue": True,
            "file_upload": True,
            "file_storage": True,
            "file_storage_mb": 9999,     # 10GB storage
            "custom_prompt": True,
            "export_chat": True,
            "api_access": True,
            "premium_support": True,
            "early_access": True,
            "unlimited_daily": True,
            "no_watermark": True,
            "screenshot_gen": True,
            "description": "Lifetime • Unlimited",
            "badge": "ELITE",
            "color": "#f59e0b",
        },
    }

    # Feature labels for display
    FEATURE_LABELS = {
        "thinking": "💭 Visible Thinking",
        "web_search": "🌐 Web Search",
        "priority_queue": "⚡ Priority Queue",
        "file_upload": "📎 File Upload",
        "file_storage": "💾 File Storage",
        "custom_prompt": "✏️ Custom Prompt",
        "export_chat": "📥 Export Chat",
        "api_access": "🔌 API Access",
        "premium_support": "💎 Premium Support",
        "early_access": "🚀 Early Access",
        "unlimited_daily": "♾️ Unlimited Daily",
        "no_watermark": "🚫 No Watermark",
        "screenshot_gen": "📸 Screenshot Gen",
    }

    FEATURE_DETAILS = {
        "context_messages": "🧠 {} msg context",
        "max_tokens_allowed": "📝 {} max tokens",
        "file_storage_mb": "💾 {} MB storage",
    }

    # Crypto
    ACCEPTED_CRYPTO = ["btc", "eth", "usdt", "sol"]
    CRYPTO_NAMES = {
        "btc": "Bitcoin",
        "eth": "Ethereum",
        "usdt": "USDT (ERC-20)",
        "sol": "Solana",
    }
    CRYPTO_LOGOS = {
        "btc": "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6Q...",
        "eth": "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6Q...",
        "usdt": "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6Q...",
        "sol": "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6Q...",
    }

    @classmethod
    def init_dirs(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        cls.DB_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def save(cls, path=None):
        path = path or (BASE_DIR / "config.local.json")
        data = {k: v for k, v in cls.__dict__.items()
                if not k.startswith("_") and k.isupper()}
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @classmethod
    def load(cls, path=None):
        # Reload token/IDs from environment (set by .env loader in main.py)
        cls.DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", cls.DISCORD_TOKEN)
        cls.OWNER_IDS = [int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x] or cls.OWNER_IDS
        cls.GUILD_ID = int(os.getenv("GUILD_ID", str(cls.GUILD_ID)))
        cls.MODEL = os.getenv("MODEL", cls.MODEL)

        # Optional: load from local JSON config
        path = path or (BASE_DIR / "config.local.json")
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(cls, k):
                    setattr(cls, k, v)
