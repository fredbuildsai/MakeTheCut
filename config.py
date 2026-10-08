import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── API credentials ───────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
MISTRAL_API_KEY: str = os.getenv("MISTRAL_API_KEY", "")
AUTHORIZED_USER_ID: int = int(os.getenv("AUTHORIZED_USER_ID", "0"))

# ── Model ─────────────────────────────────────────────────────────────────────
MISTRAL_MODEL: str = os.getenv(key="MISTRAL_MODEL", default="mistral-medium-latest")
MISTRAL_AGENT_ID: str = os.getenv(key="MISTRAL_AGENT_ID", default="")

# ── Workflow ──────────────────────────────────────────────────────────────────
FIT_THRESHOLD = 70  # minimum score to offer CV rewrite + cover letter

# ── Email (Resend) ────────────────────────────────────────────────────────────
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
RESEND_FROM: str = os.getenv("RESEND_FROM", "onboarding@resend.dev")
RESEND_TO: str = os.getenv("RESEND_TO", "")

# ── Timeouts ──────────────────────────────────────────────────────────────────
# SDK-level: max time to wait for a single HTTP response from Mistral.
MISTRAL_SDK_TIMEOUT = 45_000     # milliseconds — passed as timeout_ms to Mistral SDK
# Handler-level: asyncio.wait_for guards (slightly above SDK timeout so the SDK
# error surfaces first and gives a more informative message).
AGENT_CALL_TIMEOUT  = 50        # /ask and research_company (agent, web search)
CHAT_CALL_TIMEOUT   = 30        # chat mode (simple_chat, no web search)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
PROFILE_DIR = BASE_DIR / "profile"
APPLICATIONS_DIR = BASE_DIR / "applications"
CV_PATH = PROFILE_DIR / "cv.md"
PREFERENCES_PATH = PROFILE_DIR / "preferences.md"

# Create output directory on import
APPLICATIONS_DIR.mkdir(exist_ok=True)
PROFILE_DIR.mkdir(exist_ok=True)
