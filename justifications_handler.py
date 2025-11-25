# -*- coding: utf-8 -*-
import logging
import asyncio
import re
import random
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Cache para auto-eliminación
user_justification_messages = {}

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa: /start 22  o  /start jst_22  o  /start 30-31
    """
    if not update.message: return
    user_id = update.effective_user.id
    
    # Obtenemos los argumentos crudos
    # Si viene de comando: context.args = ['22'] o ['jst_22']
    # Si viene de regex (legacy): extraemos del texto
    
    raw_arg = ""
    if context.args:
        raw_arg = context.args[0]
    else:
        # Fallback por si acaso
        text = update.message.text
        raw_arg = text.replace("/start", "").strip()

    # Limpiamos prefijos viejos si existen
    clean_arg = raw_arg.replace("jst_", "")
    
    # Verificamos formato (números y guiones)
    if not re.match(r'^[\d-]+$', clean_arg):
        await update.message.reply_text("❌ ID inválido.")
        return

    ids = [int(x) for x in clean_arg.split('-')]
    
    # Mensaje temporal
    processing = await update.message.reply_text("🔄 ...")
    
    sent_msgs = []
    
    for jid in ids:
        try:
            msg = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=jid,
                protect_content=True
            )
            sent_msgs.append(msg.message_id)
        except Exception as e:
            await update.message.reply_text(f"❌ Error buscando ID {jid}")
            logger.error(f"Error copia: {e}")

    await processing.delete()
    
    if sent_msgs:
        # Mensaje motivacional
        try:
            from justification_messages import get_weighted_random_message
            txt = get_weighted_random_message()
        except:
            txt = "📚 Aquí tienes."
            
        txt += f"\n\n⚠️ Se borra en {AUTO_DELETE_MINUTES} min."
        
        avis = await context.bot.send_message(user_id, txt, parse_mode="Markdown")
        sent_msgs.append(avis.message_id)
        
        user_justification_messages[user_id] = sent_msgs
        asyncio.create_task(schedule_del(context, user_id))

async def schedule_del(context, user_id):
    await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
    if user_id in user_justification_messages:
        for mid in user_justification_messages[user_id]:
            try: await context.bot.delete_message(user_id, mid)
            except: pass
        del user_justification_messages[user_id]
