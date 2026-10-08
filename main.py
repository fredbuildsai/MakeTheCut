import sys
import time

_t0 = time.monotonic()

def _elapsed() -> str:
    return f"{time.monotonic() - _t0:.1f}s"

def _step(msg: str) -> None:
    print(f"[{_elapsed()}] {msg}", flush=True)

# Unbuffered output first — before any other imports so errors are visible immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

_step("⚙️  Loading modules…")

import logging;                                                                    _step("  ✓ logging")
from telegram.ext import Application, CommandHandler, MessageHandler, filters;    _step("  ✓ telegram.ext")
from config import TELEGRAM_BOT_TOKEN;                                             _step("  ✓ config")
from bot.handler import handle_message, handle_start, handle_list, handle_ask, handle_help, handle_email, handle_photo, handle_reset, handle_continue; _step("  ✓ bot.handler")
from utils.file_manager import write_schema;                                       _step("  ✓ file_manager")

_step("⚙️  Modules loaded.")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── Bot metadata ──────────────────────────────────────────────────────────────
_DESCRIPTION = (
    "Stop wasting time on jobs that aren't a real fit.\n"
    "Send a job posting URL, get an honest fit score against your CV and preferences, "
    "then optionally research the company or sharpen your CV with a tailored cover letter.\n"
    "\n"
    "How to use:\n"
    "1. Send a job URL (or paste the text if scraping fails).\n"
    "2. Review the fit score and component breakdown.\n"
    "3. Reply 'research', 'sharpen', or skip.\n"
    "\n"
    "/start — show this intro\n"
    "/list — table of all analysed jobs ('text' for plain text)\n"
    "/ask <question> — ask about your saved analyses"
)

_SHORT_DESCRIPTION = (
    "Scores job postings against your CV, then sharpens your application where it counts."
)

_COMMANDS = [
    ("start", "Show introduction and usage instructions"),
    ("help", "Full command reference and usage guide"),
    ("list", "Table of all analysed jobs (append 'text' for plain text)"),
    ("ask", "Ask a follow-up question about your saved analyses"),
    ("email", "Email the most recent application materials via Resend"),
    ("reset", "Clear all state and return to idle"),
    ("continue", "Show what the bot is waiting for if stuck"),
]


async def _sync_bot_meta(app) -> None:
    import asyncio
    try:
        await asyncio.wait_for(_do_sync_bot_meta(app), timeout=15)
    except asyncio.TimeoutError:
        logger.warning("Bot metadata sync timed out after 15s — skipping")
    except Exception as exc:
        logger.warning("Bot metadata sync failed: %s — skipping", exc)


async def _do_sync_bot_meta(app) -> None:
    import time as _time
    bot = app.bot
    changed = False

    t = _time.monotonic()
    logger.info("[%.1fs] Syncing bot metadata — fetching description…", time.monotonic() - _t0)
    current_desc = await bot.get_my_description()
    logger.info("[%.1fs] Description fetched (%.2fs)", time.monotonic() - _t0, _time.monotonic() - t)
    if current_desc.description != _DESCRIPTION:
        t = _time.monotonic()
        logger.info("Description changed — updating…")
        await bot.set_my_description(_DESCRIPTION)
        logger.info("Description updated (%.2fs)", _time.monotonic() - t)
        changed = True
    else:
        logger.info("Description unchanged")

    t = _time.monotonic()
    logger.info("[%.1fs] Fetching short description…", time.monotonic() - _t0)
    current_short = await bot.get_my_short_description()
    logger.info("[%.1fs] Short description fetched (%.2fs)", time.monotonic() - _t0, _time.monotonic() - t)
    if current_short.short_description != _SHORT_DESCRIPTION:
        t = _time.monotonic()
        logger.info("Short description changed — updating…")
        await bot.set_my_short_description(_SHORT_DESCRIPTION)
        logger.info("Short description updated (%.2fs)", _time.monotonic() - t)
        changed = True
    else:
        logger.info("Short description unchanged")

    t = _time.monotonic()
    logger.info("[%.1fs] Fetching command list…", time.monotonic() - _t0)
    current_commands = await bot.get_my_commands()
    logger.info("[%.1fs] Commands fetched (%.2fs)", time.monotonic() - _t0, _time.monotonic() - t)
    current_tuples = [(c.command, c.description) for c in current_commands]
    if current_tuples != _COMMANDS:
        t = _time.monotonic()
        logger.info("Command list changed — updating…")
        await bot.set_my_commands(_COMMANDS)
        logger.info("Command list updated (%.2fs)", _time.monotonic() - t)
        changed = True
    else:
        logger.info("Command list unchanged")

    if not changed:
        logger.info("[%.1fs] Bot metadata unchanged — no update needed", time.monotonic() - _t0)
    else:
        logger.info("[%.1fs] Bot metadata sync complete", time.monotonic() - _t0)


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Check your .env file.")

    _step("🤖  Starting the Job Fit Bot")
    logger.info("[%.1fs] Writing schema…", time.monotonic() - _t0)
    write_schema()
    logger.info("[%.1fs] Schema written — building Telegram application…", time.monotonic() - _t0)
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_sync_bot_meta)
        .build()
    )
    logger.info("[%.1fs] Application built — registering handlers…", time.monotonic() - _t0)
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("ask", handle_ask))
    app.add_handler(CommandHandler("email", handle_email))
    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(CommandHandler("continue", handle_continue))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))

    logger.info("[%.1fs] Connecting to Telegram — post_init (metadata sync) will run next…", time.monotonic() - _t0)
    _step("🤖  Job Fit Bot is running — press Ctrl+C to stop")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
