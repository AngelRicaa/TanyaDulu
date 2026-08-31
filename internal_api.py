from flask import Flask, request, jsonify
import base64
import io
from PIL import Image
import pytesseract

import risk_engine as engine
import conversational_ai as cai

app = Flask(__name__)

RISK_FACTORS = engine.load_risk_factors()
SESSIONS = {}


@app.route("/process", methods=["POST"])
def process_message():
    data = request.get_json()
    sender = data.get("sender")
    text = data.get("text", "")

    reply = handle_text_message(sender, text)
    return jsonify({"reply": reply})


@app.route("/process_image", methods=["POST"])
def process_image():
    data = request.get_json()
    sender = data.get("sender")
    image_base64 = data.get("image_base64", "")

    try:
        image_bytes = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_bytes))
        extracted_text = pytesseract.image_to_string(image, lang="ind+eng")
    except Exception as e:
        return jsonify({"reply": f"Maaf, gagal membaca gambar ({str(e)}). Coba kirim ulang atau ketik info produknya manual."})

    extracted_text = extracted_text.strip()

    if not extracted_text:
        return jsonify({
            "reply": "Maaf, saya tidak bisa membaca teks dari gambar ini. "
                     "Coba kirim screenshot yang lebih jelas, atau ketik info produknya manual."
        })

    reply = handle_text_message(sender, extracted_text)
    reply = f"[Teks terbaca dari gambar]\n\"{extracted_text[:150]}{'...' if len(extracted_text) > 150 else ''}\"\n\n{reply}"

    return jsonify({"reply": reply})


def handle_text_message(sender, text):
    session_data = SESSIONS.get(sender)

    if session_data is None:
        SESSIONS[sender] = {"stage": "awaiting_case_info"}
        return (
            "Halo! Saya TanyaDulu 🛡️, siap bantu cek risiko transaksi online.\n\n"
            "Boleh ceritakan: nama barang, harga yang ditawarkan, dan kira-kira "
            "harga pasar normalnya berapa? Contoh: 'iPhone 15, harga 2jt, harga pasar 18jt'"
        )

    stage = session_data.get("stage")

    if stage == "awaiting_case_info":
        return start_conversation_session(sender, text)
    elif stage == "in_conversation":
        return continue_conversation_session(sender, text)
    else:
        SESSIONS[sender] = {"stage": "awaiting_case_info"}
        return "Ketik ulang info transaksinya ya, saya mulai dari awal."


def start_conversation_session(sender, text):
    price, market_price, urgency_detected = parse_case_info(text)

    if price is None or market_price is None:
        return (
            "Maaf, saya belum bisa membaca harganya. Coba tulis dengan format, "
            "misal: 'iPhone 15, harga 2jt, harga pasar 18jt'."
        )

    initial_factors = cai.extract_screenshot_factors(price, market_price, urgency_detected)
    session = cai.ConversationSession(RISK_FACTORS, initial_factors)

    SESSIONS[sender] = {"stage": "in_conversation", "session": session}

    return ask_next_or_finish(sender)


def continue_conversation_session(sender, answer_text):
    session_data = SESSIONS[sender]
    session = session_data["session"]

    answer = normalize_yes_no(answer_text)
    current_question = session.next_question()
    if current_question:
        session.answer(current_question["field"], answer)

    return ask_next_or_finish(sender)


def ask_next_or_finish(sender):
    session_data = SESSIONS[sender]
    session = session_data["session"]

    if session.is_conclusive():
        return finish_conversation(sender)

    next_q = session.next_question()
    if next_q is None:
        return finish_conversation(sender)

    return next_q["question"]


def finish_conversation(sender):
    session_data = SESSIONS[sender]
    session = session_data["session"]

    score, category, reasons = session.current_assessment()
    recommendation = cai.RECOMMENDATIONS[category]

    reply = f"{recommendation}\n\nSkor risiko: {score}"
    if reasons:
        reply += "\nFaktor terdeteksi:\n" + "\n".join(f"- {r}" for r in reasons)

    del SESSIONS[sender]
    return reply


def parse_case_info(text):
    import re

    price_match = re.search(r"harga\s+(\d+(?:\.\d+)?)\s*(jt|rb)?(?!\s*pasar)", text.lower())
    market_match = re.search(r"pasar\s+(\d+(?:\.\d+)?)\s*(jt|rb)?", text.lower())

    if not price_match or not market_match:
        return None, None, None

    price = normalize_price_string(price_match.group(1), price_match.group(2))
    market_price = normalize_price_string(market_match.group(1), market_match.group(2))

    urgency_keywords = ["buruan", "stok tinggal", "segera", "sekarang juga"]
    urgency_detected = any(kw in text.lower() for kw in urgency_keywords)

    return price, market_price, urgency_detected


def normalize_price_string(number_str, unit):
    """
    Ubah angka harga jadi string 'Xjt'/'Xrb' yang bisa dibaca engine.to_rupiah().
    Kalau user nggak tulis satuan (jt/rb), pakai heuristik:
    - angka < 1000 dianggap dalam satuan ribu (mis. '750' -> 750rb)
    - angka >= 1000 dianggap sudah rupiah penuh (mis. '750000' -> 750000)
    """
    num = float(number_str)

    if unit == "jt":
        return f"{number_str}jt"
    elif unit == "rb":
        return f"{number_str}rb"
    else:
        if num < 1000:
            return f"{number_str}rb"
        else:
            return str(num)


def normalize_yes_no(text):
    text_lower = text.strip().lower()
    yes_words = ["ya", "iya", "yes", "benar", "betul"]
    if any(w in text_lower for w in yes_words):
        return "yes"
    return "no"


if __name__ == "__main__":
    app.run(port=5000, debug=True)
