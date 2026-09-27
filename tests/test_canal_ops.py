"""Pruebas de canal_ops y de la captura de ids al publicar (26-sep-2026).

Telegram y Supabase simulados, con los mismos errores que dan los reales («message to delete
not found», «message is not modified», «message can't be deleted»). Se corre con:
    python -m tests.test_canal_ops      (desde la raíz del repo, con el venv del bot)
"""
import asyncio
import os
import sys

os.environ.setdefault("BOT_TOKEN", "123:prueba")
os.environ.setdefault("PUBLIC_CHANNEL_ID", "-1001")
os.environ.setdefault("ADMIN_USER_IDS", "231090224")
os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("MINIAPP_URL", "http://x")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import canal_ops  # noqa: E402
from config import Config  # noqa: E402

CANAL, ADMIN = Config.PUBLIC_CHANNEL_ID, Config.ADMIN_USER_IDS[0]
ok = fallos = 0


def chk(cond, msg):
    global ok, fallos
    if cond:
        ok += 1
    else:
        fallos += 1
        print("  ✗", msg)


# ───────────────────────── Telegram simulado ─────────────────────────
class Msg:
    def __init__(self, message_id, text=None, photo=None, poll=None):
        self.message_id, self.text, self.photo, self.poll = message_id, text, photo, poll


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeBot:
    username = "bot_prueba"

    def __init__(self):
        self.chats = {}            # chat -> {id: Msg}
        self.sig = {}
        self.no_borrables = set()  # (chat, id) que Telegram se niega a borrar (p. ej. > 48 h)
        self.forward_prohibido = False

    def _nuevo(self, chat, **kw):
        n = self.sig.get(chat, 100) + 1
        self.sig[chat] = n
        m = Msg(n, **kw)
        self.chats.setdefault(chat, {})[n] = m
        return m

    async def send_message(self, chat_id, text, disable_notification=False, **kw):
        return self._nuevo(chat_id, text=text)

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, **kw):
        return self._nuevo(chat_id, photo=[photo], text=None)

    async def send_poll(self, chat_id, question, options, **kw):
        return self._nuevo(chat_id, poll={"q": question, "o": options})

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        m = self.chats.get(chat_id, {}).get(message_id)
        if not m:
            raise Exception("Message to edit not found")
        if m.text == text:
            raise Exception("Bad Request: message is not modified")
        m.text = text
        return m

    async def delete_message(self, chat_id, message_id, **kw):
        if (chat_id, message_id) in self.no_borrables:
            raise Exception("Bad Request: message can't be deleted")
        if message_id not in self.chats.get(chat_id, {}):
            raise Exception("Bad Request: message to delete not found")
        del self.chats[chat_id][message_id]
        return True

    async def forward_message(self, chat_id, from_chat_id, message_id, disable_notification=False, **kw):
        if self.forward_prohibido:
            raise Exception("Bad Request: message can't be forwarded")
        m = self.chats.get(from_chat_id, {}).get(message_id)
        if not m:
            raise Exception("Bad Request: message to forward not found")
        return self._nuevo(chat_id, text=m.text, photo=m.photo, poll=m.poll)

    async def get_me(self):
        return Obj(id=999)

    async def get_chat_member(self, chat, uid):
        return Obj(status="administrator", can_post_messages=True, can_edit_messages=True, can_delete_messages=True)


# ───────────────────────── Supabase simulado ─────────────────────────
class Res:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, db, tabla):
        self.db, self.tabla, self.filtros, self._upd, self._lim = db, tabla, [], None, None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filtros.append((col, val)); return self

    def order(self, *_ , **__):
        return self

    def limit(self, n):
        self._lim = n; return self

    def update(self, datos):
        self._upd = datos; return self

    def execute(self):
        if self.db.caida:
            raise Exception('relation "public.telegram_ops" does not exist')
        filas = [r for r in self.db.t.setdefault(self.tabla, []) if all(r.get(c) == v for c, v in self.filtros)]
        if self._upd is not None:
            if self.tabla == "cases" and "telegram_msgs" in self._upd and self.db.sin_columna:
                raise Exception("Could not find the 'telegram_msgs' column of 'cases'")
            for r in filas:
                r.update(self._upd)
            return Res([dict(r) for r in filas])
        return Res([dict(r) for r in filas[: self._lim or None]])


class FakeSupabase:
    def __init__(self):
        self.t, self.caida, self.sin_columna = {}, False, False
        self.service_client = self

    def table(self, nombre):
        return Q(self, nombre)


def op(db, tipo, case_id=None, payload=None, oid="op1"):
    db.t.setdefault("telegram_ops", []).append(
        {"id": oid, "op": tipo, "case_id": case_id, "payload": payload or {}, "estado": "pendiente"})


def estado_op(db, oid="op1"):
    return next(r for r in db.t["telegram_ops"] if r["id"] == oid)


async def publicar_simulado(bot, vineta, n_imagenes=0):
    """Reproduce lo que hace el bot al publicar: viñeta (si > 290), imágenes y encuesta."""
    v = await bot.send_message(CANAL, vineta) if len(vineta) > 290 else None
    imgs = [(await bot.send_photo(CANAL, f"img{i}")).message_id for i in range(n_imagenes)]
    p = await bot.send_poll(CANAL, "¿Cuál?", ["A", "B"])
    return v, imgs, p


LARGA = "Un niño de 8 años con fractura supracondílea y dolor intenso en el antebrazo. " * 5
CORTA = "Mujer de 30 años con disuria. ¿Diagnóstico?"


async def main():
    # 1. registrar_mensajes guarda todo; y si la columna no existe, NO rompe nada
    bot, db = FakeBot(), FakeSupabase()
    db.t["cases"] = [{"id": "c1", "vignette": LARGA}]
    v, imgs, p = await publicar_simulado(bot, LARGA, 2)
    canal_ops.registrar_mensajes(db, "c1", v, imgs, p)
    tm = db.t["cases"][0]["telegram_msgs"]
    chk(tm["vineta_id"] == v.message_id and tm["imagen_ids"] == imgs and tm["poll_id"] == p.message_id
        and tm["canal"] == CANAL, "registrar_mensajes guarda viñeta, imágenes y encuesta")
    db.sin_columna = True
    try:
        canal_ops.registrar_mensajes(db, "c1", v, imgs, p); chk(True, "")
    except Exception as e:
        chk(False, f"registrar_mensajes no debe lanzar si falta la columna: {e}")

    # 2. probar: envía, edita y borra en el chat del admin; reporta permisos; no deja rastro
    bot, db = FakeBot(), FakeSupabase()
    op(db, "probar", payload={"chat_id": ADMIN})
    await canal_ops.procesar_ops(bot, db)
    o = estado_op(db)
    chk(o["estado"] == "hecho" and o["resultado"]["permisos_en_canal"]["can_delete_messages"] is True,
        f"probar hecho con permisos: {o}")
    chk(not bot.chats.get(ADMIN), "probar no deja mensajes en el chat del admin")
    op(db, "probar", payload={"chat_id": 555}, oid="op2")
    await canal_ops.procesar_ops(bot, db)
    chk(estado_op(db, "op2")["estado"] == "fallo", "probar en un chat que no es admin se rechaza")

    # 3. borrar con ids registrados: se van los 4 mensajes y el caso queda despublicado
    bot, db = FakeBot(), FakeSupabase()
    otro = await bot.send_poll(CANAL, "caso anterior", ["A"])
    v, imgs, p = await publicar_simulado(bot, LARGA, 2)
    db.t["cases"] = [{"id": "c1", "case_number": 21, "vignette": LARGA, "telegram_message_id": p.message_id,
                      "published": True, "telegram_msgs": {"canal": CANAL, "vineta_id": v.message_id,
                                                           "imagen_ids": imgs, "poll_id": p.message_id}}]
    op(db, "borrar", "c1")
    await canal_ops.procesar_ops(bot, db)
    o = estado_op(db)
    chk(o["estado"] == "hecho", f"borrar con ids registrados: {o.get('error')}")
    chk(list(bot.chats[CANAL].keys()) == [otro.message_id], "solo queda el caso anterior en el canal")
    c = db.t["cases"][0]
    chk(c["published"] is False and c["telegram_message_id"] is None and c["telegram_msgs"] is None,
        "el caso queda despublicado y sin ids")

    # 4. borrar un caso VIEJO (solo id de la encuesta): verifica viñeta e imágenes antes de borrar
    bot, db = FakeBot(), FakeSupabase()
    otro = await bot.send_poll(CANAL, "caso anterior", ["A"])
    v, imgs, p = await publicar_simulado(bot, LARGA, 1)
    db.t["cases"] = [{"id": "c1", "vignette": LARGA, "telegram_message_id": p.message_id, "published": True}]
    op(db, "borrar", "c1")
    await canal_ops.procesar_ops(bot, db)
    o = estado_op(db)
    chk(o["estado"] == "hecho" and list(bot.chats[CANAL].keys()) == [otro.message_id],
        f"caso viejo: viñeta + imagen + encuesta borradas, el anterior intacto — {o.get('error')}")
    chk(not bot.chats.get(ADMIN), "las copias de verificación en el chat del admin se borran")
    chk([d["visto"] for d in o["resultado"]["verificacion"]] == ["imagen", "viñeta"], f"verificación: {o['resultado']['verificacion']}")

    # 5. caso viejo con viñeta corta (va dentro de la encuesta): solo se borra la encuesta
    bot, db = FakeBot(), FakeSupabase()
    otro = await bot.send_poll(CANAL, "caso anterior", ["A"])
    v, imgs, p = await publicar_simulado(bot, CORTA)
    db.t["cases"] = [{"id": "c1", "vignette": CORTA, "telegram_message_id": p.message_id, "published": True}]
    op(db, "borrar", "c1")
    await canal_ops.procesar_ops(bot, db)
    chk(estado_op(db)["estado"] == "hecho" and list(bot.chats[CANAL].keys()) == [otro.message_id],
        "viñeta corta: se borra solo la encuesta y el caso anterior queda")

    # 6. caso viejo cuyo mensaje previo es OTRO texto: no se toca, y se avisa
    bot, db = FakeBot(), FakeSupabase()
    ajeno = await bot.send_message(CANAL, "Anuncio del canal que no es de ningún caso " * 10)
    p = await bot.send_poll(CANAL, "¿Cuál?", ["A"])
    db.t["cases"] = [{"id": "c1", "vignette": LARGA, "telegram_message_id": p.message_id, "published": True}]
    op(db, "borrar", "c1")
    await canal_ops.procesar_ops(bot, db)
    o = estado_op(db)
    chk(ajeno.message_id in bot.chats[CANAL] and p.message_id not in bot.chats[CANAL],
        "un texto ajeno antes de la encuesta NO se borra")
    chk("aviso" in (o.get("resultado") or {}), "se avisa que la viñeta no se pudo verificar")

    # 7. si el canal prohíbe reenviar, no se adivina: solo la encuesta
    bot, db = FakeBot(), FakeSupabase()
    v, imgs, p = await publicar_simulado(bot, LARGA, 1)
    bot.forward_prohibido = True
    db.t["cases"] = [{"id": "c1", "vignette": LARGA, "telegram_message_id": p.message_id, "published": True}]
    op(db, "borrar", "c1")
    await canal_ops.procesar_ops(bot, db)
    chk(v.message_id in bot.chats[CANAL] and imgs[0] in bot.chats[CANAL] and p.message_id not in bot.chats[CANAL],
        "sin poder verificar, solo se borra lo seguro (la encuesta)")

    # 8. Telegram se niega a borrar (p. ej. mensaje viejo): fallo explicado, caso intacto
    bot, db = FakeBot(), FakeSupabase()
    v, imgs, p = await publicar_simulado(bot, LARGA)
    bot.no_borrables = {(CANAL, p.message_id), (CANAL, v.message_id)}
    db.t["cases"] = [{"id": "c1", "vignette": LARGA, "telegram_message_id": p.message_id, "published": True,
                      "telegram_msgs": {"canal": CANAL, "vineta_id": v.message_id, "imagen_ids": [], "poll_id": p.message_id}}]
    op(db, "borrar", "c1")
    await canal_ops.procesar_ops(bot, db)
    o = estado_op(db)
    chk(o["estado"] == "fallo" and "can't be deleted" in o["error"], f"fallo explicado: {o.get('error')}")
    chk(db.t["cases"][0]["published"] is True, "si la encuesta no se borró, el caso sigue publicado")

    # 9. editar la viñeta: con id registrado; y «no modificado» cuenta como éxito
    bot, db = FakeBot(), FakeSupabase()
    v, imgs, p = await publicar_simulado(bot, LARGA)
    nueva = LARGA.replace("8 años", "9 años")
    db.t["cases"] = [{"id": "c1", "vignette": nueva, "telegram_message_id": p.message_id, "published": True,
                      "telegram_msgs": {"canal": CANAL, "vineta_id": v.message_id, "imagen_ids": [], "poll_id": p.message_id}}]
    op(db, "editar_vineta", "c1", {"texto": nueva, "texto_anterior": LARGA})
    await canal_ops.procesar_ops(bot, db)
    chk(estado_op(db)["estado"] == "hecho" and bot.chats[CANAL][v.message_id].text == nueva, "viñeta editada")
    op(db, "editar_vineta", "c1", {"texto": nueva, "texto_anterior": LARGA}, oid="op2")
    await canal_ops.procesar_ops(bot, db)
    chk(estado_op(db, "op2")["resultado"]["estado"] == "ya estaba igual", "repetir la edición no falla")

    # 10. editar la viñeta de un caso VIEJO: la ubica por el texto anterior y guarda los ids
    bot, db = FakeBot(), FakeSupabase()
    v, imgs, p = await publicar_simulado(bot, LARGA)
    db.t["cases"] = [{"id": "c1", "vignette": nueva, "telegram_message_id": p.message_id, "published": True}]
    op(db, "editar_vineta", "c1", {"texto": nueva, "texto_anterior": LARGA})
    await canal_ops.procesar_ops(bot, db)
    chk(estado_op(db)["estado"] == "hecho" and bot.chats[CANAL][v.message_id].text == nueva,
        f"caso viejo: viñeta ubicada y editada — {estado_op(db).get('error')}")
    chk(db.t["cases"][0].get("telegram_msgs", {}).get("vineta_id") == v.message_id, "y queda registrado su id")

    # 11. viñeta corta: está dentro de la encuesta, no se puede editar → se dice claramente
    bot, db = FakeBot(), FakeSupabase()
    v, imgs, p = await publicar_simulado(bot, CORTA)
    db.t["cases"] = [{"id": "c1", "vignette": CORTA + " x", "telegram_message_id": p.message_id, "published": True}]
    op(db, "editar_vineta", "c1", {"texto": CORTA + " x", "texto_anterior": CORTA})
    await canal_ops.procesar_ops(bot, db)
    o = estado_op(db)
    chk(o["estado"] == "fallo" and "republicar" in o["error"], f"viñeta dentro de la encuesta: {o.get('error')}")

    # 12. una operación se ejecuta UNA vez aunque se procese dos veces
    bot, db = FakeBot(), FakeSupabase()
    op(db, "probar", payload={"chat_id": ADMIN})
    await asyncio.gather(canal_ops.procesar_ops(bot, db), canal_ops.procesar_ops(bot, db))
    chk(estado_op(db)["estado"] == "hecho" and bot.sig.get(ADMIN) == 101, "sin doble ejecución")

    # 13. sin la tabla (antes de la migración) el ciclo sigue vivo y en silencio
    bot, db = FakeBot(), FakeSupabase()
    db.caida = True
    try:
        await canal_ops.procesar_ops(bot, db); await canal_ops.procesar_ops(bot, db); chk(True, "")
    except Exception as e:
        chk(False, f"sin tabla no debe lanzar: {e}")

    # 14. el scheduler real publica con el bot simulado y registra TODOS los ids
    import scheduler
    import main  # noqa: F401  (scheduler importa case_display_num de main)
    bot, db = FakeBot(), FakeSupabase()

    class SB(FakeSupabase):
        def mark_publishing(self, _):
            return True

        def mark_done(self, *_):
            return True

        def mark_failed(self, *a):
            raise AssertionError(f"no debió fallar: {a}")

        def update_case(self, cid, data):
            for r in self.t["cases"]:
                if r["id"] == cid:
                    r.update(data)
            return True

    sb = SB()
    sb.t["cases"] = [{"id": "c9", "vignette": LARGA}]
    scheduler.init_scheduler(Obj(bot=bot), sb)

    async def _nada(*a, **k):
        return None
    scheduler._notify_admin_success = _nada
    post = {"id": "p1", "case_id": "c9", "cases": {"vignette": LARGA, "published": False,
                                                    "options": [{"letter": "A", "text": "Manometría"}, {"letter": "B", "text": "Doppler"}],
                                                    "correct_letter": "A", "tip": "tip"}}
    await scheduler._publish_single(post)
    c = sb.t["cases"][0]
    chk(c.get("published") is True and c.get("telegram_msgs", {}).get("vineta_id") and c["telegram_msgs"]["poll_id"] == c["telegram_message_id"],
        f"el scheduler registra viñeta y encuesta al publicar: {c.get('telegram_msgs')}")

    print(f"\n  {ok} comprobaciones OK · {fallos} fallos")
    return fallos


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
