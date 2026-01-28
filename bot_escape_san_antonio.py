#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import unicodedata
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Any, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PicklePersistence,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN_ENV = "TELEGRAM_TOKEN"
SALA3_IMAGE = "sala3.png"
PERSIST_FILE = "escape_san_antonio.pickle"

MODE_INDIVIDUAL = "individual"
MODE_GROUP = "group"

# =========================
# MINI WEB SERVER (Render needs an open port)
# =========================
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silenciar logs HTTP para no llenar Render logs
        return

def start_health_server():
    """
    Render (Web Service) exige que el proceso abra un puerto.
    Render define el puerto en la variable de entorno PORT.
    """
    port = int(os.getenv("PORT", "10000"))  # fallback si lo pruebas local
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info(f"Health server listening on port {port}")
    server.serve_forever()

# =========================
# HELPERS
# =========================
def now_ts() -> int:
    return int(time.time())

def normalize(text: str) -> str:
    text = text.strip().lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(text.split())

def lines(*parts: str) -> str:
    return "\n".join(parts)

def safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def elapsed(state: dict) -> int:
    return (now_ts() - safe_int(state.get("start_ts", now_ts()))) + safe_int(state.get("penalty_sec", 0))

def count_total_attempts(state: dict) -> int:
    att = state.get("attempts", {}) or {}
    return sum(safe_int(v, 0) for v in att.values())

def count_total_hints_used(state: dict) -> int:
    hu = state.get("hints_used", {}) or {}
    return sum(safe_int(v, 0) for v in hu.values())

def compute_score(state: dict) -> Dict[str, int]:
    base = 1000
    total_time = elapsed(state)
    penalty_sec = safe_int(state.get("penalty_sec", 0))
    hints_used = count_total_hints_used(state)
    attempts = count_total_attempts(state)
    optional_done = len(state.get("optional_done", []) or [])
    completed = 1 if state.get("completed") else 0

    score = base
    score -= total_time
    score -= 40 * hints_used
    score -= 5 * attempts
    score += 50 * optional_done
    if completed:
        score += 200

    if score < 0:
        score = 0

    return {
        "score": score,
        "time_sec": total_time,
        "penalty_sec": penalty_sec,
        "hints_used": hints_used,
        "attempts": attempts,
        "optional_done": optional_done,
    }

# =========================
# TEXTOS
# =========================
INTRO = (
    "EL CAMINO DEL DESIERTO — Escape Room (San Antonio Abad)\n\n"
    "Comandos:\n"
    "• /pista\n"
    "• /inventario\n"
    "• /estado\n"
    "• /reiniciar\n\n"
    "Elige cómo quieres jugar:"
)

# =========================
# ROOMS
# =========================
ROOMS: Dict[int, Dict[str, Any]] = {
    1: {
        "text": lines(
            "SALA 1 · LA LLAMADA",
            "",
            "Antonio entra en la iglesia. Oye unas palabras decisivas.",
            "",
            "Ordena esta frase y escríbela completa:",
            "tienes / vende / y / a los pobres / dalo / lo que"
        ),
        "hints": [
            "Empieza por el verbo principal: 'vende'.",
            "La frase completa habla de desprenderse y ayudar a los pobres."
        ],
        "answers": ["vende lo que tienes y dalo a los pobres"],
        "item": "RENUNCIA",
        "success": "Correcto. Objeto: RENUNCIA. Pasas a la Sala 2."
    },
    2: {
        "text": lines(
            "SALA 2 · LA HERENCIA",
            "",
            "Antonio hereda bienes. Decide qué hacer con ellos.",
            "",
            "Elige A, B o C:",
            "A) Guardarlos para su seguridad",
            "B) Repartirlos entre los pobres",
            "C) Invertirlos para obtener más"
        ),
        "hints": [
            "Elige la opción que encaja con el desprendimiento.",
            "No se queda con la fortuna."
        ],
        "answers": ["b"],
        "item": "DESAPEGO",
        "success": "Correcto. Objeto: DESAPEGO. Pasas a la Sala 3."
    },
    3: {
        "text": lines(
            "SALA 3 · EL DESIERTO",
            "",
            "Antonio se retira para vivir en soledad.",
            "Mira la imagen: hay una palabra escondida.",
            "",
            "Escribe la palabra."
        ),
        "hints": [
            "Es el lugar físico y simbólico del retiro.",
            "Tiene 7 letras."
        ],
        "answers": ["desierto"],
        "item": "SOLEDAD",
        "image": True,
        "success": "Correcto. Objeto: SOLEDAD. Pasas a la Sala 4."
    },
    4: {
        "text": lines(
            "SALA 4 · LAS TENTACIONES (SECUENCIAL)",
            "",
            "Responderás 3 micro-preguntas, una detrás de otra.",
            "Escribe SOLO la virtud (una palabra).",
            "",
            "Primera tentación: SOBERBIA",
            "¿Qué virtud la vence?"
        ),
        "hints": [
            "Contrario de soberbia.",
            "Virtud relacionada con reconocer límites."
        ],
        "answers": None,
        "item": "FORTALEZA",
        "success": "Correcto. Objeto: FORTALEZA. Pasas a la Sala 5."
    },
    5: {
        "text": lines(
            "SALA 5 · LA ORACIÓN",
            "",
            "Completa:",
            "La oración es el ___ del alma"
        ),
        "hints": [
            "Relacionado con respirar.",
            "Empieza por 'a'."
        ],
        "answers": ["aliento"],
        "item": "ORACION",
        "success": "Correcto. Objeto: ORACION. Pasas a la Sala 6."
    },
    6: {
        "text": lines(
            "SALA 6 · EL EJEMPLO",
            "",
            "Responde VERDADERO o FALSO:",
            "San Antonio vivió siempre aislado y nunca tuvo contacto con otras personas."
        ),
        "hints": [
            "Fue referente; la gente lo buscaba.",
            "Aconsejaba a otros."
        ],
        "answers": ["falso"],
        "item": "COMUNIDAD",
        "success": "Correcto. Objeto: COMUNIDAD. Pasas a la Sala 7."
    },
    7: {
        "text": lines(
            "SALA 7 · EL CONSEJO",
            "",
            "Elige A, B o C. ¿Qué consejo encaja con su vida?",
            "A) Acumula riquezas",
            "B) Persevera en el bien",
            "C) Busca reconocimiento"
        ),
        "hints": [
            "No acumular ni buscar fama.",
            "La clave es la constancia."
        ],
        "answers": ["b"],
        "item": "SABIDURIA",
        "success": "Correcto. Objeto: SABIDURIA. Pasas a la Sala 8."
    },
    8: {
        "text": lines(
            "SALA 8 · LA CARIDAD",
            "",
            "Resuelve el anagrama:",
            "IDADCAR"
        ),
        "hints": [
            "Virtud esencial del cristianismo.",
            "Empieza por 'c'."
        ],
        "answers": ["caridad"],
        "item": "COMPASION",
        "success": "Correcto. Objeto: COMPASION. Pasas a la Sala 9."
    },
    9: {
        "text": lines(
            "SALA 9 · EL FINAL",
            "",
            "Escribe UNA virtud que defina su vida.",
        ),
        "hints": [
            "Elige una de estas cuatro: humildad, fe, pobreza, perseverancia.",
            "Escríbela tal cual."
        ],
        "answers": ["humildad", "fe", "pobreza", "perseverancia"],
        "item": "PAZ_INTERIOR",
        "success": "Correcto. Objeto: PAZ_INTERIOR. Pasas a la Sala 10."
    },
    10: {
        "text": lines(
            "SALA 10 · EL LEGADO",
            "",
            "Escribe TRES objetos de tu inventario separados por comas.",
        ),
        "hints": [
            "Una combinación válida: renuncia, oracion, sabiduria.",
            "Otra válida: desapego, fortaleza, compasion."
        ],
        "answers": None,
        "item": None,
        "success": lines(
            "ESCAPE COMPLETADO.",
            "",
            "Has recorrido el camino del desierto.",
            "El legado de San Antonio no fue riqueza, sino ejemplo."
        )
    },
}

# =========================
# OPCIONALES
# =========================
OPTIONALS: Dict[str, Dict[str, Any]] = {
    "A": {
        "text": lines(
            "SALA OPCIONAL A · EL SILENCIO",
            "",
            "Completa:",
            "En el silencio, el corazón aprende a ___."
        ),
        "answers": ["escuchar"],
        "reward": {"free_hints": 1},
        "success": "Correcto. Recompensa: +1 pista gratuita. Vuelves a la ruta principal.",
        "back_to": 4
    },
    "B": {
        "text": lines(
            "SALA OPCIONAL B · DISCERNIMIENTO",
            "",
            "Elige A, B o C:",
            "A) Huir de toda dificultad",
            "B) Perseverar con humildad",
            "C) Buscar reconocimiento"
        ),
        "answers": ["b"],
        "reward": {"remove_penalty_sec": 60},
        "success": "Correcto. Recompensa: -60 s de penalización (si la tenías). Vuelves a la ruta principal.",
        "back_to": 7
    },
    "C": {
        "text": lines(
            "SALA OPCIONAL C · CARIDAD ACTIVA",
            "",
            "Anagrama: ROAMICNSOP",
            "Escribe la palabra."
        ),
        "answers": ["compasion"],
        "reward": {"jokers": 1},
        "success": "Correcto. Recompensa: +1 comodín. Vuelves a la ruta principal.",
        "back_to": 9
    },
}

OPTIONAL_OFFER_POINTS: Dict[int, Tuple[str, int]] = {
    3: ("A", 4),
    6: ("B", 7),
    8: ("C", 9),
}

# =========================
# STATE
# =========================
def init_state(container: dict) -> None:
    if "escape" not in container:
        container["escape"] = {
            "mode": None,
            "room": 0,
            "inventory": [],
            "start_ts": now_ts(),
            "penalty_sec": 0,
            "hints_used": {},
            "attempts": {},
            "completed": False,
            "optional_done": [],
            "in_optional": None,
            "free_hints": 0,
            "jokers": 0,
            "s4_step": 0,
        }

def get_container(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "escape" in context.chat_data and context.chat_data["escape"].get("mode") == MODE_GROUP:
        init_state(context.chat_data)
        return context.chat_data
    init_state(context.user_data)
    return context.user_data

def st(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return get_container(context)["escape"]

def add_item(state: dict, item: Optional[str]) -> None:
    if not item:
        return
    inv = state.setdefault("inventory", [])
    if item not in inv:
        inv.append(item)

def inc(state: dict, key: str, room: int) -> int:
    d = state.setdefault(key, {})
    d[room] = safe_int(d.get(room, 0)) + 1
    return d[room]

# =========================
# VALIDATION
# =========================
def validate_room_10(raw: str, inv: List[str], jokers: int) -> Tuple[bool, bool]:
    inv_norm = {normalize(x) for x in inv}
    items = [normalize(x) for x in raw.split(",") if normalize(x)]
    unique = []
    for it in items:
        if it not in unique:
            unique.append(it)

    if len(unique) >= 3:
        return set(unique[:3]).issubset(inv_norm), False

    if len(unique) == 2 and jokers > 0:
        return set(unique).issubset(inv_norm), True

    return False, False

# =========================
# SENDERS
# =========================
async def send_room(update: Update, context: ContextTypes.DEFAULT_TYPE, room: int) -> None:
    state = st(context)
    state["room"] = room
    if room == 4:
        state["s4_step"] = 0

    data = ROOMS[room]
    if data.get("image"):
        if os.path.exists(SALA3_IMAGE):
            with open(SALA3_IMAGE, "rb") as f:
                await update.effective_chat.send_photo(photo=f, caption=data["text"])
        else:
            await update.effective_chat.send_message(data["text"] + "\n\n(AVISO: no encuentro sala3.png)")
        return

    await update.effective_chat.send_message(data["text"])

async def offer_optional(update: Update, opt_id: str, next_room: int) -> None:
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Entrar en sala opcional", callback_data=f"opt_enter_{opt_id}")],
        [InlineKeyboardButton("Seguir ruta principal", callback_data=f"opt_skip_{opt_id}_{next_room}")]
    ])
    await update.effective_chat.send_message(
        "Has desbloqueado una sala opcional.\n"
        "Puedes hacerla para ganar una recompensa o continuar.",
        reply_markup=kb
    )

# =========================
# COMMANDS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Jugar INDIVIDUAL", callback_data="mode_individual")],
        [InlineKeyboardButton("Jugar GRUPO (progreso compartido)", callback_data="mode_group")],
    ])
    await update.message.reply_text(INTRO, reply_markup=kb)

async def cmd_reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Sí, reiniciar", callback_data="restart_yes")],
        [InlineKeyboardButton("No", callback_data="restart_no")],
    ])
    await update.message.reply_text("¿Reiniciar partida? Se perderá el progreso.", reply_markup=kb)

async def cmd_inventario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = st(context)
    inv = state.get("inventory", [])
    free_hints = safe_int(state.get("free_hints", 0))
    jokers = safe_int(state.get("jokers", 0))

    msg = "INVENTARIO\n"
    msg += "\n".join([f"• {x}" for x in inv]) if inv else "(vacío)"
    msg += f"\n\nPistas gratuitas: {free_hints}\nComodines: {jokers}"
    await update.message.reply_text(msg)

async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = st(context)
    room = safe_int(state.get("room", 0))
    stats = compute_score(state)
    msg = (
        "ESTADO\n"
        f"Modo: {state.get('mode')}\n"
        f"Sala: {room}/10\n"
        f"Tiempo (con penalización): {stats['time_sec']} s\n"
        f"Penalización acumulada: {stats['penalty_sec']} s\n"
        f"Pistas usadas: {stats['hints_used']}\n"
        f"Intentos totales: {stats['attempts']}\n"
        f"Opcionales completadas: {stats['optional_done']}\n"
        f"Pistas gratuitas: {safe_int(state.get('free_hints', 0))}\n"
        f"Comodines: {safe_int(state.get('jokers', 0))}\n"
        f"Puntuación provisional: {stats['score']}/1000\n"
    )
    await update.message.reply_text(msg)

async def cmd_pista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = st(context)
    if state.get("mode") is None:
        await update.message.reply_text("Usa /start y elige modo para comenzar.")
        return
    if state.get("completed"):
        await update.message.reply_text("Ya completaste el escape. Usa /reiniciar si quieres repetir.")
        return
    if state.get("in_optional"):
        await update.message.reply_text("Esta sala opcional no tiene pista automática.")
        return

    room = safe_int(state.get("room", 0))
    if room == 0:
        await update.message.reply_text("Aún no has comenzado. Usa /start.")
        return

    hints = ROOMS[room].get("hints", [])
    if not hints:
        await update.message.reply_text("Esta sala no tiene pistas.")
        return

    used = safe_int(state.setdefault("hints_used", {}).get(room, 0)) + 1
    state["hints_used"][room] = used

    idx = used - 1
    if idx >= len(hints):
        await update.message.reply_text("No hay más pistas disponibles para esta sala.")
        return

    if safe_int(state.get("free_hints", 0)) > 0:
        state["free_hints"] -= 1
        await update.message.reply_text(f"PISTA: {hints[idx]}\n(Sin penalización: usaste una pista gratuita.)")
        return

    penalty = 30 * used
    state["penalty_sec"] = safe_int(state.get("penalty_sec", 0)) + penalty
    await update.message.reply_text(f"PISTA: {hints[idx]}\nPenalización: +{penalty} s")

# =========================
# BUTTONS
# =========================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "mode_individual":
        context.user_data.clear()
        init_state(context.user_data)
        state = context.user_data["escape"]
        state["mode"] = MODE_INDIVIDUAL
        state["start_ts"] = now_ts()
        state["room"] = 1
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Entrar en Sala 1", callback_data="enter_1")]])
        await q.message.reply_text("Modo INDIVIDUAL activado. Pulsa para comenzar.", reply_markup=kb)
        return

    if data == "mode_group":
        if update.effective_chat.type == "private":
            await q.message.reply_text("El modo GRUPO solo funciona dentro de un grupo de Telegram.")
            return
        context.chat_data.clear()
        init_state(context.chat_data)
        state = context.chat_data["escape"]
        state["mode"] = MODE_GROUP
        state["start_ts"] = now_ts()
        state["room"] = 1
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Entrar en Sala 1", callback_data="enter_1")]])
        await q.message.reply_text("Modo GRUPO activado. Pulsa para comenzar.", reply_markup=kb)
        return

    if data == "restart_yes":
        cont = get_container(context)
        mode = cont.get("escape", {}).get("mode", None)
        cont["escape"] = {}
        init_state(cont)
        cont["escape"]["mode"] = mode
        await q.message.reply_text("Partida reiniciada. Usa /start para comenzar de nuevo.")
        return

    if data == "restart_no":
        await q.message.reply_text("De acuerdo. No se reinicia.")
        return

    if data == "enter_1":
        await send_room(update, context, 1)
        return

    if data.startswith("opt_enter_"):
        opt_id = data.split("_")[-1]
        state = st(context)
        done = state.setdefault("optional_done", [])
        if opt_id in done:
            await q.message.reply_text("Esta sala opcional ya está completada.")
            return
        state["in_optional"] = opt_id
        await q.message.reply_text(OPTIONALS[opt_id]["text"])
        return

    if data.startswith("opt_skip_"):
        parts = data.split("_")
        next_room = int(parts[3])
        await send_room(update, context, next_room)
        return

# =========================
# SALA 4 SECUENCIAL
# =========================
async def handle_room4_sequence(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text_norm: str) -> bool:
    state = st(context)
    if safe_int(state.get("room", 0)) != 4:
        return False

    step = safe_int(state.get("s4_step", 0))
    expected = ["humildad", "pobreza", "confianza"]

    if step < 0 or step > 2:
        state["s4_step"] = 0
        await update.message.reply_text("Reiniciamos la Sala 4.\nTentación: SOBERBIA\n¿Qué virtud la vence?")
        return True

    inc(state, "attempts", 4)

    if user_text_norm != expected[step]:
        await update.message.reply_text("No es correcto. Inténtalo de nuevo (una palabra).")
        return True

    if step == 0:
        state["s4_step"] = 1
        await update.message.reply_text("Correcto.\n\nSiguiente tentación: RIQUEZA\n¿Qué virtud la vence?")
        return True

    if step == 1:
        state["s4_step"] = 2
        await update.message.reply_text("Correcto.\n\nSiguiente tentación: MIEDO\n¿Qué virtud la vence?")
        return True

    state["s4_step"] = 3
    add_item(state, ROOMS[4]["item"])
    await update.message.reply_text("Correcto.\nHas vencido las 3 tentaciones.")
    await update.message.reply_text(ROOMS[4]["success"])
    await send_room(update, context, 5)
    return True

# =========================
# TEXT HANDLER
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return

    state = st(context)
    if state.get("mode") is None:
        await update.message.reply_text("Usa /start y elige modo para comenzar.")
        return
    if state.get("completed"):
        await update.message.reply_text("Ya completaste el escape. Usa /reiniciar si quieres repetir.")
        return

    raw = update.message.text
    txt = normalize(raw)

    opt_id = state.get("in_optional")
    if opt_id:
        answers = [normalize(a) for a in OPTIONALS[opt_id]["answers"]]
        if txt not in answers:
            await update.message.reply_text("No es correcto. Inténtalo de nuevo.")
            return

        done = state.setdefault("optional_done", [])
        if opt_id not in done:
            done.append(opt_id)

        reward = OPTIONALS[opt_id].get("reward", {})
        if "free_hints" in reward:
            state["free_hints"] = safe_int(state.get("free_hints", 0)) + safe_int(reward["free_hints"], 0)
        if "jokers" in reward:
            state["jokers"] = safe_int(state.get("jokers", 0)) + safe_int(reward["jokers"], 0)
        if "remove_penalty_sec" in reward:
            state["penalty_sec"] = max(0, safe_int(state.get("penalty_sec", 0)) - safe_int(reward["remove_penalty_sec"], 0))

        state["in_optional"] = None
        await update.message.reply_text(OPTIONALS[opt_id]["success"])
        await send_room(update, context, OPTIONALS[opt_id]["back_to"])
        return

    room = safe_int(state.get("room", 0))
    if room == 0:
        await update.message.reply_text("Pulsa /start para comenzar.")
        return

    if await handle_room4_sequence(update, context, txt):
        return

    inc(state, "attempts", room)
    data = ROOMS[room]

    ok = False
    used_joker = False

    if room == 10:
        ok, used_joker = validate_room_10(raw, state.get("inventory", []), safe_int(state.get("jokers", 0)))
    else:
        answers = [normalize(a) for a in (data.get("answers") or [])]
        ok = (txt in answers)

    if not ok:
        await update.message.reply_text("No es correcto. Inténtalo de nuevo. (Usa /pista si lo necesitas.)")
        return

    if room == 10 and used_joker:
        state["jokers"] = max(0, safe_int(state.get("jokers", 0)) - 1)

    add_item(state, data.get("item"))
    await update.message.reply_text(data["success"])

    if room == 10:
        state["completed"] = True
        stats = compute_score(state)
        final_msg = lines(
            "",
            "PUNTUACION FINAL",
            f"Puntos: {stats['score']}",
            f"Tiempo total: {stats['time_sec']} s",
            f"Penalizacion acumulada: {stats['penalty_sec']} s",
            f"Pistas usadas: {stats['hints_used']}",
            f"Intentos totales: {stats['attempts']}",
            f"Opcionales completadas: {stats['optional_done']}",
            "",
            "Gracias por jugar."
        )
        await update.message.reply_text(final_msg)
        return

    if room in OPTIONAL_OFFER_POINTS:
        opt_id_offer, next_room = OPTIONAL_OFFER_POINTS[room]
        await offer_optional(update, opt_id_offer, next_room)
        return

    await send_room(update, context, room + 1)

# =========================
# ERROR HANDLER
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error no controlado:", exc_info=context.error)

# =========================
# MAIN
# =========================
def main() -> None:
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise RuntimeError(f"Falta la variable de entorno {TOKEN_ENV} con el token del bot.")

    # 1) Abrir puerto para Render (en hilo aparte)
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()

    # 2) Bot normal (polling)
    persistence = PicklePersistence(filepath=PERSIST_FILE)
    app = Application.builder().token(token).persistence(persistence).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pista", cmd_pista))
    app.add_handler(CommandHandler("inventario", cmd_inventario))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("reiniciar", cmd_reiniciar))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.add_error_handler(error_handler)

    PORT = int(os.environ.get("PORT", 10000))

WEBHOOK_PATH = "/telegram"
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL") + WEBHOOK_PATH

app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=WEBHOOK_PATH,
    webhook_url=WEBHOOK_URL,
    allowed_updates=Update.ALL_TYPES,
)

if __name__ == "__main__":
    main()


