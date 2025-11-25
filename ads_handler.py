# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from typing import Optional, Dict
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS, TZ

logger = logging.getLogger(__name__)

# Almacenamiento de ADS activas
active_ads: Dict[str, Dict] = {}

# Patrón para botones @@@
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para crear nueva publicidad: /set_ads"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Solo administradores")
        return
    
    text = (
        "📢 **Crear Nueva Publicidad**\n\n"
        "**Paso 1:** Envíame el contenido del AD:\n"
        "• Imagen + texto\n"
        "• Video + texto\n"
        "• Solo texto\n\n"
        "**Para botones usa:**\n"
        "`@@@ Texto del botón | URL`\n\n"
        "Envía el contenido ahora o /cancel"
    )
    
    await update.message.reply_text(text, parse_mode="Markdown")
    
    context.user_data['creating_ad'] = True
    context.user_data['ad_step'] = 'content'


async def handle_private_message_for_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes privados durante creación de ADS"""
    if not is_admin(update.effective_user.id):
        return
    
    msg = update.message
    text = msg.text or ""
    
    # Cancelar
    if text.lower() == '/cancel':
        context.user_data.clear()
        await msg.reply_text("❌ Operación cancelada")
        return
    
    # Paso 1: Recibir contenido
    if context.user_data.get('ad_step') == 'content':
        ad_content = {
            'text': msg.text or msg.caption or "",
            'photo': msg.photo[-1].file_id if msg.photo else None,
            'video': msg.video.file_id if msg.video else None,
            'document': msg.document.file_id if msg.document else None,
        }
        
        context.user_data['ad_content'] = ad_content
        context.user_data['ad_step'] = 'interval'
        
        # Detectar botones
        buttons_found = len(BUTTON_PATTERN.findall(ad_content['text']))
        buttons_info = f"\n✅ {buttons_found} botón(es) detectado(s)" if buttons_found else ""
        
        await msg.reply_text(
            f"✅ Contenido guardado{buttons_info}\n\n"
            "**Paso 2:** ¿Cada cuánto publicar?\n\n"
            "Ejemplos:\n"
            "• `10m` → Cada 10 minutos\n"
            "• `1h` → Cada 1 hora\n"
            "• `6h` → Cada 6 horas\n"
            "• `24h` → Cada 24 horas\n\n"
            "Envía el intervalo o /cancel",
            parse_mode="Markdown"
        )
        return
    
    # Paso 2: Recibir intervalo
    if context.user_data.get('ad_step') == 'interval':
        match = re.match(r'(\d+)(m|h)', text.strip().lower())
        
        if not match:
            await msg.reply_text("❌ Formato inválido. Usa: `10m`, `1h`, etc.", parse_mode="Markdown")
            return
        
        value = int(match.group(1))
        unit = match.group(2)
        
        interval_minutes = value * 60 if unit == 'h' else value
        
        if interval_minutes < 5:
            await msg.reply_text("❌ Mínimo 5 minutos")
            return
        
        # Crear AD
        ad_id = f"ad_{datetime.now(tz=TZ).strftime('%Y%m%d_%H%M%S')}"
        ad_content = context.user_data['ad_content']
        
        try:
            # Publicar primer AD
            message_id = await publish_ad(context, ad_content)
            
            if not message_id:
                await msg.reply_text("❌ Error publicando")
                return
            
            # Programar republicación
            task = asyncio.create_task(
                schedule_ad_republish(context, ad_id, ad_content, interval_minutes)
            )
            
            active_ads[ad_id] = {
                'message_id': message_id,
                'content': ad_content,
                'interval_minutes': interval_minutes,
                'task': task,
                'created_at': datetime.now(tz=TZ)
            }
            
            context.user_data.clear()
            
            await msg.reply_text(
                f"✅ **AD creado**\n\n"
                f"🆔 `{ad_id}`\n"
                f"⏱️ Cada {interval_minutes} minutos\n"
                f"🔄 Próximo: En {interval_minutes} min\n\n"
                f"Usa /list_ads o /delete_ads",
                parse_mode="Markdown"
            )
            
            logger.info(f"✅ AD creado: {ad_id}")
            
        except Exception as e:
            await msg.reply_text(f"❌ Error: {e}")


async def publish_ad(context: ContextTypes.DEFAULT_TYPE, ad_content: dict) -> Optional[int]:
    """Publica un AD en el canal público"""
    try:
        text = ad_content['text']
        
        # Procesar botones @@@
        button_matches = BUTTON_PATTERN.findall(text)
        reply_markup = None
        
        if button_matches:
            buttons = []
            for match in button_matches:
                label = match[0].strip()
                url = match[1].strip() if match[1] else None
                
                if label and url:
                    if not url.startswith(('http://', 'https://', 'tg://')):
                        if url.startswith('t.me/'):
                            url = 'https://' + url
                        elif '.' in url:
                            url = 'https://' + url
                    buttons.append(InlineKeyboardButton(label, url=url))
            
            if buttons:
                keyboard = [[b] for b in buttons]  # Un botón por fila
                reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = BUTTON_PATTERN.sub('', text).strip()
        
        # Publicar
        if ad_content['photo']:
            msg = await context.bot.send_photo(
                chat_id=PUBLIC_CHANNEL_ID,
                photo=ad_content['photo'],
                caption=text if text else None,
                reply_markup=reply_markup
            )
        elif ad_content['video']:
            msg = await context.bot.send_video(
                chat_id=PUBLIC_CHANNEL_ID,
                video=ad_content['video'],
                caption=text if text else None,
                reply_markup=reply_markup
            )
        elif ad_content['document']:
            msg = await context.bot.send_document(
                chat_id=PUBLIC_CHANNEL_ID,
                document=ad_content['document'],
                caption=text if text else None,
                reply_markup=reply_markup
            )
        else:
            msg = await context.bot.send_message(
                chat_id=PUBLIC_CHANNEL_ID,
                text=text if text else "📢",
                reply_markup=reply_markup
            )
        
        return msg.message_id
        
    except Exception as e:
        logger.error(f"❌ Error publicando AD: {e}")
        return None


async def schedule_ad_republish(
    context: ContextTypes.DEFAULT_TYPE,
    ad_id: str,
    ad_content: dict,
    interval_minutes: int
):
    """Republica el AD periódicamente, eliminando el anterior"""
    try:
        while True:
            await asyncio.sleep(interval_minutes * 60)
            
            # Eliminar AD anterior
            if ad_id in active_ads:
                old_msg = active_ads[ad_id].get('message_id')
                if old_msg:
                    try:
                        await context.bot.delete_message(
                            chat_id=PUBLIC_CHANNEL_ID,
                            message_id=old_msg
                        )
                    except:
                        pass
            
            # Publicar nuevo
            new_msg = await publish_ad(context, ad_content)
            
            if new_msg and ad_id in active_ads:
                active_ads[ad_id]['message_id'] = new_msg
                logger.info(f"🔄 AD republicado: {ad_id}")
            
    except asyncio.CancelledError:
        logger.info(f"⏹️ AD cancelado: {ad_id}")
    except Exception as e:
        logger.error(f"❌ Error en AD {ad_id}: {e}")


async def cmd_list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista ADs activas"""
    if not is_admin(update.effective_user.id):
        return
    
    if not active_ads:
        await update.message.reply_text("📭 No hay ADs activas")
        return
    
    lines = ["📢 **ADs Activas:**\n"]
    
    for ad_id, info in active_ads.items():
        interval = info['interval_minutes']
        created = info['created_at'].strftime("%H:%M")
        lines.append(f"• `{ad_id}`\n  ⏱️ Cada {interval}min | 📅 {created}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delete_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina una AD"""
    if not is_admin(update.effective_user.id):
        return
    
    if not active_ads:
        await update.message.reply_text("📭 No hay ADs")
        return
    
    keyboard = []
    for ad_id in active_ads.keys():
        keyboard.append([InlineKeyboardButton(f"🗑️ {ad_id}", callback_data=f"ads_del_{ad_id}")])
    
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="ads_cancel")])
    
    await update.message.reply_text(
        "Selecciona el AD a eliminar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_ads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja callbacks de ADS"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    data = query.data
    
    if data.startswith("ads_del_"):
        ad_id = data.replace("ads_del_", "")
        
        if ad_id not in active_ads:
            await query.edit_message_text("❌ AD no encontrada")
            return
        
        # Cancelar tarea
        info = active_ads[ad_id]
        if 'task' in info:
            info['task'].cancel()
        
        # Eliminar mensaje
        try:
            await context.bot.delete_message(
                chat_id=PUBLIC_CHANNEL_ID,
                message_id=info['message_id']
            )
        except:
            pass
        
        del active_ads[ad_id]
        
        await query.edit_message_text(f"✅ AD `{ad_id}` eliminada", parse_mode="Markdown")
        logger.info(f"🗑️ AD eliminada: {ad_id}")
    
    elif data == "ads_cancel":
        await query.edit_message_text("❌ Cancelado")
