"""
canal_ops — operaciones sobre el canal pedidas desde CUALQUIER máquina (26-sep-2026).

Por qué existe: JP pidió que un caso publicado que se corrige o se elimina se refleje en el
canal. El token del bot vive solo aquí (Render), así que las máquinas de trabajo (la Mac con
Claude, la máquina de Grok) no mandan nada a Telegram: escriben un pedido en la tabla
`telegram_ops` de Supabase y este módulo lo ejecuta en el mismo ciclo de 30 s del scheduler.

Operaciones:
  probar         envía, edita y borra un mensaje en el chat de un admin, y reporta los permisos
                 del bot en el canal (prueba de extremo a extremo sin tocar el canal).
  borrar         borra TODOS los mensajes de una publicación (viñeta, imágenes, encuesta).
  editar_vineta  reescribe el texto de la viñeta publicada. La encuesta NO se puede editar
                 (límite de Telegram): si cambian opciones, respuesta o TIP hay que republicar.

Reglas de seguridad:
  - Nada de esto puede tumbar la publicación: todo va con try/except y el scheduler lo llama
    DESPUÉS de publicar lo programado.
  - Casos publicados antes de este módulo solo tienen el id de la encuesta. Para encontrar su
    viñeta y sus imágenes se VERIFICA el contenido (se reenvía el mensaje al chat del admin
    sin notificación, se compara y se borra la copia). Lo que no se puede verificar NO se borra.
"""

import logging
import re
import unicodedata
from datetime import datetime, timezone

from config import Config

logger = logging.getLogger(__name__)

_aviso_tabla = False
MAX_HACIA_ATRAS = 12          # mensajes a revisar antes de la encuesta (viñeta + hasta ~10 imágenes)


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def _norm(t):
    t = unicodedata.normalize("NFKC", t or "")
    return re.sub(r"\s+", " ", t).strip()


# ─────────────────────────── registrar lo que se publica ───────────────────────────
def registrar_mensajes(supabase, case_id, vineta_msg, imagen_ids, poll_msg):
    """Guarda TODOS los ids de una publicación. Llamada aparte y protegida: si la columna
    aún no existe o Supabase falla, la publicación ya quedó hecha y solo se registra el aviso."""
    try:
        datos = {
            "canal": Config.PUBLIC_CHANNEL_ID,
            "vineta_id": getattr(vineta_msg, "message_id", None),
            "imagen_ids": [i for i in (imagen_ids or []) if i],
            "poll_id": getattr(poll_msg, "message_id", None),
            "publicado": _ahora(),
        }
        supabase.service_client.table("cases").update(
            {"telegram_msgs": datos, "channel_id": Config.PUBLIC_CHANNEL_ID}
        ).eq("id", case_id).execute()
    except Exception as e:  # nunca romper la publicación por esto
        logger.warning(f"No se pudieron registrar los ids de mensajes del caso {case_id}: {e}")
    marcar_publicado_en_registro(supabase, case_id)


def marcar_publicado_en_registro(supabase, case_id):
    """El registro central (compartido por todas las máquinas) se entera de la publicación al
    instante, sin depender de qué máquina la programó. Protegido: nunca afecta la publicación."""
    try:
        sc = supabase.service_client
        r = sc.table("registro_casos").select("case_key,estado").eq("supabase_case_id", case_id).limit(1).execute()
        if not r.data or r.data[0]["estado"] == "publicado":
            return
        sc.rpc("registro_transicion", {"p_key": r.data[0]["case_key"], "p_a": "publicado",
                                       "p_por_que": "publicado por el bot", "p_quien": "bot",
                                       "p_maquina": "render"}).execute()
    except Exception as e:
        logger.warning(f"registro central: no se marcó publicado el caso {case_id}: {e}")


# ─────────────────────────── cola de operaciones ───────────────────────────
async def procesar_ops(bot, supabase, limite=5):
    global _aviso_tabla
    sc = supabase.service_client
    try:
        res = (sc.table("telegram_ops").select("*").eq("estado", "pendiente")
               .order("creado").limit(limite).execute())
    except Exception as e:
        if not _aviso_tabla:
            logger.warning(f"telegram_ops no disponible todavía (¿falta la migración?): {e}")
            _aviso_tabla = True
        return
    _aviso_tabla = False
    for op in res.data or []:
        # reclamar: solo uno la ejecuta aunque hubiera dos procesos
        tomada = (sc.table("telegram_ops").update({"estado": "ejecutando"})
                  .eq("id", op["id"]).eq("estado", "pendiente").execute())
        if not tomada.data:
            continue
        try:
            if op["op"] == "probar":
                resultado = await _probar(bot, op)
            elif op["op"] == "borrar":
                resultado = await _borrar(bot, sc, op)
            elif op["op"] == "editar_vineta":
                resultado = await _editar_vineta(bot, sc, op)
            else:
                raise ValueError(f"operación desconocida: {op['op']}")
            sc.table("telegram_ops").update(
                {"estado": "hecho", "resultado": resultado, "ejecutado": _ahora()}
            ).eq("id", op["id"]).execute()
            logger.info(f"telegram_ops {op['op']} {op['id']}: hecho")
        except OpFallida as e:
            sc.table("telegram_ops").update(
                {"estado": "fallo", "error": str(e)[:900], "resultado": e.resultado, "ejecutado": _ahora()}
            ).eq("id", op["id"]).execute()
            logger.warning(f"telegram_ops {op['op']} {op['id']}: fallo — {e}")
        except Exception as e:
            sc.table("telegram_ops").update(
                {"estado": "fallo", "error": f"{type(e).__name__}: {e}"[:900], "ejecutado": _ahora()}
            ).eq("id", op["id"]).execute()
            logger.error(f"telegram_ops {op['op']} {op['id']}: error", exc_info=True)


class OpFallida(Exception):
    def __init__(self, msg, resultado=None):
        super().__init__(msg)
        self.resultado = resultado


# ─────────────────────────── probar ───────────────────────────
async def _probar(bot, op):
    admins = list(Config.ADMIN_USER_IDS)
    chat = int((op.get("payload") or {}).get("chat_id") or (admins[0] if admins else 0))
    if chat not in admins:
        raise OpFallida(f"probar solo se hace en el chat de un admin; {chat} no lo es")
    m = await bot.send_message(chat_id=chat, text="🔧 Prueba técnica del registro central — se borra sola.",
                               disable_notification=True)
    await bot.edit_message_text(chat_id=chat, message_id=m.message_id,
                                text="🔧 Prueba técnica: edición correcta — se borra sola.")
    await bot.delete_message(chat_id=chat, message_id=m.message_id)
    permisos = {}
    try:
        yo = await bot.get_me()
        miembro = await bot.get_chat_member(Config.PUBLIC_CHANNEL_ID, yo.id)
        permisos = {"estado": getattr(miembro, "status", None)}
        for k in ("can_post_messages", "can_edit_messages", "can_delete_messages"):
            permisos[k] = getattr(miembro, k, None)
    except Exception as e:
        permisos = {"error": str(e)}
    return {"chat": chat, "enviar": True, "editar": True, "borrar": True, "permisos_en_canal": permisos}


# ─────────────────────────── ubicar los mensajes de una publicación ───────────────────────────
def _caso(sc, case_id):
    r = (sc.table("cases").select("id,case_number,vignette,telegram_message_id,telegram_msgs,published")
         .eq("id", case_id).limit(1).execute())
    if not r.data:
        raise OpFallida(f"no existe el caso {case_id}")
    return r.data[0]


async def _reconstruir(bot, canal, poll_id, vineta_texto):
    """Para publicaciones viejas (solo se conoce la encuesta): recorre hacia atrás desde la
    encuesta y VERIFICA cada mensaje reenviándolo al chat del admin. Imágenes entre la viñeta y
    la encuesta son de este caso; al encontrar la viñeta (mismo texto) se detiene; ante
    cualquier otra cosa se detiene sin tocarla."""
    admin = Config.ADMIN_USER_IDS[0]
    objetivo = _norm(vineta_texto)
    espera_vineta = len(vineta_texto or "") > 290
    vineta_id, imagenes, detalle = None, [], []
    for mid in range(poll_id - 1, max(poll_id - 1 - MAX_HACIA_ATRAS, 0), -1):
        try:
            fwd = await bot.forward_message(chat_id=admin, from_chat_id=canal, message_id=mid,
                                            disable_notification=True)
        except Exception as e:
            detalle.append({"id": mid, "visto": f"no se pudo verificar: {e}"})
            break
        try:
            if getattr(fwd, "photo", None):
                imagenes.append(mid); detalle.append({"id": mid, "visto": "imagen"}); continue
            if espera_vineta and getattr(fwd, "text", None) and _norm(fwd.text) == objetivo:
                vineta_id = mid; detalle.append({"id": mid, "visto": "viñeta"}); break
            detalle.append({"id": mid, "visto": "otro mensaje (no se toca)"}); break
        finally:
            try:
                await bot.delete_message(chat_id=admin, message_id=fwd.message_id)
            except Exception:
                pass
    if espera_vineta and vineta_id is None:
        imagenes = []          # sin la viñeta no hay certeza de que las imágenes sean de este caso
    return vineta_id, imagenes, detalle


async def _mensajes_de(bot, caso, vineta_texto=None):
    tm = caso.get("telegram_msgs") or {}
    canal = tm.get("canal") or Config.PUBLIC_CHANNEL_ID
    poll = tm.get("poll_id") or caso.get("telegram_message_id")
    if tm.get("poll_id"):
        return canal, poll, tm.get("vineta_id"), list(tm.get("imagen_ids") or []), [{"fuente": "registrado al publicar"}]
    if not poll:
        raise OpFallida("el caso no tiene ningún mensaje registrado en el canal")
    vid, imgs, det = await _reconstruir(bot, canal, poll, vineta_texto if vineta_texto is not None else caso.get("vignette"))
    return canal, poll, vid, imgs, det


# ─────────────────────────── borrar ───────────────────────────
async def _borrar(bot, sc, op):
    caso = _caso(sc, op["case_id"])
    canal, poll, vid, imgs, detalle = await _mensajes_de(bot, caso, (op.get("payload") or {}).get("texto_publicado"))
    resultado = {"case_number": caso.get("case_number"), "canal": canal, "verificacion": detalle, "mensajes": {}}
    pendientes = [m for m in [poll] + imgs + [vid] if m]
    for mid in pendientes:
        try:
            await bot.delete_message(chat_id=canal, message_id=mid)
            resultado["mensajes"][str(mid)] = "borrado"
        except Exception as e:
            t = str(e)
            resultado["mensajes"][str(mid)] = "ya no existía" if "not found" in t.lower() else f"error: {t}"
    errores = {k: v for k, v in resultado["mensajes"].items() if v.startswith("error")}
    poll_fuera = resultado["mensajes"].get(str(poll)) in ("borrado", "ya no existía")
    if poll_fuera:
        restantes = {"canal": canal, "poll_id": None,
                     "vineta_id": vid if vid and str(vid) in errores else None,
                     "imagen_ids": [i for i in imgs if str(i) in errores]}
        sc.table("cases").update({"published": False, "telegram_message_id": None,
                                  "telegram_msgs": restantes if (restantes["vineta_id"] or restantes["imagen_ids"]) else None}
                                 ).eq("id", caso["id"]).execute()
    if not vid and len(caso.get("vignette") or "") > 290 and not caso.get("telegram_msgs"):
        resultado["aviso"] = "la viñeta no se pudo verificar y NO se borró: quitarla a mano en el canal"
    if errores:
        raise OpFallida(f"{len(errores)} mensaje(s) no se pudieron borrar: {errores}", resultado)
    return resultado


# ─────────────────────────── editar la viñeta ───────────────────────────
async def _editar_vineta(bot, sc, op):
    caso = _caso(sc, op["case_id"])
    p = op.get("payload") or {}
    nuevo = p.get("texto") or caso.get("vignette") or ""
    anterior = p.get("texto_anterior") or nuevo
    if len(anterior) <= 290 and not (caso.get("telegram_msgs") or {}).get("vineta_id"):
        raise OpFallida("la viñeta publicada va DENTRO de la encuesta (≤290 caracteres) y una encuesta "
                        "no se puede editar en Telegram: para corregirla hay que republicar el caso")
    canal, poll, vid, imgs, detalle = await _mensajes_de(bot, caso, anterior)
    if not vid:
        raise OpFallida("no se encontró con certeza el mensaje de la viñeta; no se editó nada",
                        {"verificacion": detalle})
    try:
        await bot.edit_message_text(chat_id=canal, message_id=vid, text=nuevo)
        estado = "editada"
    except Exception as e:
        if "not modified" in str(e).lower():
            estado = "ya estaba igual"
        else:
            raise OpFallida(f"Telegram rechazó la edición: {e}", {"vineta_id": vid})
    if not (caso.get("telegram_msgs") or {}).get("vineta_id"):
        tm = {"canal": canal, "vineta_id": vid, "imagen_ids": imgs, "poll_id": poll}
        sc.table("cases").update({"telegram_msgs": tm}).eq("id", caso["id"]).execute()
    return {"vineta_id": vid, "estado": estado, "case_number": caso.get("case_number"), "verificacion": detalle}
