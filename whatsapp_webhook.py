import os
import requests
from flask import Flask, request, jsonify

import risk_engine_clean as engine
import conversational_ai as cai

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "tanyadulu_verify_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")

RISK_FACTORS = engine.load_risk_factors()

# Simpan sesi percakapan aktif per nomor pengirim (in-memory, cukup untuk prototype)
# Untuk production sebaiknya diganti database (Redis/PostgreSQL) supaya tidak hilang saat server restart.
SESSIONS = {}


def send_whatsapp_message(to_number, text):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text},
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        print(f"[WhatsApp API error] {response.status_code}: {response.text}")
    return response


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return jsonify({"status": "ignored"}), 200

        message = value["messages"][0]
        sender = message["from"]
        msg_type = message["type"]

    except (KeyError, IndexError):
        return jsonify({"status": "invalid payload"}), 200

    if msg_type == "text":
        text = message["text"]["body"]
        handle_text_message(sender, text)
    elif msg_type == "image":
        send_whatsapp_message(
            sender,
            "Terima kasih sudah kirim gambar. Fitur analisis screenshot otomatis "
            "masih dalam pengembangan — untuk sekarang, tolong ketik ringkasan info "
            "produknya ya (nama barang, harga, dan harga pasar sekitar berapa).",
        )
    else:
        send_whatsapp_message(sender, "Maaf, saya baru bisa memproses pesan teks dan gambar untuk saat ini.")

    return jsonify({"status": "ok"}), 200


def handle_text_message(sender, text):
    session_data = SESSIONS.get(sender)

    if session_data is None:
        # Belum ada sesi aktif -> mulai sesi baru, anggap pesan pertama berisi info produk
        SESSIONS[sender] = {"stage": "awaiting_case_info", "raw_text": text}
        send_whatsapp_message(
            sender,
            "Halo! Saya TanyaDulu 🛡️, siap bantu cek risiko transaksi online.\n\n"
            "Boleh ceritakan: nama barang, harga yang ditawarkan, dan kira-kira "
            "harga pasar normalnya berapa?",
        )
        return

    stage = session_data.get("stage")

    if stage == "awaiting_case_info":
        start_conversation_session(sender, text)
    elif stage == "in_conversation":
        continue_conversation_session(sender, text)
    else:
        send_whatsapp_message(sender, "Ketik ulang info transaksinya ya, saya mulai dari awal.")
        SESSIONS[sender] = {"stage": "awaiting_case_info"}


def start_conversation_session(sender, text):
    """
    Untuk prototype: parsing harga dari teks bebas masih sederhana.
    Nanti ini digantikan modul OCR/NLP yang lebih baik di Fase 5.
    """
    price, market_price, urgency_detected = parse_case_info(text)

    if price is None or market_price is None:
        send_whatsapp_message(
            sender,
            "Maaf, saya belum bisa membaca harganya. Coba tulis dengan format, "
            "misal: 'iPhone 15, harga 2jt, harga pasar 18jt'.",
        )
        return

    initial_factors = cai.extract_screenshot_factors(price, market_price, urgency_detected)
    session = cai.ConversationSession(RISK_FACTORS, initial_factors)

    SESSIONS[sender] = {"stage": "in_conversation", "session": session}

    ask_next_or_finish(sender)


def continue_conversation_session(sender, answer_text):
    session_data = SESSIONS[sender]
    session = session_data["session"]

    answer = normalize_yes_no(answer_text)
    current_question = session.next_question()
    if current_question:
        session.answer(current_question["field"], answer)

    ask_next_or_finish(sender)


def ask_next_or_finish(sender):
    session_data = SESSIONS[sender]
    session = session_data["session"]

    if session.is_conclusive():
        finish_conversation(sender)
        return

    next_q = session.next_question()
    if next_q is None:
        finish_conversation(sender)
        return

    send_whatsapp_message(sender, next_q["question"])


def finish_conversation(sender):
    session_data = SESSIONS[sender]
    session = session_data["session"]

    score, category, reasons = session.current_assessment()
    recommendation = cai.RECOMMENDATIONS[category]

    reply = f"{recommendation}\n\nSkor risiko: {score}"
    if reasons:
        reply += "\nFaktor terdeteksi:\n" + "\n".join(f"- {r}" for r in reasons)

    send_whatsapp_message(sender, reply)

    del SESSIONS[sender]


def parse_case_info(text):
    """
    Placeholder parsing sederhana. Ganti dengan NLP/regex yang lebih baik,
    atau modul OCR (Fase 5) kalau inputnya berupa screenshot.
    """
    import re

    price_match = re.search(r"harga\s+(\d+(?:\.\d+)?)\s*(jt|rb)", text.lower())
    market_match = re.search(r"pasar\s+(\d+(?:\.\d+)?)\s*(jt|rb)", text.lower())

    if not price_match or not market_match:
        return None, None, None

    price = f"{price_match.group(1)}{price_match.group(2)}"
    market_price = f"{market_match.group(1)}{market_match.group(2)}"

    urgency_keywords = ["buruan", "stok tinggal", "segera", "sekarang juga"]
    urgency_detected = any(kw in text.lower() for kw in urgency_keywords)

    return price, market_price, urgency_detected


def normalize_yes_no(text):
    text_lower = text.strip().lower()
    yes_words = ["ya", "iya", "yes", "benar", "betul"]
    if any(w in text_lower for w in yes_words):
        return "yes"
    return "no"


if __name__ == "__main__":
    app.run(port=5000, debug=True)
