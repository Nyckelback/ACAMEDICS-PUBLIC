# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Cache para auto-eliminación
user_justification_messages = {}

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa: /start 22  o  /start jst_22  o  /start 30-31  o  /start just_22
    """
    if not update.message: 
        return
    
    user_id = update.effective_user.id
    
    # Obtener argumento crudo
    raw_arg = ""
    if context.args and len(context.args) > 0:
        raw_arg = context.args[0]
    else:
        # Fallback: extraer del texto
        text = update.message.text or ""
        parts = text.split()
        if len(parts) > 1:
            raw_arg = parts[1]
    
    if not raw_arg:
        logger.warning(f"No se recibió argumento de usuario {user_id}")
        return
    
    logger.info(f"📥 Solicitud de justificación: user={user_id}, arg='{raw_arg}'")
    
    # Limpiar prefijos conocidos
    clean_arg = raw_arg
    for prefix in ['jst_', 'just_', 'j_']:
        clean_arg = clean_arg.replace(prefix, '')
    
    # Verificar formato válido (números, guiones, comas)
    if not re.match(r'^[\d,\-]+$', clean_arg):
        logger.error(f"ID inválido: '{clean_arg}'")
        await update.message.reply_text(f"❌ ID inválido: {clean_arg}")
        return
    
    # Parsear IDs (soporta: 22, 22-25, 22,23,24)
    ids = []
    parts = clean_arg.replace(',', '-').split('-')
    for p in parts:
        p = p.strip()
        if p.isdigit():
            ids.append(int(p))
    
    if not ids:
        logger.error(f"No se pudieron extraer IDs de: '{clean_arg}'")
        await update.message.reply_text("❌ No se encontraron IDs válidos.")
        return
    
    logger.info(f"📋 IDs a buscar: {ids} en canal {JUSTIFICATIONS_CHAT_ID}")
    
    # Mensaje temporal
    processing = await update.message.reply_text("🔄 Buscando contenido...")
    
    sent_msgs = []
    errors = []
    
    for jid in ids:
        try:
            logger.info(f"📤 Copiando mensaje {jid} de canal {JUSTIFICATIONS_CHAT_ID} a usuario {user_id}")
            
            msg = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=jid,
                protect_content=True
            )
            sent_msgs.append(msg.message_id)
            logger.info(f"✅ Mensaje {jid} enviado exitosamente")
            
        except TelegramError as e:
            error_msg = str(e)
            logger.error(f"❌ TelegramError copiando {jid}: {error_msg}")
            
            if "message to copy not found" in error_msg.lower():
                errors.append(f"ID {jid}: No existe en el canal")
            elif "chat not found" in error_msg.lower():
                errors.append(f"ID {jid}: Canal no accesible")
            elif "bot was blocked" in error_msg.lower():
                errors.append(f"Bot bloqueado por el usuario")
            else:
                errors.append(f"ID {jid}: {error_msg}")
                
        except Exception as e:
            logger.exception(f"❌ Error inesperado copiando {jid}: {e}")
            errors.append(f"ID {jid}: Error inesperado")
    
    # Borrar mensaje de procesamiento
    try:
        await processing.delete()
    except:
        pass
    
    # Reportar errores si los hubo
    if errors and not sent_msgs:
        await update.message.reply_text(
            f"❌ **No se pudo entregar:**\n" + "\n".join(errors) +
            f"\n\n💡 Canal configurado: `{JUSTIFICATIONS_CHAT_ID}`",
            parse_mode="Markdown"
        )
        return
    
    if sent_msgs:
        # Mensaje motivacional
        try:
            from justification_messages import get_weighted_random_message
            txt = get_weighted_random_message()
        except:
            txt = "📚 Aquí tienes tu contenido."
        
        txt += f"\n\n⚠️ *Se borra en {AUTO_DELETE_MINUTES} min.*"
        
        if errors:
            txt += f"\n\n⚠️ Algunos no se encontraron: {', '.join([str(e) for e in errors])}"
        
        avis = await context.bot.send_message(user_id, txt, parse_mode="Markdown")
        sent_msgs.append(avis.message_id)
        
        # Guardar para auto-eliminación
        user_justification_messages[user_id] = sent_msgs
        asyncio.create_task(schedule_del(context, user_id))

async def schedule_del(context, user_id):
    """Programa la eliminación automática"""
    await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
    
    if user_id in user_justification_messages:
        for mid in user_justification_messages[user_id]:
            try:
                await context.bot.delete_message(user_id, mid)
            except Exception as e:
                logger.debug(f"No se pudo borrar mensaje {mid}: {e}")
        del user_justification_messages[user_id]
        logger.info(f"🗑️ Justificaciones de usuario {user_id} eliminadas")
