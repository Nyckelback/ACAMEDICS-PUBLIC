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

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import TelegramError
from telegram.constants import ChatAction

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
STATE_EDIT_PUBLISHED_NUMBER = 6
STATE_EDIT_PUBLISHED_CASE = 7
STATE_EDIT_PUBLISHED_CONFIRM = 8


def case_display_num(uuid_str: str) -> int:
    """Generate a consistent display number (1000-3000) from UUID hash.
    Must match the JavaScript version in index.html exactly."""
    if not uuid_str:
        return 1000
    h = 0
    for ch in uuid_str:
        h = ((h << 5) - h) + ord(ch)
        h &= 0xFFFFFFFF  # Keep as 32-bit
        if h >= 0x80000000:
            h -= 0x100000000  # Convert to signed 32-bit
    return 1000 + (abs(h) % 2001)

# Supabase client
supabase = None
app = None


def _admin_keyboard() -> ReplyKeyboardMarkup:
    """Build collapsible keyboard for admin (shown via grid icon, not persistent)."""
    keyboard = [
        [KeyboardButton("Caso"), KeyboardButton("Preview")],
        [KeyboardButton("Publicar"), KeyboardButton("Cancelar")],
        [KeyboardButton("Editar"), KeyboardButton("Editar Caso")],
        [KeyboardButton("Admin")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=False,
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /start command.
    If args provided: deep link processing
    If no args: welcome message
    """
    user = update.effective_user
    args = context.args

    if not args:
        # Welcome message
        welcome_text = (
            "🎓 ¡Bienvenido a ACAMEDICS!\n\n"
            "Donde la medicina se aprende caso a caso.\n\n"
            "Casos clínicos con justificación completa, "
            "tips acamédicos y bibliografía basada en evidencia.\n\n"
            "Entra al canal, pon a prueba tu criterio clínico "
            "y aprende con cada justificación."
        )
        if _is_admin(user.id):
            await update.message.reply_text(
                welcome_text,
                reply_markup=_admin_keyboard(),
            )
        else:
            await update.message.reply_text(welcome_text)
        return

    # Process deep link - log ALL args for debugging
    logger.info(f"/start called with args={args} for user {user.id}")
    deep_link = args[0]

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


async def _delete_previous_justification(
    user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Delete previous justification messages before sending new ones."""
    prev_ids = context.user_data.get("last_justification_ids", [])
    if prev_ids:
        for mid in prev_ids:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=mid)
            except Exception:
                pass  # Message may already be deleted
        context.user_data["last_justification_ids"] = []
        logger.info(f"Deleted {len(prev_ids)} previous justification messages for user {user_id}")


async def _handle_new_format_deeplink(
    update: Update, context: ContextTypes.DEFAULT_TYPE, deep_link: str
) -> None:
    """Handle new format deep links: case_UUID"""
    try:
        case_uuid = deep_link.replace("case_", "")
        user_id = update.effective_user.id

        # Delete previous justification messages
        await _delete_previous_justification(user_id, context)

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

        msg1 = await update.message.reply_text(
            "Abre tu justificación:",
            reply_markup=keyboard,
        )

        # Send random funny message
        funny_msg = get_random_message()
        msg2 = await update.message.reply_text(funny_msg)

        # Track sent message IDs for future deletion
        sent_ids = [msg1.message_id, msg2.message_id]
        context.user_data["last_justification_ids"] = sent_ids

        # Schedule auto-delete
        if Config.AUTO_DELETE_MINUTES > 0:
            for mid in sent_ids:
                try:
                    context.job_queue.run_once(
                        _delete_message,
                        when=timedelta(minutes=Config.AUTO_DELETE_MINUTES),
                        data=(user_id, mid),
                    )
                except Exception as e:
                    logger.warning(f"Could not schedule auto-delete: {e}")

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
    user_id = update.effective_user.id
    logger.info(f"Old format deep link: '{deep_link}' for user {user_id}")

    try:
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
            parts = working[2:].rsplit("_", 1)
            if len(parts) == 2:
                username = parts[0]
                msg_id_str = parts[1]
                message_ids = [int(x) for x in msg_id_str.split("-")]
                try:
                    chat = await context.bot.get_chat(f"@{username}")
                    source_chat_id = chat.id
                except Exception:
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

        logger.info(f"Parsed: source_chat={source_chat_id}, msg_ids={message_ids}")

        # Delete previous justification messages before sending new ones
        await _delete_previous_justification(user_id, context)

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
                logger.info(f"Copied msg {msg_id} -> {sent.message_id}")
            except Exception as e:
                logger.error(f"Error copying msg {msg_id} from {source_chat_id}: {e}")

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

        # Track sent message IDs for future deletion
        context.user_data["last_justification_ids"] = sent_ids

        # Schedule auto-delete for ALL sent messages
        if Config.AUTO_DELETE_MINUTES > 0:
            for mid in sent_ids:
                try:
                    context.job_queue.run_once(
                        _delete_message,
                        when=timedelta(minutes=Config.AUTO_DELETE_MINUTES),
                        data=(user_id, mid),
                    )
                except Exception as e:
                    logger.warning(f"Could not schedule auto-delete for msg {mid}: {e}")

        logger.info(f"Old deep link processed successfully: {len(sent_ids)} messages sent")

    except ValueError as e:
        logger.error(f"Error parsing old format deep link '{deep_link}': {e}")
        await update.message.reply_text("❌ Enlace inválido.")
    except Exception as e:
        logger.error(f"Error handling old format deep link '{deep_link}': {e}", exc_info=True)
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
        return ConversationHandler.END

    # Check if there's already a pending case
    pending = context.user_data.get("pending_case")
    if pending and not context.user_data.get("caso_confirmed_replace"):
        vig_short = pending.get("vignette", "")[:60].replace("\n", " ")
        await update.message.reply_text(
            f"⚠️ Ya tienes un caso pendiente:\n"
            f"«{vig_short}...»\n\n"
            "¿Qué deseas hacer?\n"
            "/caso → Reemplazar con uno nuevo\n"
            "/cancelar → Cancelar el pendiente\n"
            "/publicar → Publicar el pendiente"
        )
        # Mark so next /caso goes through without asking again
        context.user_data["caso_confirmed_replace"] = True
        return STATE_WAITING_IMAGES

    # Clean up any leftover unpublished preview
    old_preview = context.user_data.get("preview_uuid")
    if old_preview:
        try:
            supabase.delete_case(old_preview)
            logger.info(f"Cleaned up old preview {old_preview} on new /caso")
        except Exception as e:
            logger.warning(f"Could not delete old preview {old_preview}: {e}")

    # Initialize conversation data
    context.user_data["pending_case"] = None
    context.user_data["preview_uuid"] = None
    context.user_data["images"] = []
    context.user_data["caso_confirmed_replace"] = False
    context.user_data["published"] = False

    await update.message.reply_text(
        "📝 Envía el caso clínico completo (viñeta + opciones + CORRECTA + justificación + tip + bibliografía)",
        reply_markup=_admin_keyboard(),
    )
    return STATE_CASE_MODE


async def case_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input in case mode."""
    if not update.message:
        return STATE_CASE_MODE
    try:
        # Show typing indicator while parsing
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

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

        # Store parsed case in context (strip parser-only fields that aren't DB columns)
        case_dict = parsed.to_dict()
        for key in ("errors", "parsed_ok", "raw_text"):
            case_dict.pop(key, None)
        context.user_data["pending_case"] = case_dict
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
            preview += "📸 Fotos para agregar imágenes"
            buttons = [
                [
                    InlineKeyboardButton("👁️ Preview", callback_data="action_preview"),
                    InlineKeyboardButton("📢 Publicar", callback_data="action_publicar"),
                ]
            ]
            score_msg = await update.message.reply_text(
                preview, reply_markup=InlineKeyboardMarkup(buttons)
            )
            # Save score message ID so edited_message_handler can update it in-place
            context.user_data["score_message_id"] = score_msg.message_id
            context.user_data["score_chat_id"] = update.effective_chat.id
        else:
            missing = [name for name, ok in checks if not ok]
            preview += f"⚠️ Falta: {', '.join(missing)}\n📝 Envía el caso de nuevo con los campos faltantes"
            score_msg = await update.message.reply_text(preview)
            context.user_data["score_message_id"] = score_msg.message_id
            context.user_data["score_chat_id"] = update.effective_chat.id
        return STATE_WAITING_IMAGES

    except Exception as e:
        logger.error(f"Error processing case: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return STATE_CASE_MODE


async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle photo uploads in waiting_images state."""
    if not update.message:
        return STATE_WAITING_IMAGES
    try:
        if not update.message.photo:
            return STATE_WAITING_IMAGES

        logger.info(f"Photo received from user {update.effective_user.id}")

        # Guard: ensure pending_case exists
        pending = context.user_data.get("pending_case")
        if not pending:
            await update.message.reply_text(
                "⚠️ No hay caso en preparación. Usa /caso primero y luego envía las fotos."
            )
            return STATE_WAITING_IMAGES

        # Immediate feedback: show upload indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

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
    """Handle documents: accept image files at full resolution, warn for other types."""
    if not update.message:
        return STATE_WAITING_IMAGES

    doc = update.message.document
    if not doc:
        return STATE_WAITING_IMAGES

    # Check if the document is an image (sent as file for full quality)
    mime = doc.mime_type or ""
    logger.info(f"Document received: mime={mime}, filename={doc.file_name}, from user {update.effective_user.id}")

    if mime.startswith("image/"):
        try:
            # Guard: ensure pending_case exists
            pending = context.user_data.get("pending_case")
            if not pending:
                await update.message.reply_text(
                    "⚠️ No hay caso en preparación. Usa /caso primero y luego envía las fotos."
                )
                return STATE_WAITING_IMAGES

            # Immediate feedback: show upload indicator
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

            file = await context.bot.get_file(doc.file_id)
            doc_bytes = await file.download_as_bytearray()

            # Determine extension from mime type
            ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}
            ext = ext_map.get(mime, "jpg")
            filename = f"photo_{doc.file_id}.{ext}"

            image_url = supabase.upload_image(bytes(doc_bytes), filename)

            if not image_url:
                await update.message.reply_text("❌ Error al subir la imagen. Intenta de nuevo.")
                return STATE_WAITING_IMAGES

            # Add to pending case
            if "images" not in pending:
                pending["images"] = []
            pending["images"].append(image_url)

            image_count = len(context.user_data["pending_case"]["images"])
            await update.message.reply_text(
                f"🖼️ Imagen {image_count} agregada (calidad original). Envía más o /publicar"
            )
            return STATE_WAITING_IMAGES

        except Exception as e:
            logger.error(f"Error handling image document: {e}")
            await update.message.reply_text(f"❌ Error al procesar la imagen: {str(e)}")
            return STATE_WAITING_IMAGES

    # Not an image document — warn
    await update.message.reply_text(
        "⚠️ Enviaste un archivo que no es imagen. Para agregar imágenes, envíalas como FOTO o como archivo de imagen.\n"
        "Envía fotos o /publicar para continuar."
    )
    return STATE_WAITING_IMAGES


async def waiting_images_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle unexpected text in WAITING_IMAGES state."""
    if not update.message:
        return STATE_WAITING_IMAGES
    await update.message.reply_text(
        "⚠️ Ya hay un caso cargado. Opciones:\n\n"
        "📸 Envía fotos para agregar imágenes\n"
        "👁️ /preview → Ver en la Mini App\n"
        "📢 /publicar → Publicar en el canal\n"
        "✏️ /editar → Editar secciones\n"
        "🔄 /caso → Reemplazar con otro caso\n"
        "🗑️ /cancelar → Cancelar todo"
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
    if not update.message:
        return STATE_EDIT_TIP
    context.user_data["pending_case"]["tip"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Tip actualizado.\n\n"
        "📸 Fotos | /preview | /publicar."
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
    if not update.message:
        return STATE_EDIT_JUSTIFICATION
    context.user_data["pending_case"]["justification"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Justificación actualizada.\n\n"
        "📸 Fotos | /preview | /publicar."
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
    if not update.message:
        return STATE_EDIT_BIB
    lines = [l.strip() for l in update.message.text.strip().split("\n") if l.strip()]
    # Clean prefixes like "- ", "* ", "1. " etc.
    import re as _re
    cleaned = []
    for line in lines:
        cleaned.append(_re.sub(r"^[\d]+[.)]\s*|^[-*•]\s*", "", line).strip())
    context.user_data["pending_case"]["bibliography"] = [r for r in cleaned if r]
    await update.message.reply_text(
        f"✅ Bibliografía actualizada ({len(cleaned)} refs).\n\n"
        "📸 Fotos | /preview | /publicar."
    )
    return STATE_WAITING_IMAGES


# ═══════════════════════════════════════════
# EDIT PUBLISHED CASE FLOW
# ═══════════════════════════════════════════

async def editar_caso_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the edit published case flow. Ask for case display number."""
    if not _is_admin(update.effective_user.id):
        return ConversationHandler.END

    # Check if there's already a case being created
    pending = context.user_data.get("pending_case")
    if pending:
        await update.message.reply_text(
            "⚠️ Ya tienes un caso en preparación. Usa /cancelar primero o /publicar."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "✏️ **Editar caso publicado**\n\n"
        "Envía el número del caso (el # que aparece en la Mini App).\n"
        "Ejemplo: `2184`\n\n"
        "O /cancelar para salir.",
        parse_mode="Markdown",
    )
    return STATE_EDIT_PUBLISHED_NUMBER


async def edit_published_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the display number and look up the case in Supabase."""
    if not update.message:
        return STATE_EDIT_PUBLISHED_NUMBER

    text = update.message.text.strip()

    # Ignore known keyboard button texts (they're not case numbers)
    _known_buttons = {"caso", "preview", "publicar", "cancelar", "editar", "editar caso", "admin"}
    if text.lower() in _known_buttons:
        await update.message.reply_text(
            "⚠️ Estás en modo edición de caso publicado.\n"
            "Envía el número del caso o /cancelar para salir."
        )
        return STATE_EDIT_PUBLISHED_NUMBER

    text = text.replace("#", "")

    try:
        display_num = int(text)
    except ValueError:
        await update.message.reply_text(
            "❌ Eso no es un número válido. Envía solo el número, ej: `2184`",
            parse_mode="Markdown",
        )
        return STATE_EDIT_PUBLISHED_NUMBER

    # First try to find by display_number field (for new cases)
    try:
        result = supabase.client.table("cases").select("*").eq("display_number", display_num).eq("published", True).execute()

        if not result.data:
            # Fallback: search all published cases and compute hash
            all_cases = supabase.client.table("cases").select("id,vignette,correct_letter,correct_text,justification,tip,bibliography,images").eq("published", True).execute()
            found = None
            for case in (all_cases.data or []):
                if case_display_num(case["id"]) == display_num:
                    found = case
                    break

            if not found:
                await update.message.reply_text(
                    f"❌ No se encontró ningún caso publicado con el número #{display_num}.\n"
                    "Verifica el número e intenta de nuevo, o /cancelar."
                )
                return STATE_EDIT_PUBLISHED_NUMBER

            case_data = found
        else:
            case_data = result.data[0]

        # Store the case being edited
        context.user_data["editing_case_uuid"] = case_data["id"]
        context.user_data["editing_display_num"] = display_num

        # Show current case info
        vig_preview = (case_data.get("vignette") or "")[:100].replace('\n', ' ')
        just_preview = (case_data.get("justification") or "")[:150].replace('\n', ' ')
        tip_preview = (case_data.get("tip") or "(sin tip)")[:100].replace('\n', ' ')
        bib_count = len(case_data.get("bibliography") or [])

        await update.message.reply_text(
            f"📋 **Caso #{display_num}** encontrado\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Viñeta: {vig_preview}...\n\n"
            f"✅ Correcta: {case_data.get('correct_letter', '?')}\n\n"
            f"📖 Justificación: {just_preview}...\n\n"
            f"💡 Tip: {tip_preview}\n\n"
            f"📚 Bibliografía: {bib_count} refs\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            "Ahora envíame el **caso completo** con el mismo formato de siempre "
            "(viñeta, opciones, correcta, justificación, tip, bibliografía).\n\n"
            "El sistema parseará todo y te mostrará un resumen antes de confirmar.\n\n"
            "O /cancelar para salir.",
            parse_mode="Markdown",
        )
        return STATE_EDIT_PUBLISHED_CASE

    except Exception as e:
        logger.error(f"Error looking up case by display number: {e}")
        await update.message.reply_text(f"❌ Error al buscar el caso: {str(e)}")
        return STATE_EDIT_PUBLISHED_NUMBER


async def edit_published_case_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the full case text, parse it, and ask for confirmation."""
    if not update.message:
        return STATE_EDIT_PUBLISHED_CASE

    raw_text = update.message.text.strip()

    # Ignore known keyboard button texts
    _known_buttons = {"caso", "preview", "publicar", "cancelar", "editar", "editar caso", "admin"}
    if raw_text.lower() in _known_buttons:
        await update.message.reply_text(
            "⚠️ Estás editando un caso publicado.\n"
            "Envía el caso completo o /cancelar para salir."
        )
        return STATE_EDIT_PUBLISHED_CASE

    if len(raw_text) < 50:
        await update.message.reply_text(
            "⚠️ El texto es muy corto. Envía el caso completo."
        )
        return STATE_EDIT_PUBLISHED_CASE

    try:
        parsed = parse_case(raw_text)
        is_valid, errors = validate_case(parsed)

        display_num = context.user_data.get("editing_display_num", "?")

        # Build the case data dict
        new_case = {
            "vignette": parsed.vignette,
            "options": parsed.options,
            "correct_letter": parsed.correct_letter,
            "correct_text": "",
            "justification": parsed.justification,
            "tip": parsed.tip or "",
            "bibliography": parsed.bibliography,
        }
        # Get correct text from options
        for opt in parsed.options:
            if opt["letter"] == parsed.correct_letter:
                new_case["correct_text"] = opt["text"]
                break

        context.user_data["editing_new_case"] = new_case

        # Show parsed summary
        checks = [
            ("Viñeta", bool(parsed.vignette)),
            ("Opciones", len(parsed.options) >= 2),
            ("Correcta", bool(parsed.correct_letter)),
            ("Justificación", bool(parsed.justification)),
        ]
        passed = sum(1 for _, ok in checks if ok)
        total = len(checks)
        score_bar = "".join("🟢" if ok else "🔴" for _, ok in checks)

        vig_preview = parsed.vignette[:90].replace('\n', ' ')
        opt_letters = ', '.join(opt['letter'] for opt in parsed.options)
        just_preview = (parsed.justification or "")[:100].replace('\n', ' ')
        tip_preview = (parsed.tip or "(vacío)")[:80].replace('\n', ' ')

        preview_text = (
            f"✏️ **Editar Caso #{display_num}**\n"
            f"{passed}/{total} {score_bar}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Viñeta: {vig_preview}...\n\n"
            f"🔤 Opciones ({len(parsed.options)}): {opt_letters}\n\n"
            f"✅ Correcta: {parsed.correct_letter}\n\n"
            f"📖 Justificación: {just_preview}...\n\n"
            f"💡 Tip: {tip_preview}\n\n"
            f"📚 Bibliografía: {len(parsed.bibliography)} refs\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        if passed == total:
            preview_text += "¿Confirmas la actualización?"
            buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirmar", callback_data="edit_pub_confirm"),
                    InlineKeyboardButton("❌ Cancelar", callback_data="edit_pub_cancel"),
                ]
            ])
            await update.message.reply_text(preview_text, parse_mode="Markdown", reply_markup=buttons)
            return STATE_EDIT_PUBLISHED_CONFIRM
        else:
            missing = [name for name, ok in checks if not ok]
            preview_text += f"⚠️ Falta: {', '.join(missing)}\nEnvía el caso de nuevo completo."
            await update.message.reply_text(preview_text, parse_mode="Markdown")
            return STATE_EDIT_PUBLISHED_CASE

    except Exception as e:
        logger.error(f"Error parsing edited case: {e}")
        await update.message.reply_text(f"❌ Error al parsear el caso: {str(e)}\nIntenta de nuevo.")
        return STATE_EDIT_PUBLISHED_CASE


async def edit_published_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirm/cancel buttons for editing published case."""
    query = update.callback_query

    if query.data == "edit_pub_cancel":
        await query.answer("Edición cancelada")
        await query.edit_message_text("🗑️ Edición cancelada.")
        context.user_data.pop("editing_case_uuid", None)
        context.user_data.pop("editing_display_num", None)
        context.user_data.pop("editing_new_case", None)
        return ConversationHandler.END

    if query.data == "edit_pub_confirm":
        await query.answer("⏳ Actualizando caso...")

        case_uuid = context.user_data.get("editing_case_uuid")
        new_case = context.user_data.get("editing_new_case")
        display_num = context.user_data.get("editing_display_num")

        if not case_uuid or not new_case:
            await query.edit_message_text("❌ Error: datos de edición perdidos.")
            return ConversationHandler.END

        try:
            # Update the case in Supabase
            supabase.update_case(case_uuid, new_case)

            await query.edit_message_text(
                f"✅ **Caso #{display_num}** actualizado exitosamente.\n\n"
                "La Mini App ya mostrará la nueva versión.",
                parse_mode="Markdown",
            )

            logger.info(f"Published case {case_uuid} (#{display_num}) updated successfully")

            # Clean up
            context.user_data.pop("editing_case_uuid", None)
            context.user_data.pop("editing_display_num", None)
            context.user_data.pop("editing_new_case", None)
            return ConversationHandler.END

        except Exception as e:
            logger.error(f"Error updating published case: {e}")
            await query.edit_message_text(f"❌ Error al actualizar: {str(e)}")
            return ConversationHandler.END

    await query.answer()
    return STATE_EDIT_PUBLISHED_CONFIRM


async def edit_published_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the edit published flow."""
    context.user_data.pop("editing_case_uuid", None)
    context.user_data.pop("editing_display_num", None)
    context.user_data.pop("editing_new_case", None)
    await update.message.reply_text(
        "🗑️ Edición cancelada.",
        reply_markup=_admin_keyboard(),
    )
    return ConversationHandler.END


async def action_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle inline button callbacks for Preview and Publicar.
    Uses query.answer() for toast notifications (top bar) instead of messages."""
    query = update.callback_query

    if query.data == "action_preview":
        pending_case = context.user_data.get("pending_case")
        if not pending_case:
            await query.answer("❌ No hay caso pendiente", show_alert=True)
            return STATE_WAITING_IMAGES
        await query.answer("⏳ Generando preview...")
        return await _do_preview(query, context)

    elif query.data == "action_publicar":
        if context.user_data.get("published"):
            await query.answer("✅ Ya fue publicado. Usa /caso para otro", show_alert=True)
            return ConversationHandler.END
        pending_case = context.user_data.get("pending_case")
        if not pending_case:
            await query.answer("❌ No hay caso pendiente", show_alert=True)
            return ConversationHandler.END
        await query.answer("⏳ Publicando en el canal...")
        return await _do_publicar(query, context)

    await query.answer()
    return STATE_WAITING_IMAGES


async def _do_preview(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Preview logic called from inline button. Edits the original message in-place."""
    pending_case = context.user_data.get("pending_case")

    try:
        miniapp_short_name = os.getenv("MINIAPP_SHORT_NAME", "justificacion")

        # Save case to Supabase (unpublished) for preview
        preview_uuid = context.user_data.get("preview_uuid")
        if preview_uuid:
            supabase.update_case(preview_uuid, pending_case)
        else:
            preview_uuid = supabase.save_case(pending_case)
            context.user_data["preview_uuid"] = preview_uuid

        if not preview_uuid:
            return STATE_WAITING_IMAGES

        # Edit the SAME message buttons only - no new message
        preview_url = f"https://t.me/{context.bot.username}/{miniapp_short_name}?startapp={preview_uuid}"
        new_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="👁️ VER PREVIEW",
                        url=preview_url,
                    )
                ],
                [
                    InlineKeyboardButton("📢 Publicar", callback_data="action_publicar"),
                    InlineKeyboardButton("🔄 Actualizar", callback_data="action_preview"),
                ],
            ]
        )

        try:
            await query.edit_message_reply_markup(reply_markup=new_keyboard)
        except Exception as edit_err:
            logger.warning(f"Could not edit message buttons: {edit_err}")
        return STATE_WAITING_IMAGES
    except Exception as e:
        logger.error(f"Error creating preview from button: {e}")
        return STATE_WAITING_IMAGES


async def _do_publicar(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Publicar logic called from inline button. Edits message in-place."""
    # Mark as published IMMEDIATELY to prevent double-click
    context.user_data["published"] = True

    try:
        pending_case = context.user_data.get("pending_case")
        case_number = supabase.get_next_case_number()
        case_uuid = context.user_data.get("preview_uuid")
        if case_uuid:
            supabase.update_case(case_uuid, pending_case)
        else:
            case_uuid = supabase.save_case(pending_case)
        if not case_uuid:
            context.user_data["published"] = False
            return STATE_WAITING_IMAGES

        vignette = pending_case["vignette"]
        options = pending_case["options"]
        poll_question = vignette
        if len(vignette) > 290:
            await context.bot.send_message(
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

        correct_letter = pending_case["correct_letter"]
        correct_index = next(
            (i for i, opt in enumerate(options) if opt["letter"] == correct_letter),
            0,
        )

        # Use TIP for quiz explanation tooltip (lightbulb icon), fallback to justification
        tip_text = pending_case.get("tip", "")
        explanation = f"💡 {tip_text[:195]}" if tip_text else pending_case["justification"][:200]
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
        supabase.update_case(
            case_uuid,
            {"telegram_message_id": poll_msg.message_id, "published": True, "display_number": case_display_num(case_uuid)},
        )

        # Edit the original message to show confirmation (no new message)
        try:
            await query.edit_message_text(
                f"✅ Caso #{case_number} publicado exitosamente.",
            )
        except Exception:
            pass

        context.user_data.clear()
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error publishing case from button: {e}")
        context.user_data["published"] = False
        return STATE_WAITING_IMAGES


async def preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /preview command - save case to DB and show Mini App preview."""
    if not _is_admin(update.effective_user.id):
        return STATE_WAITING_IMAGES

    try:
        pending_case = context.user_data.get("pending_case")
        if not pending_case:
            await update.message.reply_text("❌ No hay caso en preparación.")
            return STATE_WAITING_IMAGES

        # Show typing indicator while generating preview
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Save case to Supabase (unpublished) for preview
        preview_uuid = context.user_data.get("preview_uuid")
        if preview_uuid:
            # Update existing preview
            supabase.update_case(preview_uuid, pending_case)
        else:
            # Save new preview
            preview_uuid = supabase.save_case(pending_case)
            context.user_data["preview_uuid"] = preview_uuid

        if not preview_uuid:
            await update.message.reply_text("❌ Error al guardar preview.")
            return STATE_WAITING_IMAGES

        # Send Mini App preview button
        miniapp_short_name = Config.MINIAPP_SHORT_NAME
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="👁️ VER PREVIEW",
                        url=f"https://t.me/{context.bot.username}/{miniapp_short_name}?startapp={preview_uuid}",
                    )
                ]
            ]
        )

        await update.message.reply_text(
            "👁️ Preview guardado. Abre la Mini App para ver cómo queda:\n\n"
            "Cuando estés listo: /publicar\n"
            "Para editar: /editar",
            reply_markup=keyboard,
        )
        return STATE_WAITING_IMAGES

    except Exception as e:
        logger.error(f"Error creating preview: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return STATE_WAITING_IMAGES


async def publicar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /publicar command (admin only) - publish case as quiz poll."""
    if not _is_admin(update.effective_user.id):
        return ConversationHandler.END

    # Double-publish protection
    if context.user_data.get("published"):
        await update.message.reply_text("✅ Este caso ya fue publicado. Usa /caso para crear uno nuevo.")
        return ConversationHandler.END

    try:
        pending_case = context.user_data.get("pending_case")
        if not pending_case:
            await update.message.reply_text("❌ No hay un caso en preparación.")
            return ConversationHandler.END

        # Show typing indicator while publishing
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Mark as published IMMEDIATELY to prevent race conditions
        context.user_data["published"] = True

        # Get case number BEFORE saving (so count is accurate)
        case_number = supabase.get_next_case_number()

        # Reuse preview UUID if it exists, otherwise save new
        case_uuid = context.user_data.get("preview_uuid")
        if case_uuid:
            # Update existing preview case with latest data
            supabase.update_case(case_uuid, pending_case)
        else:
            # Save new case to Supabase
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

        # Use TIP for quiz explanation tooltip (lightbulb icon), fallback to justification
        tip_text = pending_case.get("tip", "")
        explanation = f"💡 {tip_text[:195]}" if tip_text else pending_case["justification"][:200]

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
                "display_number": case_display_num(case_uuid),
            },
        )

        # Confirm to admin
        await update.message.reply_text(
            f"✅ Caso #{case_number} publicado en el canal.\n"
            f"UUID: `{case_uuid}`",
            parse_mode="Markdown",
            reply_markup=_admin_keyboard(),
        )

        # Clear user data
        context.user_data.clear()
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error publishing case: {e}")
        await update.message.reply_text(f"❌ Error al publicar el caso: {str(e)}")
        return STATE_WAITING_IMAGES


async def cancelar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancelar command - cancel case creation and clean up preview."""
    preview_uuid = context.user_data.get("preview_uuid")
    pending = context.user_data.get("pending_case")

    if not pending and not preview_uuid:
        await update.message.reply_text("ℹ️ No hay caso pendiente para cancelar.")
        return ConversationHandler.END

    # Delete orphan preview from Supabase if it exists
    if preview_uuid:
        try:
            supabase.delete_case(preview_uuid)
            logger.info(f"Cleaned up preview {preview_uuid} on cancel")
        except Exception as e:
            logger.warning(f"Could not delete preview {preview_uuid}: {e}")
    context.user_data.clear()
    await update.message.reply_text(
        "🗑️ Caso cancelado y eliminado.",
        reply_markup=_admin_keyboard(),
    )
    return ConversationHandler.END


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin command - show admin commands."""
    if not _is_admin(update.effective_user.id):
        return  # Silently ignore for non-admins

    admin_text = (
        "🔧 <b>Comandos de Administrador:</b>\n\n"
        "/caso - Crear caso clínico\n"
        "/preview - Ver cómo queda en la Mini App\n"
        "/publicar - Publicar en el canal\n"
        "/editar - Editar secciones del caso\n"
        "/editar_caso - Editar caso ya publicado\n"
        "/cancelar - Cancelar\n"
        "/admin - Ver este menú\n\n"
        "<b>Flujo:</b>\n"
        "1. /caso → Pega el caso completo\n"
        "2. (Opcional) Envía fotos\n"
        "3. /preview → Revisa en la Mini App\n"
        "4. /publicar → Se envía al canal\n"
    )
    await update.message.reply_text(
        admin_text,
        parse_mode="HTML",
        reply_markup=_admin_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    import random
    help_messages = [
        (
            "🎓 **ACAMEDICS — Medicina académica**\n\n"
            "Casos clínicos con justificación completa, "
            "tips acamédicos y bibliografía basada en evidencia.\n\n"
            "Responde en el canal y toca 'VER JUSTIFICACIÓN' "
            "para descubrir el análisis completo.\n\n"
            "📖 Aprende. Practica. Domina la clínica."
        ),
        (
            "🩺 **¿Cómo funciona ACAMEDICS?**\n\n"
            "1. Lee el caso clínico en el canal\n"
            "2. Elige tu respuesta\n"
            "3. Toca 'VER JUSTIFICACIÓN' para ver la explicación\n\n"
            "💡 Cada caso incluye justificación detallada, "
            "tips acamédicos y bibliografía actualizada."
        ),
        (
            "📚 **ACAMEDICS — Basado en evidencia**\n\n"
            "Nuestros casos clínicos están diseñados para "
            "fortalecer tu razonamiento clínico.\n\n"
            "Entra al canal, responde y "
            "revisa la justificación completa. "
            "¡Tu conocimiento se construye caso a caso!"
        ),
    ]
    await update.message.reply_text(
        random.choice(help_messages),
        parse_mode="Markdown",
    )


def _is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return user_id in Config.ADMIN_USER_IDS


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in the bot."""
    # Ignore Conflict errors (happen during deploys when two instances overlap)
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logger.warning(f"Conflict error (normal during deploy): {context.error}")
        return

    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # Try to send error message to admin
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Algo salió mal. Intenta de nuevo en unos segundos."
            )
        except Exception as e:
            logger.error(f"Could not send error message: {e}")


async def post_init(application) -> None:
    """Set bot commands menu after initialization."""
    from telegram import BotCommand, BotCommandScopeChat

    # Public commands - visible to everyone
    public_commands = [
        BotCommand("start", "🎓 Iniciar"),
        BotCommand("help", "ℹ️ Ayuda"),
    ]
    await application.bot.set_my_commands(public_commands)

    # Admin commands - visible only to admin users
    admin_commands = [
        BotCommand("caso", "📝 Crear caso clínico"),
        BotCommand("preview", "👁️ Ver preview en Mini App"),
        BotCommand("publicar", "📢 Publicar en el canal"),
        BotCommand("editar", "✏️ Editar secciones del caso"),
        BotCommand("editar_caso", "📝 Editar caso publicado"),
        BotCommand("cancelar", "🗑️ Cancelar caso pendiente"),
        BotCommand("admin", "🔧 Panel de administrador"),
        BotCommand("help", "ℹ️ Ayuda"),
    ]
    for admin_id in Config.ADMIN_USER_IDS:
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
            logger.info(f"Admin commands set for user {admin_id}")
        except Exception as e:
            logger.warning(f"Could not set admin commands for {admin_id}: {e}")

    logger.info("Bot commands menu registered")


def main() -> None:
    """Main entry point for the bot."""
    global supabase, app

    try:
        # Initialize Supabase
        supabase = init_supabase(Config.SUPABASE_URL, Config.SUPABASE_KEY, Config.SUPABASE_SERVICE_KEY)
        logger.info("Supabase initialized")

        # Create bot application with post_init for command menu
        app = Application.builder().token(Config.BOT_TOKEN).post_init(post_init).build()

        # Register handlers
        # Start command
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("admin", admin_command))

        # Regex filter for keyboard button texts (without slash)
        _BTN_CASO = filters.Regex(r"(?i)^caso$") & ~filters.UpdateType.EDITED_MESSAGE
        _BTN_PREVIEW = filters.Regex(r"(?i)^preview$") & ~filters.UpdateType.EDITED_MESSAGE
        _BTN_PUBLICAR = filters.Regex(r"(?i)^publicar$") & ~filters.UpdateType.EDITED_MESSAGE
        _BTN_CANCELAR = filters.Regex(r"(?i)^cancelar$") & ~filters.UpdateType.EDITED_MESSAGE
        _BTN_EDITAR = filters.Regex(r"(?i)^editar$") & ~filters.UpdateType.EDITED_MESSAGE

        # Case creation conversation
        case_conv = ConversationHandler(
            entry_points=[
                CommandHandler("caso", caso_command),
                MessageHandler(_BTN_CASO, caso_command),
            ],
            states={
                STATE_CASE_MODE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, case_text_handler),
                ],
                STATE_WAITING_IMAGES: [
                    CallbackQueryHandler(action_button_callback, pattern="^action_"),
                    MessageHandler(filters.PHOTO & ~filters.UpdateType.EDITED_MESSAGE, image_handler),
                    MessageHandler(filters.Document.ALL & ~filters.UpdateType.EDITED_MESSAGE, document_warning_handler),
                    # Slash commands
                    CommandHandler("caso", caso_command),
                    CommandHandler("publicar", publicar_command),
                    CommandHandler("preview", preview_command),
                    CommandHandler("editar", editar_command),
                    CommandHandler("editar_tip", editar_tip_command),
                    CommandHandler("editar_just", editar_just_command),
                    CommandHandler("editar_bib", editar_bib_command),
                    CommandHandler("cancelar", cancelar_command),
                    # Keyboard button texts (without slash) - BEFORE generic text handler
                    MessageHandler(_BTN_CASO, caso_command),
                    MessageHandler(_BTN_PREVIEW, preview_command),
                    MessageHandler(_BTN_PUBLICAR, publicar_command),
                    MessageHandler(_BTN_CANCELAR, cancelar_command),
                    MessageHandler(_BTN_EDITAR, editar_command),
                    # Generic text handler (catch-all) - MUST be last
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, waiting_images_text_handler),
                ],
                STATE_EDIT_TIP: [
                    CommandHandler("cancelar", cancelar_command),
                    MessageHandler(_BTN_CANCELAR, cancelar_command),
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, edit_tip_handler),
                ],
                STATE_EDIT_JUSTIFICATION: [
                    CommandHandler("cancelar", cancelar_command),
                    MessageHandler(_BTN_CANCELAR, cancelar_command),
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, edit_just_handler),
                ],
                STATE_EDIT_BIB: [
                    CommandHandler("cancelar", cancelar_command),
                    MessageHandler(_BTN_CANCELAR, cancelar_command),
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, edit_bib_handler),
                ],
            },
            fallbacks=[
                CommandHandler("cancelar", cancelar_command),
                MessageHandler(_BTN_CANCELAR, cancelar_command),
            ],
            per_user=True,
        )
        app.add_handler(case_conv)

        # Edit published case conversation
        _BTN_EDITAR_CASO = filters.Regex(r"(?i)^editar\s*caso$") & ~filters.UpdateType.EDITED_MESSAGE
        edit_pub_conv = ConversationHandler(
            entry_points=[
                CommandHandler("editar_caso", editar_caso_command),
                MessageHandler(_BTN_EDITAR_CASO, editar_caso_command),
            ],
            states={
                STATE_EDIT_PUBLISHED_NUMBER: [
                    CommandHandler("cancelar", edit_published_cancelar),
                    MessageHandler(_BTN_CANCELAR, edit_published_cancelar),
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, edit_published_number_handler),
                ],
                STATE_EDIT_PUBLISHED_CASE: [
                    CommandHandler("cancelar", edit_published_cancelar),
                    MessageHandler(_BTN_CANCELAR, edit_published_cancelar),
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, edit_published_case_handler),
                ],
                STATE_EDIT_PUBLISHED_CONFIRM: [
                    CallbackQueryHandler(edit_published_confirm_callback, pattern="^edit_pub_"),
                    CommandHandler("cancelar", edit_published_cancelar),
                    MessageHandler(_BTN_CANCELAR, edit_published_cancelar),
                ],
            },
            fallbacks=[
                CommandHandler("cancelar", edit_published_cancelar),
                MessageHandler(_BTN_CANCELAR, edit_published_cancelar),
            ],
            per_user=True,
        )
        app.add_handler(edit_pub_conv)

        # Standalone fallback handlers for commands/buttons used outside conversation
        async def fallback_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text("ℹ️ No hay caso pendiente para cancelar.")

        async def fallback_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text("ℹ️ No hay caso pendiente. Usa /caso para empezar.")

        async def fallback_publicar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text("ℹ️ No hay caso pendiente. Usa /caso para empezar.")

        # Slash command fallbacks
        app.add_handler(CommandHandler("cancelar", fallback_cancelar))
        app.add_handler(CommandHandler("preview", fallback_preview))
        app.add_handler(CommandHandler("publicar", fallback_publicar))
        # Keyboard button text fallbacks (without slash)
        app.add_handler(MessageHandler(_BTN_CANCELAR, fallback_cancelar))
        app.add_handler(MessageHandler(_BTN_PREVIEW, fallback_preview))
        app.add_handler(MessageHandler(_BTN_PUBLICAR, fallback_publicar))
        app.add_handler(MessageHandler(filters.Regex(r"(?i)^admin$"), admin_command))

        # Fallback handlers for photos/documents sent outside conversation
        # (e.g., after bot restart when ConversationHandler state is lost)
        async def fallback_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_admin(update.effective_user.id):
                return
            logger.info(f"Photo received outside conversation from user {update.effective_user.id}")
            await update.message.reply_text(
                "📸 Recibí tu imagen, pero no hay un caso activo.\n"
                "Usa /caso para crear un caso primero, luego envía las fotos."
            )

        async def fallback_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_admin(update.effective_user.id):
                return
            doc = update.message.document
            if doc and (doc.mime_type or "").startswith("image/"):
                logger.info(f"Image document received outside conversation from user {update.effective_user.id}")
                await update.message.reply_text(
                    "📸 Recibí tu imagen, pero no hay un caso activo.\n"
                    "Usa /caso para crear un caso primero, luego envía las fotos."
                )

        app.add_handler(MessageHandler(filters.PHOTO & ~filters.UpdateType.EDITED_MESSAGE, fallback_photo))
        app.add_handler(MessageHandler(filters.Document.ALL & ~filters.UpdateType.EDITED_MESSAGE, fallback_document))

        # Handler for EDITED messages - re-parse the case when admin edits their message
        async def edited_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Handle edited messages - re-parse case if admin edits their case text."""
            if not update.edited_message or not update.edited_message.text:
                return
            if not _is_admin(update.edited_message.from_user.id):
                return

            # Only re-parse if we have a pending case (user is in case creation flow)
            pending = context.user_data.get("pending_case")
            if not pending:
                return

            text = update.edited_message.text
            # Don't process commands
            if text.startswith("/"):
                return

            try:
                parsed = parse_case(text)
                if not parsed.parsed_ok:
                    await update.edited_message.reply_text(
                        "⚠️ Caso editado detectado pero tiene errores:\n"
                        + "\n".join(f"• {e}" for e in parsed.errors[:3])
                    )
                    return

                # Update the pending case with new parsed data (strip parser-only fields)
                old_images = context.user_data.get("pending_case", {}).get("images", [])
                case_dict = parsed.to_dict()
                for key in ("errors", "parsed_ok", "raw_text"):
                    case_dict.pop(key, None)
                context.user_data["pending_case"] = case_dict
                context.user_data["pending_case"]["images"] = old_images

                # Update preview in Supabase if exists
                preview_uuid = context.user_data.get("preview_uuid")
                if preview_uuid:
                    supabase.update_case(preview_uuid, context.user_data["pending_case"])

                # Build score
                checks = [
                    ("Viñeta", bool(parsed.vignette)),
                    ("Opciones", len(parsed.options) >= 2),
                    ("Correcta", bool(parsed.correct_letter)),
                    ("Justificación", bool(parsed.justification)),
                    ("Tip", bool(parsed.tip)),
                    ("Bibliografía", len(parsed.bibliography) > 0),
                ]
                passed = sum(1 for _, ok in checks if ok)
                total = len(checks)
                score_bar = "".join("🟢" if ok else "🔴" for _, ok in checks)
                score_emoji = "✅" if passed == total else "⚠️"

                preview_status = ""
                if preview_uuid:
                    preview_status = "\n🔄 Preview actualizado automáticamente"

                buttons = [
                    [
                        InlineKeyboardButton("👁️ Preview", callback_data="action_preview"),
                        InlineKeyboardButton("📢 Publicar", callback_data="action_publicar"),
                    ]
                ]

                new_text = (
                    f"🔄 Caso actualizado desde edición\n"
                    f"{score_emoji} {passed}/{total} {score_bar}{preview_status}"
                )

                # Try to EDIT the existing score message in-place (no new message)
                score_msg_id = context.user_data.get("score_message_id")
                score_chat_id = context.user_data.get("score_chat_id")
                if score_msg_id and score_chat_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=score_chat_id,
                            message_id=score_msg_id,
                            text=new_text,
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                    except Exception as edit_err:
                        # If edit fails (e.g., text identical), send new message as fallback
                        logger.warning(f"Could not edit score message: {edit_err}")
                        score_msg = await update.edited_message.reply_text(
                            new_text,
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        context.user_data["score_message_id"] = score_msg.message_id
                else:
                    # No existing score message, send new
                    score_msg = await update.edited_message.reply_text(
                        new_text,
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
                    context.user_data["score_message_id"] = score_msg.message_id
                    context.user_data["score_chat_id"] = update.edited_message.chat.id

                # Reset published flag since case changed
                context.user_data["published"] = False

            except Exception as e:
                logger.error(f"Error processing edited message: {e}")

        # Group 1 = runs independently of ConversationHandler (group 0)
        app.add_handler(MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & filters.TEXT & ~filters.COMMAND,
            edited_message_handler,
        ), group=1)

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
        # allowed_updates includes edited_message so bot detects when admin edits case text
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "edited_message", "callback_query"],
        )

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
