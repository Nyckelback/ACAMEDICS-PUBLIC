"""
Medical Clinical Cases Telegram Bot.
Main entry point for the bot using python-telegram-bot v21.6
"""

import logging
import asyncio
import io
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import TelegramError

from config import Config
from case_parser import parse_case, validate_case
from supabase_client import init_supabase
from justification_messages import get_random_message

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# State constants
STATE_CASE_MODE = 1
STATE_WAITING_IMAGES = 2
STATE_EDIT_TIP = 3
STATE_EDIT_JUSTIFICATION = 4
STATE_EDIT_BIB = 5

# Supabase client
supabase = None
app = None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /start command.
    If args provided: deep link processing
    If no args: welcome message
    """
    user = update.effective_user
    args = context.args

    if not args:
        # Welcome message for new users
        welcome_text = (
            "👋 ¡Bienvenido al Bot de Casos Clínicos Médicos!\n\n"
            "Este bot está diseñado para ayudarte a estudiar y practicar casos clínicos.\n\n"
            "📚 Características:\n"
            "• Acceso a justificaciones detalladas\n"
            "• Mini App con explicaciones completas\n"
            "• Casos organizados por especialidad\n\n"
            "¿Cómo usar?\n"
            "Haz clic en los botones 'VER JUSTIFICACIÓN' en el canal público."
        )
        await update.message.reply_text(welcome_text)
        return

    # Process deep link
    deep_link = args[0]
    logger.info(f"Processing deep link: {deep_link} for user {user.id}")

    # NEW FORMAT: case_UUID
    if deep_link.startswith("case_"):
        await _handle_new_format_deeplink(update, context, deep_link)
        return

    # MINI APP FORMAT: raw UUID (from t.me/bot/appname?startapp=UUID)
    # UUIDs are 36 chars with format xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    if len(deep_link) == 36 and deep_link.count("-") == 4:
        await _handle_new_format_deeplink(update, context, f"case_{deep_link}")
        return

    # OLD FORMATS: backward compatibility
    await _handle_old_format_deeplink(update, context, deep_link)


async def _handle_new_format_deeplink(
    update: Update, context: ContextTypes.DEFAULT_TYPE, deep_link: str
) -> None:
    """Handle new format deep links: case_UUID"""
    try:
        case_uuid = deep_link.replace("case_", "")

        # Send Mini App button
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="📖 VER JUSTIFICACIÓN",
                        web_app=WebAppInfo(url=f"{Config.MINIAPP_URL}?case={case_uuid}"),
                    )
                ]
            ]
        )

        await update.message.reply_text(
            "Abre tu justificación:",
            reply_markup=keyboard,
        )

        # Send random funny message
        funny_msg = get_random_message()
        msg = await update.message.reply_text(funny_msg)

        # Schedule auto-delete
        if Config.AUTO_DELETE_MINUTES > 0:
            context.job_queue.run_once(
                _delete_message,
                when=timedelta(minutes=Config.AUTO_DELETE_MINUTES),
                data=(update.effective_chat.id, msg.message_id),
            )

        logger.info(f"Mini App accessed for case {case_uuid}")
    except Exception as e:
        logger.error(f"Error handling new format deep link: {e}")
        await update.message.reply_text("❌ Error al procesar el enlace.")


async def _handle_old_format_deeplink(
    update: Update, context: ContextTypes.DEFAULT_TYPE, deep_link: str
) -> None:
    """
    Handle old format deep links for backward compatibility.
    Formats: just_XX, j_XX, number only, p_USERNAME_MSGIDS, c_CHATID_MSGIDS, with optional n_ prefix for no joke
    """
    try:
        user_id = update.effective_user.id
        source_chat_id = Config.JUSTIFICATIONS_CHAT_ID
        message_ids = []
        with_joke = True

        working = deep_link

        # n_ prefix = no joke
        if working.startswith("n_"):
            with_joke = False
            working = working[2:]

        if working.startswith("just_"):
            msg_id = int(working[5:])
            message_ids = [msg_id]
        elif working.startswith("j_"):
            msg_id = int(working[2:])
            message_ids = [msg_id]
        elif working.isdigit():
            message_ids = [int(working)]
        elif working.startswith("p_"):
            # p_USERNAME_MSGIDS - public channel
            # Find the last underscore to split username from message IDs
            parts = working[2:].rsplit("_", 1)
            if len(parts) == 2:
                username = parts[0]
                msg_id_str = parts[1]
                message_ids = [int(x) for x in msg_id_str.split("-")]
                try:
                    chat = await context.bot.get_chat(f"@{username}")
                    source_chat_id = chat.id
                except:
                    pass
        elif working.startswith("c_"):
            # c_CHATID_MSGIDS - private channel
            parts = working[2:].split("_")
            if len(parts) == 2:
                source_chat_id = int(f"-100{parts[0]}")
                message_ids = [int(x) for x in parts[1].split("-")]

        if not message_ids:
            logger.warning(f"Could not parse old format deep link: {deep_link}")
            await update.message.reply_text("❌ Enlace inválido.")
            return

        # Send each message
        sent_ids = []
        for msg_id in message_ids:
            try:
                sent = await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=msg_id,
                    protect_content=True,
                )
                sent_ids.append(sent.message_id)
            except Exception as e:
                logger.error(f"Error copying msg {msg_id}: {e}")

        if not sent_ids:
            await update.message.reply_text("❌ Justificación no encontrada.")
            return

        # Funny message
        if with_joke:
            funny = get_random_message()
        else:
            funny = "📦 ¡Contenido entregado!"
        msg = await update.message.reply_text(funny)
        sent_ids.append(msg.message_id)

        # Schedule auto-delete for ALL sent messages
        if Config.AUTO_DELETE_MINUTES > 0:
            for mid in sent_ids:
                context.job_queue.run_once(
                    _delete_message,
                    when=timedelta(minutes=Config.AUTO_DELETE_MINUTES),
                    data=(user_id, mid),
                )

    except ValueError as e:
        logger.error(f"Error parsing old format deep link: {e}")
        await update.message.reply_text("❌ Enlace inválido.")
    except Exception as e:
        logger.error(f"Error handling old format deep link: {e}")
        await update.message.reply_text("❌ Error al procesar el enlace.")


async def _delete_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a message after timeout."""
    try:
        chat_id, message_id = context.job.data
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")


async def caso_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /caso command (admin only) - activate case mode."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Solo administradores pueden crear casos.")
        return ConversationHandler.END

    # Initialize conversation data
    context.user_data["pending_case"] = None
    context.user_data["images"] = []

    await update.message.reply_text(
        "📝 Envía el caso clínico completo (viñeta + opciones + CORRECTA + justificación + tip + bibliografía)"
    )
    return STATE_CASE_MODE


async def case_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input in case mode."""
    try:
        # Parse the case
        parsed = parse_case(update.message.text)

        if not parsed.parsed_ok:
            error_text = "❌ Error al parsear el caso:\n\n"
            error_text += "\n".join(f"• {err}" for err in parsed.errors)
            error_text += "\n\n📝 Envía nuevamente el caso completo:"
            await update.message.reply_text(error_text)
            return STATE_CASE_MODE

        # Validate the case
        is_valid, val_errors = validate_case(parsed)
        if not is_valid:
            error_text = "❌ Caso inválido:\n\n"
            error_text += "\n".join(f"• {err}" for err in val_errors)
            error_text += "\n\n📝 Envía nuevamente el caso completo:"
            await update.message.reply_text(error_text)
            return STATE_CASE_MODE

        # Store parsed case in context
        context.user_data["pending_case"] = parsed.to_dict()
        context.user_data["pending_case"]["images"] = []

        # Show preview with visual score
        checks = []
        checks.append(("Viñeta", bool(parsed.vignette)))
        checks.append(("Opciones", len(parsed.options) >= 2))
        checks.append(("Correcta", bool(parsed.correct_letter)))
        checks.append(("Justificación", bool(parsed.justification)))
        checks.append(("Tip", bool(parsed.tip)))
        checks.append(("Bibliografía", len(parsed.bibliography) > 0))

        passed = sum(1 for _, ok in checks if ok)
        total = len(checks)
        all_ok = passed == total

        # Score bar
        score_emoji = "✅" if all_ok else "⚠️"
        score_bar = "".join("🟢" if ok else "🔴" for _, ok in checks)
        header = f"{score_emoji} {passed}/{total} {score_bar}"
        if all_ok:
            header += "\nCASO LISTO PARA PUBLICAR"
        else:
            header += "\nFALTAN CAMPOS"

        # Sections
        vig_preview = parsed.vignette[:90].replace('\n', ' ')
        vig_line = f"{'✅' if parsed.vignette else '❌'} Viñeta: {vig_preview}{'...' if len(parsed.vignette) > 90 else ''}"

        opt_letters = ', '.join(opt['letter'] for opt in parsed.options)
        opt_line = f"{'✅' if len(parsed.options) >= 2 else '❌'} Opciones ({len(parsed.options)}): {opt_letters}"

        # Show correct answer with its text
        correct_text = ""
        for opt in parsed.options:
            if opt["letter"] == parsed.correct_letter:
                correct_text = opt["text"][:50]
                break
        cor_line = f"{'✅' if parsed.correct_letter else '❌'} Correcta: {parsed.correct_letter}" + (f" - {correct_text}..." if correct_text else "")

        just_preview = parsed.justification[:80].replace('\n', ' ') if parsed.justification else "(vacío)"
        just_line = f"{'✅' if parsed.justification else '❌'} Justificación: {just_preview}{'...' if len(parsed.justification) > 80 else ''}"

        tip_preview = parsed.tip[:70].replace('\n', ' ') if parsed.tip else "(vacío)"
        tip_line = f"{'✅' if parsed.tip else '⚪'} Tip: {tip_preview}{'...' if parsed.tip and len(parsed.tip) > 70 else ''}"

        bib_line = f"{'✅' if parsed.bibliography else '⚪'} Bibliografía: {len(parsed.bibliography)} refs"

        preview = (
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{vig_line}\n\n"
            f"{opt_line}\n\n"
            f"{cor_line}\n\n"
            f"{just_line}\n\n"
            f"{tip_line}\n\n"
            f"{bib_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )

        # Action prompt
        if all_ok:
            preview += "📸 Envía imágenes o /publicar para publicar"
        else:
            missing = [name for name, ok in checks if not ok]
            preview += f"⚠️ Falta: {', '.join(missing)}\n📝 Envía el caso de nuevo con los campos faltantes"

        await update.message.reply_text(preview)
        return STATE_WAITING_IMAGES

    except Exception as e:
        logger.error(f"Error processing case: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return STATE_CASE_MODE


async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle photo uploads in waiting_images state."""
    try:
        if not update.message.photo:
            return STATE_WAITING_IMAGES

        # Get the largest photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        # Download photo
        photo_bytes = await file.download_as_bytearray()

        # Upload to Supabase
        filename = f"photo_{photo.file_id}.jpg"
        image_url = supabase.upload_image(bytes(photo_bytes), filename)

        if not image_url:
            await update.message.reply_text("❌ Error al subir la imagen. Intenta de nuevo.")
            return STATE_WAITING_IMAGES

        # Add to pending case
        if "images" not in context.user_data["pending_case"]:
            context.user_data["pending_case"]["images"] = []
        context.user_data["pending_case"]["images"].append(image_url)

        image_count = len(context.user_data["pending_case"]["images"])
        await update.message.reply_text(
            f"🖼️ Imagen {image_count} agregada. Envía más o /publicar"
        )
        return STATE_WAITING_IMAGES

    except Exception as e:
        logger.error(f"Error handling image: {e}")
        await update.message.reply_text(f"❌ Error al procesar la imagen: {str(e)}")
        return STATE_WAITING_IMAGES


async def document_warning_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Warn admin when they send a document instead of a photo."""
    await update.message.reply_text(
        "⚠️ Enviaste un archivo/documento. Para agregar imágenes, envíalas como FOTO (no como archivo).\n"
        "Envía fotos o /publicar para continuar."
    )
    return STATE_WAITING_IMAGES


async def editar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show edit menu for pending case."""
    pending = context.user_data.get("pending_case")
    if not pending:
        await update.message.reply_text("❌ No hay caso en preparación.")
        return ConversationHandler.END
    await update.message.reply_text(
        "✏️ ¿Qué quieres editar?\n\n"
        "/editar_tip - Cambiar el tip\n"
        "/editar_just - Cambiar la justificación\n"
        "/editar_bib - Cambiar la bibliografía\n\n"
        "O envía /publicar para publicar tal cual."
    )
    return STATE_WAITING_IMAGES


async def editar_tip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start editing tip."""
    pending = context.user_data.get("pending_case")
    if not pending:
        await update.message.reply_text("❌ No hay caso en preparación.")
        return ConversationHandler.END
    current = pending.get("tip", "(vacío)")
    await update.message.reply_text(
        f"💡 Tip actual:\n{current[:200]}{'...' if len(current) > 200 else ''}\n\n"
        "📝 Envía el nuevo tip:"
    )
    return STATE_EDIT_TIP


async def edit_tip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive new tip text."""
    context.user_data["pending_case"]["tip"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Tip actualizado.\n\n"
        "📸 Envía imágenes o /publicar para publicar."
    )
    return STATE_WAITING_IMAGES


async def editar_just_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start editing justification."""
    pending = context.user_data.get("pending_case")
    if not pending:
        await update.message.reply_text("❌ No hay caso en preparación.")
        return ConversationHandler.END
    current = pending.get("justification", "(vacío)")
    await update.message.reply_text(
        f"📝 Justificación actual:\n{current[:300]}{'...' if len(current) > 300 else ''}\n\n"
        "📝 Envía la nueva justificación:"
    )
    return STATE_EDIT_JUSTIFICATION


async def edit_just_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive new justification text."""
    context.user_data["pending_case"]["justification"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Justificación actualizada.\n\n"
        "📸 Envía imágenes o /publicar para publicar."
    )
    return STATE_WAITING_IMAGES


async def editar_bib_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start editing bibliography."""
    pending = context.user_data.get("pending_case")
    if not pending:
        await update.message.reply_text("❌ No hay caso en preparación.")
        return ConversationHandler.END
    current_refs = pending.get("bibliography", [])
    bib_text = "\n".join(f"• {r}" for r in current_refs) if current_refs else "(vacío)"
    await update.message.reply_text(
        f"📚 Bibliografía actual:\n{bib_text[:400]}\n\n"
        "📝 Envía la nueva bibliografía (una referencia por línea):"
    )
    return STATE_EDIT_BIB


async def edit_bib_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive new bibliography text."""
    lines = [l.strip() for l in update.message.text.strip().split("\n") if l.strip()]
    # Clean prefixes like "- ", "* ", "1. " etc.
    import re as _re
    cleaned = []
    for line in lines:
        cleaned.append(_re.sub(r"^[\d]+[.)]\s*|^[-*•]\s*", "", line).strip())
    context.user_data["pending_case"]["bibliography"] = [r for r in cleaned if r]
    await update.message.reply_text(
        f"✅ Bibliografía actualizada ({len(cleaned)} refs).\n\n"
        "📸 Envía imágenes o /publicar para publicar."
    )
    return STATE_WAITING_IMAGES


async def publicar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /publicar command (admin only) - publish case as quiz poll."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Solo administradores pueden publicar casos.")
        return ConversationHandler.END

    try:
        pending_case = context.user_data.get("pending_case")
        if not pending_case:
            await update.message.reply_text("❌ No hay un caso en preparación.")
            return ConversationHandler.END

        # Get case number BEFORE saving (so count is accurate)
        case_number = supabase.get_next_case_number()

        # Save case to Supabase
        case_uuid = supabase.save_case(pending_case)
        if not case_uuid:
            await update.message.reply_text("❌ Error al guardar el caso en la base de datos.")
            return ConversationHandler.END

        # Prepare poll data
        vignette = pending_case["vignette"]
        options = pending_case["options"]

        # If vignette is too long for poll question (300 char limit), send it as text first
        poll_question = vignette
        if len(vignette) > 290:
            # Send vignette as separate message
            await context.bot.send_message(
                chat_id=Config.PUBLIC_CHANNEL_ID,
                text=vignette,
            )
            # Use shorter question
            poll_question = "¿Cuál es la respuesta correcta?"

        # Prepare option texts
        option_texts = []
        for opt in options:
            full = f"{opt['letter']}. {opt['text']}"
            # Telegram limit is 100 chars per option
            if len(full) > 100:
                full = full[:97] + "..."
            option_texts.append(full)

        correct_letter = pending_case["correct_letter"]
        correct_index = next(
            (i for i, opt in enumerate(options) if opt["letter"] == correct_letter),
            0,
        )

        # Prepare explanation (first 200 chars)
        explanation = pending_case["justification"][:200]

        # Prepare Mini App button (attached directly to the poll)
        miniapp_short_name = Config.MINIAPP_SHORT_NAME
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="VER JUSTIFICACIÓN 💬",
                        url=f"https://t.me/{context.bot.username}/{miniapp_short_name}?startapp={case_uuid}",
                    )
                ]
            ]
        )

        # Send poll to channel WITH the button attached
        poll_msg = await context.bot.send_poll(
            chat_id=Config.PUBLIC_CHANNEL_ID,
            question=poll_question,
            options=option_texts,
            type="quiz",
            correct_option_id=correct_index,
            explanation=explanation,
            is_anonymous=True,
            reply_markup=keyboard,
        )

        logger.info(f"Poll published for case {case_uuid}: {poll_msg.message_id}")

        # Update case with message IDs
        supabase.update_case(
            case_uuid,
            {
                "telegram_message_id": poll_msg.message_id,
                "published": True,
            },
        )

        # Confirm to admin
        await update.message.reply_text(
            f"✅ Caso #{case_number} publicado en el canal.\n"
            f"UUID: `{case_uuid}`",
            parse_mode="Markdown",
        )

        # Clear user data
        context.user_data.clear()
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error publishing case: {e}")
        await update.message.reply_text(f"❌ Error al publicar el caso: {str(e)}")
        return STATE_WAITING_IMAGES


async def cancelar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancelar command - cancel case creation."""
    context.user_data.clear()
    await update.message.reply_text("🗑️ Cancelado")
    return ConversationHandler.END


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin command - show admin commands."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Solo administradores pueden usar este comando.")
        return

    admin_text = (
        "🔧 **Comandos de Administrador:**\n\n"
        "/caso - Iniciar creación de caso clínico\n"
        "/publicar - Publicar caso en el canal\n"
        "/cancelar - Cancelar operación en curso\n"
        "/editar - Ver opciones de edición\n"
        "/editar\\_tip - Editar el tip\n"
        "/editar\\_just - Editar la justificación\n"
        "/editar\\_bib - Editar la bibliografía\n"
        "/admin - Ver este menú\n\n"
        "**Flujo:**\n"
        "1. /caso → Envía el caso → (Fotos) → /publicar\n"
        "2. Antes de /publicar puedes usar /editar\n"
    )
    await update.message.reply_text(admin_text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "ℹ️ **Ayuda**\n\n"
        "Este bot gestiona casos clínicos médicos.\n\n"
        "**Para usuarios:**\n"
        "/start - Mensaje de bienvenida\n\n"
        "**Para administradores:**\n"
        "/admin - Ver comandos de admin\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


def _is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return user_id in Config.ADMIN_USER_IDS


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in the bot."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # Try to send error message to admin
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Error interno del bot. Los administradores han sido notificados."
            )
        except Exception as e:
            logger.error(f"Could not send error message: {e}")


def main() -> None:
    """Main entry point for the bot."""
    global supabase, app

    try:
        # Initialize Supabase
        supabase = init_supabase(Config.SUPABASE_URL, Config.SUPABASE_KEY, Config.SUPABASE_SERVICE_KEY)
        logger.info("Supabase initialized")

        # Create bot application
        app = Application.builder().token(Config.BOT_TOKEN).build()

        # Register handlers
        # Start command
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("admin", admin_command))

        # Case creation conversation
        case_conv = ConversationHandler(
            entry_points=[CommandHandler("caso", caso_command)],
            states={
                STATE_CASE_MODE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, case_text_handler),
                ],
                STATE_WAITING_IMAGES: [
                    MessageHandler(filters.PHOTO, image_handler),
                    MessageHandler(filters.Document.ALL, document_warning_handler),
                    CommandHandler("publicar", publicar_command),
                    CommandHandler("editar", editar_command),
                    CommandHandler("editar_tip", editar_tip_command),
                    CommandHandler("editar_just", editar_just_command),
                    CommandHandler("editar_bib", editar_bib_command),
                    CommandHandler("cancelar", cancelar_command),
                ],
                STATE_EDIT_TIP: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_tip_handler),
                    CommandHandler("cancelar", cancelar_command),
                ],
                STATE_EDIT_JUSTIFICATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_just_handler),
                    CommandHandler("cancelar", cancelar_command),
                ],
                STATE_EDIT_BIB: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_bib_handler),
                    CommandHandler("cancelar", cancelar_command),
                ],
            },
            fallbacks=[CommandHandler("cancelar", cancelar_command)],
            per_user=True,
        )
        app.add_handler(case_conv)

        # Error handler
        app.add_error_handler(error_handler)

        logger.info("Bot handlers registered")
        logger.info("Starting bot in polling mode...")

        # Start health check server for Render (needs an open port)
        port = int(os.environ.get("PORT", 10000))
        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            def log_message(self, format, *args):
                pass  # Suppress logs
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f"Health check server on port {port}")

        # Ensure event loop exists (required for Python 3.14+)
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        # Start the bot (blocking call)
        # drop_pending_updates=True prevents processing old queued updates on restart
        app.run_polling(drop_pending_updates=True)

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
