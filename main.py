import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from google import genai
from google.genai import types

load_dotenv(override=True)

TELEGRAM_TOKEN = (os.getenv("TELEGRAM_TOKEN") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
MODEL_NAME = (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash").strip()
RENDER_URL = (os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
ai_client = genai.Client(vertexai=False, api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

AGENTS = {
    "direktor": {
        "name": "Sardor",
        "role": "Direktor",
        "emoji": "\U0001f454",
        "prompt": (
            "Sen AI SMM agentligining tajribali direktori Sardorsan. "
            "Har doim o'zbek tilida, aniq, tizimli va qisqa javob ber. "
            "Mijoz topshirig'ini olganingda: 1) asosiy maqsadni belgilaysan, "
            "2) Buxgalter, SMM Strateg, Kopirayter, Targetolog va Dizayner uchun "
            "aniq qisqa vazifalar taqsimlab berasan."
        ),
    },
    "buxgalter": {
        "name": "Jasur",
        "role": "Buxgalter",
        "emoji": "\U0001f4b0",
        "prompt": (
            "Sen SMM agentligining bosh buxgalteri Jasursan. "
            "Har doim o'zbek tilida javob ber. Reklama byudjeti, agentlik xizmat haqi, "
            "kutilayotgan daromad, xarajatlar taqsimoti va taxminiy ROI ni raqamlar bilan "
            "tushunarli ro'yxat ko'rinishida hisoblab ber."
        ),
    },
    "strateg": {
        "name": "Dilnoza",
        "role": "SMM Strateg",
        "emoji": "\U0001f4ca",
        "prompt": (
            "Sen SMM strateg Dilnozasan. Har doim o'zbek tilida javob ber. "
            "Maqsadli auditoriya (CA), raqobatchilardan ustunlik (UTP) va "
            "1 haftalik/1 oylik aniq kontent-reja (Reels, Stories, Post) tuzib ber."
        ),
    },
    "kopirayter": {
        "name": "Aziz",
        "role": "Kopirayter",
        "emoji": "\u270d\ufe0f",
        "prompt": (
            "Sen kreativ kopirayter Azizsan. Har doim jonli, sotuvchi o'zbek tilida yoz. "
            "1 ta kuchli sotuvchi Instagram post matni (sarlavha, asosiy qism, CTA va heshteglar) "
            "hamda 1 ta qisqa Reels ssenariysi (ilk 3 soniyalik hook bilan) tayyorlab ber."
        ),
    },
    "targetolog": {
        "name": "Bobur",
        "role": "Targetolog",
        "emoji": "\U0001f3af",
        "prompt": (
            "Sen Meta (Instagram/Facebook) targetologi Bobursan. "
            "Har doim o'zbek tilida javob ber. Qaysi reklama maqsadini tanlash, "
            "auditoriya sozlamalari (yosh, qiziqishlar, hudud), kunlik byudjet va "
            "1 ta lidning taxminiy narxini (CPL) yozib ber."
        ),
    },
    "dizayner": {
        "name": "Malika",
        "role": "Dizayner & Produser",
        "emoji": "\U0001f3a8",
        "prompt": (
            "Sen SMM dizayner va video produser Malikasan. Har doim o'zbek tilida javob ber. "
            "Reklama banneri va Reels vizual konsepsiyasi, ranglar palitrasi, "
            "bannerdagi yozuv joylashuvi hamda AI rasm generatsiyasi uchun tayyor "
            "inglizcha 1 ta mukammal prompt tuzib ber."
        ),
    },
}

SPECIALISTS = ["buxgalter", "strateg", "kopirayter", "targetolog", "dizayner"]

BTN_TEAM = "\U0001f3e2 Jamoaviy topshiriq (Hamma)"
BTN_DIREKTOR = "\U0001f454 Sardor (Direktor)"
BTN_BUXGALTER = "\U0001f4b0 Jasur (Buxgalter)"
BTN_STRATEG = "\U0001f4ca Dilnoza (Strateg)"
BTN_KOPIRAYTER = "\u270d\ufe0f Aziz (Kopirayter)"
BTN_TARGETOLOG = "\U0001f3af Bobur (Targetolog)"
BTN_DIZAYNER = "\U0001f3a8 Malika (Dizayner)"

BUTTON_TO_MODE = {
    BTN_TEAM: "team",
    BTN_DIREKTOR: "direktor",
    BTN_BUXGALTER: "buxgalter",
    BTN_STRATEG: "strateg",
    BTN_KOPIRAYTER: "kopirayter",
    BTN_TARGETOLOG: "targetolog",
    BTN_DIZAYNER: "dizayner",
}

KEYBOARD = {
    "keyboard": [
        [{"text": BTN_TEAM}],
        [{"text": BTN_DIREKTOR}, {"text": BTN_BUXGALTER}],
        [{"text": BTN_STRATEG}, {"text": BTN_KOPIRAYTER}],
        [{"text": BTN_TARGETOLOG}, {"text": BTN_DIZAYNER}],
    ],
    "resize_keyboard": True,
}

user_modes = {}
processed_updates = set()


def send_msg(chat_id, text, reply_markup=None):
    chunk_size = 3800
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]
    with httpx.Client(timeout=30.0) as client:
        for idx, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup and idx == 0:
                payload["reply_markup"] = reply_markup
            client.post(f"{TG_API}/sendMessage", json=payload)


def send_doc(chat_id, filename, content, caption):
    with httpx.Client(timeout=30.0) as client:
        client.post(
            f"{TG_API}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (filename, content.encode("utf-8"), "text/markdown")},
        )


def ask_agent(agent_key, user_text):
    if not ai_client:
        raise ValueError("GEMINI_API_KEY sozlanmagan!")
    agent = AGENTS[agent_key]
    last_err = None
    for attempt in range(4):
        try:
            resp = ai_client.models.generate_content(
                model=MODEL_NAME,
                contents=user_text,
                config=types.GenerateContentConfig(
                    system_instruction=agent["prompt"],
                    temperature=0.7,
                ),
            )
            return resp.text or "Javob olinmadi."
        except Exception as err:
            last_err = err
            msg = str(err)
            if any(c in msg for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")) and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise last_err


def handle_message(chat_id, text):
    if text == "/start":
        user_modes[chat_id] = "team"
        send_msg(
            chat_id,
            "\U0001f3e2 AI SMM Agentligingiz 24/7 serverda ishga tushdi!\n\n"
            "Hozirgi rejim: Jamoaviy topshiriq (6 nafar xodim).\n"
            "Istalgan mijoz yoki reklama topshirig'ini yozing - Sardor va jamoa bajarib beradi.\n"
            "Yoki pastdagi tugmalardan bitta xodimni tanlab 1-ga-1 gaplashing.",
            reply_markup=KEYBOARD,
        )
        return

    if text in BUTTON_TO_MODE:
        mode = BUTTON_TO_MODE[text]
        user_modes[chat_id] = mode
        if mode == "team":
            send_msg(chat_id, "\U0001f3e2 Jamoaviy rejim yoqildi. Topshiriq yozing:")
        else:
            ag = AGENTS[mode]
            send_msg(chat_id, f"{ag['emoji']} {ag['name']} ({ag['role']}) bilan 1-ga-1 suhbatdasiz. Savolingizni yozing:")
        return

    mode = user_modes.get(chat_id, "team")
    try:
        if mode == "team":
            send_msg(chat_id, "\u23f3 Direktor Sardor topshiriqni ko'rib chiqmoqda...")
            director_plan = ask_agent("direktor", text)
            send_msg(chat_id, f"\U0001f454 SARDOR (Direktor):\n\n{director_plan}")

            send_msg(chat_id, "\U0001f680 5 nafar mutaxassis ishga kirishdi, natijalar ketma-ket keladi...")

            report_parts = [
                f"# AI SMM AGENTLIGI HISOBOTI\nSana: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
                f"## Topshiriq:\n{text}\n",
                f"## Sardor (Direktor)\n{director_plan}\n",
            ]

            for key in SPECIALISTS:
                ag = AGENTS[key]
                prompt = (
                    f"Mijoz topshirig'i:\n{text}\n\n"
                    f"Direktor Sardorning ko'rsatmasi:\n{director_plan}\n\n"
                    "Endi faqat o'z yo'nalishing bo'yicha amaliy, aniq va foydali natija tayyorla."
                )
                try:
                    ans = ask_agent(key, prompt)
                except Exception as e:
                    ans = f"Vaqtinchalik xatolik: {e}"

                send_msg(chat_id, f"{ag['emoji']} {ag['name'].upper()} ({ag['role']}):\n\n{ans}")
                report_parts.append(f"## {ag['name']} ({ag['role']})\n{ans}\n")

            full_report = "\n---\n\n".join(report_parts)
            send_doc(chat_id, "SMM_Agentlik_Hisobot.md", full_report, "\u2705 Barcha xodimlar ishni yakunladi!")
        else:
            ag = AGENTS[mode]
            send_msg(chat_id, f"\u23f3 {ag['emoji']} {ag['name']} javob tayyorlamoqda...")
            ans = ask_agent(mode, text)
            send_msg(chat_id, f"{ag['emoji']} {ag['name'].upper()} ({ag['role']}):\n\n{ans}")
    except Exception as err:
        send_msg(chat_id, f"\u274c Xatolik yuz berdi: {err}")


def register_webhook(base_url):
    webhook_url = f"{base_url.rstrip('/')}/webhook"
    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            f"{TG_API}/setWebhook",
            json={"url": webhook_url, "drop_pending_updates": True},
        )
        return {"webhook_url": webhook_url, "telegram_response": r.json()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if TELEGRAM_TOKEN and RENDER_URL:
        try:
            res = register_webhook(RENDER_URL)
            print("Webhook avtomatik ulandi:", res)
        except Exception as e:
            print("Webhook ulashda ogohlantirish:", e)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    return {
        "status": "AI SMM Agentligi ishlayapti!",
        "telegram_token_ok": bool(TELEGRAM_TOKEN),
        "gemini_key_ok": bool(GEMINI_API_KEY),
        "model": MODEL_NAME,
    }


@app.get("/setup")
def setup_webhook(request: Request):
    base = RENDER_URL or str(request.base_url).rstrip("/").replace("http://", "https://")
    return register_webhook(base)


@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    upd_id = data.get("update_id")
    if upd_id in processed_updates:
        return {"ok": True}
    if upd_id is not None:
        processed_updates.add(upd_id)
        if len(processed_updates) > 1000:
            processed_updates.clear()

    msg = data.get("message")
    if msg and "text" in msg:
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        background_tasks.add_task(handle_message, chat_id, text)

    return {"ok": True}
