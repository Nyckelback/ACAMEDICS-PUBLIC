"""
ACAMEDICS Scheduler — Automatic publication of scheduled cases.

Runs as an async background loop inside the bot process.
- Polls Supabase every 30 seconds for due posts
- Uses asyncio.Lock to serialize publications (never 2 in parallel)
- On startup, marks overdue posts as 'failed' (no auto-publish of past posts)
- Notifies admin on failures
"""

import asyncio
import logging
from datetime import datetime

import pytz

from config import Config

logger = logging.getLogger(__name__)

# Lock to ensure only one case publishes at a time
_publish_lock = asyncio.Lock()

# Reference to bot application (set by main.py on startup)
_bot_app = None
_supabase = None

POLL_INTERVAL_SECONDS = 30


def init_scheduler(bot_app, supabase_client):
    """Initialize scheduler with references to bot app and supabase."""
    global _bot_app, _supabase
    _bot_app = bot_app
    _supabase = supabase_client
    logger.info("Scheduler initialized")


async def on_startup():
    """
    Called once after bot starts.
    Marks overdue posts as 'failed' and notifies admin.
    """
    if not _supabase:
        return

    tz = pytz.timezone(Config.TZ)
    now = datetime.now(tz)

    overdue = _supabase.mark_overdue_as_failed(now)
    if overdue and _bot_app and Config.ADMIN_USER_IDS:
        # Notify first admin
        admin_id = Config.ADMIN_USER_IDS[0]
        lines = []
        for post in overdue:
            case_data = post.get("cases") or {}
            vig = (case_data.get("vignette") or "???")[:60].replace("\n", " ")
            scheduled = post.get("scheduled_at", "?")
            # Parse and format the date nicely
            try:
                dt = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                dt_local = dt.astimezone(tz)
                date_str = dt_local.strftime("%a %d %b %H:%M")
            except Exception:
                date_str = scheduled[:16]
            lines.append(f"  • {date_str} — «{vig}...»")

        msg = (
            f"⚠️ <b>{len(overdue)} caso(s) no se publicaron</b> "
            f"(bot offline):\n\n"
            + "\n".join(lines)
            + "\n\nPublícalos manualmente con /publicar o /caso."
        )
        try:
            await _bot_app.bot.send_message(
                chat_id=admin_id, text=msg, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Could not notify admin about overdue posts: {e}")

    # Also clean up any stuck 'publishing' entries (from crash during publish)
    try:
        result = (
            _supabase.service_client.table("scheduled_posts")
            .select("id")
            .eq("status", "publishing")
            .execute()
        )
        stuck = result.data or []
        for post in stuck:
            _supabase.mark_failed(post["id"], "Bot se reinició durante publicación")
        if stuck:
            logger.warning(f"Cleaned up {len(stuck)} stuck 'publishing' entries")
    except Exception as e:
        logger.error(f"Error cleaning up stuck entries: {e}")


async def scheduler_loop():
    """
    Main scheduler loop. Runs forever, polling every 30 seconds.
    """
    logger.info("Scheduler loop started")

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            await _check_and_publish()
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")
            break
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}", exc_info=True)
            # Don't crash the loop on errors
            await asyncio.sleep(10)


async def _check_and_publish():
    """Check for due posts and publish them one by one."""
    if not _supabase or not _bot_app:
        return

    tz = pytz.timezone(Config.TZ)
    now = datetime.now(tz)

    due_posts = _supabase.get_due_posts(now)
    if not due_posts:
        return

    logger.info(f"Found {len(due_posts)} due post(s) to publish")

    for post in due_posts:
        async with _publish_lock:
            await _publish_single(post)


async def _publish_single(post: dict):
    """
    Publish a single scheduled post.
    Reuses the same logic as manual /publicar.
    """
    entry_id = post["id"]
    case_id = post["case_id"]
    case_data = post.get("cases")

    if not case_data:
        _supabase.mark_failed(entry_id, "Case data not found in DB")
        await _notify_admin_failure(entry_id, "Caso no encontrado en la DB")
        return

    # Skip if case was already published manually
    if case_data.get("published"):
        logger.info(f"Skipping {entry_id}: case {case_id} already published manually")
        _supabase.service_client.table("scheduled_posts").update({
            "status": "done",
            "error_message": "Already published manually",
        }).eq("id", entry_id).execute()
        return

    # Mark as publishing (lock in DB)
    if not _supabase.mark_publishing(entry_id):
        return

    try:
        bot = _bot_app.bot

        # ── Prepare poll data (same logic as _do_publicar in main.py) ──
        vignette = case_data["vignette"]
        options = case_data.get("options", [])

        poll_question = vignette
        if len(vignette) > 290:
            await bot.send_message(
                chat_id=Config.PUBLIC_CHANNEL_ID,
                text=vignette,
            )
            poll_question = "¿Cuál es la respuesta correcta?"

        option_texts = []
        for opt in options:
            full = f"{opt['letter']}. {opt['text']}"
            if len(full) > 100:
                full = full[:97] + "..."
            option_texts.append(full)

        correct_letter = case_data["correct_letter"]
        correct_index = next(
            (i for i, opt in enumerate(options) if opt["letter"] == correct_letter),
            0,
        )

        # Explanation tooltip
        tip_text = case_data.get("tip", "")
        explanation = f"💡 {tip_text[:195]}" if tip_text else (case_data.get("justification") or "")[:200]

        # Mini App button
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        miniapp_short_name = Config.MINIAPP_SHORT_NAME
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="VER JUSTIFICACIÓN 💬",
                        url=f"https://t.me/{bot.username}/{miniapp_short_name}?startapp={case_id}",
                    )
                ]
            ]
        )

        # Send poll to channel
        poll_msg = await bot.send_poll(
            chat_id=Config.PUBLIC_CHANNEL_ID,
            question=poll_question,
            options=option_texts,
            type="quiz",
            correct_option_id=correct_index,
            explanation=explanation,
            is_anonymous=True,
            reply_markup=keyboard,
        )

        logger.info(f"Scheduled post {entry_id} published: poll msg {poll_msg.message_id}")

        # Update case as published
        from main import case_display_num
        _supabase.update_case(case_id, {
            "telegram_message_id": poll_msg.message_id,
            "published": True,
            "display_number": case_display_num(case_id),
        })

        # Mark schedule entry as done
        _supabase.mark_done(entry_id, poll_msg.message_id)

        # Notify admin
        await _notify_admin_success(case_id, post.get("scheduled_at", ""))

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error(f"Failed to publish scheduled post {entry_id}: {e}")
        _supabase.mark_failed(entry_id, error_msg)
        await _notify_admin_failure(entry_id, error_msg)


async def _notify_admin_success(case_id: str, scheduled_at: str):
    """Send a success notification to the first admin."""
    if not _bot_app or not Config.ADMIN_USER_IDS:
        return
    try:
        tz = pytz.timezone(Config.TZ)
        try:
            dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            dt_local = dt.astimezone(tz)
            time_str = dt_local.strftime("%H:%M")
        except Exception:
            time_str = "?"

        await _bot_app.bot.send_message(
            chat_id=Config.ADMIN_USER_IDS[0],
            text=f"✅ Caso programado publicado automáticamente ({time_str}).",
        )
    except Exception as e:
        logger.error(f"Could not send success notification: {e}")


async def _notify_admin_failure(entry_id: str, error_msg: str):
    """Send a failure notification to the first admin."""
    if not _bot_app or not Config.ADMIN_USER_IDS:
        return
    try:
        await _bot_app.bot.send_message(
            chat_id=Config.ADMIN_USER_IDS[0],
            text=(
                f"❌ <b>Fallo al publicar caso programado</b>\n\n"
                f"Error: {error_msg[:200]}\n\n"
                f"Publícalo manualmente."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Could not send failure notification: {e}")
