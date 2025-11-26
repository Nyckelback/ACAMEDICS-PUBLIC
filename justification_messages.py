# -*- coding: utf-8 -*-
"""
Banco de mensajes creativos para justificaciones médicas
"""
import random

PROFESSIONAL_MESSAGES = [
    "📚 ¡Justificación lista! Revisa con calma.",
    "✨ Material de estudio enviado.",
    "🎯 ¡Justificación disponible!",
    "📖 Contenido académico listo para revisar.",
    "🔍 Material explicativo enviado exitosamente.",
    "💡 ¡Información detallada lista!",
    "📝 Justificación completa disponible.",
    "🩺 Material clínico enviado. ¡Éxito!",
    "📊 Caso analizado y justificado. ¡A estudiar!",
    "🎓 Material académico listo. ¡Que sea útil!",
    "💪 Un paso más cerca de la residencia. ¡Justificación enviada!",
    "🏆 Futuro residente, aquí está tu justificación.",
    "📈 Tu curva de aprendizaje acaba de subir. Material enviado.",
    "🌟 Brillas más que la lámpara del quirófano. Justificación lista.",
    "🚀 Despegando hacia la residencia. Combustible: Esta justificación.",
]

SOFT_MEDICAL_HUMOR = [
    "💊 Tu dosis de conocimiento ha sido enviada.",
    "🩺 Diagnóstico: Necesitas esta justificación. Tratamiento: Leerla.",
    "📋 Historia clínica del caso: Completa. Tu tarea: Estudiarla.",
    "🔬 Resultados del laboratorio de conocimiento listos.",
    "💉 Inyección de sabiduría administrada con éxito.",
    "🏥 Interconsulta con la justificación: Aprobada.",
    "🚑 Justificación de emergencia despachada.",
    "👨‍⚕️ El Dr. Bot te envió la justificación STAT!",
    "🌡️ Justificación a temperatura ambiente. Consumir antes de 10 min.",
    "🦴 Rayos X del caso revelados. Sin fracturas en la lógica.",
]

MEDICAL_KNOWLEDGE_HUMOR = [
    "🫀 Tu nodo SA está enviando impulsos de felicidad.",
    "🧬 Mutación detectada en el gen del conocimiento: +100 IQ.",
    "💊 Farmacocinética: Absorción inmediata, Distribución cerebral.",
    "🦠 Gram positivo para el aprendizaje. Sensible a esta justificación.",
    "🩸 Tu Hb subió 2 puntos solo de ver esta justificación.",
    "🧪 pH del conocimiento: 7.4. Perfectamente balanceado.",
    "🔬 Biopsia de tu ignorancia: Negativa.",
    "🫁 Relación V/Q perfecta entre pregunta y justificación.",
]

BOLD_FUNNY_MESSAGES = [
    "💀 Si no aciertas después de esto, el problema no es el caso...",
    "🧠 Justificación enviada. Úsala sabiamente.",
    "☕ Justificación + café = Residente feliz",
    "😷 Esta justificación no previene COVID, pero sí la ignorancia.",
    "🔥 Justificación más caliente que la fiebre del paciente.",
    "💸 Esta justificación vale más que tu sueldo de residente.",
    "🍕 Justificación enviada. Ahora sí puedes ir por pizza.",
    "😴 Justificación lista. Léela antes de la guardia.",
    "🎮 Pausaste el PlayStation para esto. Que valga la pena.",
    "📱 Notificación importante: No es match de Tinder, es tu justificación.",
]

ALL_MESSAGES = (
    PROFESSIONAL_MESSAGES +
    SOFT_MEDICAL_HUMOR +
    MEDICAL_KNOWLEDGE_HUMOR +
    BOLD_FUNNY_MESSAGES
)


def get_random_message() -> str:
    return random.choice(ALL_MESSAGES)


def get_weighted_random_message() -> str:
    """Mensaje con mayor probabilidad de profesionales"""
    weights = [
        (PROFESSIONAL_MESSAGES, 30),
        (SOFT_MEDICAL_HUMOR, 25),
        (MEDICAL_KNOWLEDGE_HUMOR, 25),
        (BOLD_FUNNY_MESSAGES, 20),
    ]
    
    weighted_list = []
    for messages, weight in weights:
        weighted_list.extend(messages * weight)
    
    return random.choice(weighted_list)


# ========================================
# MENSAJES GENERALES (para @@@ botones)
# NO mencionan "justificación" - son neutros
# ========================================
GENERAL_MESSAGES = [
    "📥 ¡Listo! Aquí tienes tu contenido.",
    "✅ Contenido enviado correctamente.",
    "📦 ¡Entrega exitosa!",
    "🎁 Aquí está lo que pediste.",
    "📲 Contenido disponible.",
    "✨ ¡Listo para ti!",
    "🚀 Enviado con éxito.",
    "📋 Material listo para revisar.",
    "💾 Descarga disponible.",
    "📎 Aquí tienes el archivo.",
    "🔓 Contenido desbloqueado.",
    "📤 Entregado correctamente.",
    "⬇️ Descarga lista.",
    "🎯 ¡Aquí lo tienes!",
    "📁 Archivo enviado.",
    "✔️ Contenido entregado.",
    "🌟 ¡Disfrútalo!",
    "📱 Material disponible.",
    "💫 ¡Todo listo!",
    "🔔 Notificación: Contenido enviado.",
]


def get_general_message() -> str:
    """
    Retorna un mensaje general neutro (para @@@ botones).
    NO menciona justificaciones ni términos médicos específicos.
    """
    return random.choice(GENERAL_MESSAGES)
