import pandas as pd
import risk_engine as engine

QUESTION_BANK = [
    {
        "field": "seller_known",
        "factor_id": "R04",
        "question": "Apakah Anda mengenal penjual ini atau pernah membeli dari akun ini sebelumnya?",
        "triggers_factor_if": "no",
    },
    {
        "field": "payment",
        "factor_id": "R01",
        "question": "Apakah penjual meminta Anda transfer langsung sebelum barang dikirim?",
        "triggers_factor_if": "yes",
    },
    {
        "field": "evidence",
        "factor_id": "R05",
        "question": "Apakah penjual sudah memberikan foto atau video barang asli?",
        "triggers_factor_if": "no",
    },
]

RECOMMENDATIONS = {
    "high": (
        "🔴 RISIKO TINGGI — Sebaiknya jangan transfer dulu.\n"
        "- Minta penjual kirim foto/video barang asli dengan kertas bertuliskan tanggal hari ini.\n"
        "- Gunakan metode pembayaran yang punya perlindungan pembeli.\n"
        "- Jangan berikan OTP, PIN, password, atau foto identitas."
    ),
    "medium": (
        "🟡 PERLU DIPERIKSA — Ada beberapa hal yang sebaiknya dipastikan dulu.\n"
        "- Cek ulang identitas dan riwayat penjual.\n"
        "- Minta bukti tambahan sebelum melanjutkan transaksi."
    ),
    "low": (
        "🟢 RISIKO RENDAH — Transaksi tampak relatif aman.\n"
        "- Tetap waspada dan simpan bukti percakapan sebagai jaga-jaga."
    ),
}


def extract_screenshot_factors(price, market_price, urgency_detected):
    factors = []
    ratio = engine.to_rupiah(price) / engine.to_rupiah(market_price)
    if ratio < 0.6:
        factors.append("R03")
    if urgency_detected:
        factors.append("R02")
    return factors


class ConversationSession:
    def __init__(self, risk_factors, initial_factors=None):
        self.risk_factors = risk_factors
        self.known_factors = list(initial_factors) if initial_factors else []
        self.answered_fields = set()
        self.log = []

    def current_assessment(self):
        score, category, reasons = engine.score_case(self.known_factors, self.risk_factors)
        return score, category, reasons

    def next_question(self):
        for q in QUESTION_BANK:
            if q["field"] not in self.answered_fields:
                return q
        return None

    def answer(self, field, value):
        q = next((q for q in QUESTION_BANK if q["field"] == field), None)
        if q is None:
            return
        self.answered_fields.add(field)
        if value == q["triggers_factor_if"]:
            self.known_factors.append(q["factor_id"])
        self.log.append({"question": q["question"], "answer": value})

    def is_conclusive(self):
        score, category, _ = self.current_assessment()
        _, high_min = engine.THRESHOLDS["medium"][1], engine.THRESHOLDS["high"][0]
        return score >= high_min

    def run(self, answer_source):
        while True:
            if self.is_conclusive():
                break
            q = self.next_question()
            if q is None:
                break
            value = answer_source(q)
            self.answer(q["field"], value)

        score, category, reasons = self.current_assessment()
        return {
            "score": score,
            "category": category,
            "reasons": reasons,
            "questions_asked": len(self.log),
            "log": self.log,
            "recommendation": RECOMMENDATIONS[category],
        }


def simulate_conversation(price, market_price, urgency_detected, scripted_answers):
    risk_factors = engine.load_risk_factors()
    initial_factors = extract_screenshot_factors(price, market_price, urgency_detected)

    session = ConversationSession(risk_factors, initial_factors)

    answers_iter = iter(scripted_answers)

    def answer_source(question):
        return next(answers_iter)

    print(f"[Screenshot] price={price}, market_price={market_price}, urgency_detected={urgency_detected}")
    print(f"[Faktor awal terdeteksi dari screenshot] {initial_factors}\n")

    result = session.run(answer_source)

    for turn in result["log"]:
        print(f"AI   : {turn['question']}")
        print(f"User : {turn['answer']}\n")

    print(f"Jumlah pertanyaan yang diajukan: {result['questions_asked']} dari {len(QUESTION_BANK)} maksimum")
    print(f"Skor akhir: {result['score']}")
    print(f"Kategori  : {result['category']}")
    print("Alasan:")
    for r in result["reasons"]:
        print(f"  - {r}")
    print(f"\n{result['recommendation']}")

    return result


def simulate_answer_from_case(case, field):
    """Terjemahkan nilai kolom di dataset jadi jawaban 'yes'/'no' seolah dijawab user."""
    if field == "seller_known":
        return case["seller_known"]
    elif field == "payment":
        return "yes" if case["payment"] == "transfer" else "no"
    elif field == "evidence":
        return case["evidence"]
    return "no"


def run_batch_validation(cases_path="shopping_cases.csv"):
    risk_factors = engine.load_risk_factors()
    cases = pd.read_csv(cases_path)

    results = []
    for _, case in cases.iterrows():
        initial_factors = extract_screenshot_factors(case["price"], case["market_price"], case["urgency"] == "yes")
        session = ConversationSession(risk_factors, initial_factors)

        def answer_source(question, case=case):
            return simulate_answer_from_case(case, question["field"])

        result = session.run(answer_source)
        results.append({
            "case_id": case["case_id"],
            "label": case["label"],
            "predicted_category": result["category"],
            "score": result["score"],
            "questions_asked": result["questions_asked"],
            "match": case["label"] == result["category"],
        })

    df = pd.DataFrame(results)
    accuracy = df["match"].mean() * 100
    avg_questions = df["questions_asked"].mean()

    print(f"Total kasus diuji        : {len(df)}")
    print(f"Akurasi vs label asli    : {accuracy:.2f}%")
    print(f"Rata-rata pertanyaan     : {avg_questions:.2f} dari {len(QUESTION_BANK)} maksimum")
    print(f"Kasus berhenti lebih dini: {(df['questions_asked'] < len(QUESTION_BANK)).sum()} dari {len(df)}\n")

    print("Distribusi jumlah pertanyaan per kategori hasil prediksi:")
    print(df.groupby("predicted_category")["questions_asked"].mean())

    print("\nConfusion matrix:")
    print(pd.crosstab(df["label"], df["predicted_category"], margins=True))

    return df


if __name__ == "__main__":
    print("=== SKENARIO 1: iPhone murah, mencurigakan (harus berhenti lebih awal jika sudah HIGH) ===\n")
    simulate_conversation(
        price="2jt",
        market_price="18jt",
        urgency_detected=True,
        scripted_answers=["no", "yes", "no"],
    )

    print("\n\n=== SKENARIO 2: Sofa bekas, wajar (kemungkinan LOW, tetap tanya semua) ===\n")
    simulate_conversation(
        price="2.8jt",
        market_price="3jt",
        urgency_detected=False,
        scripted_answers=["yes", "no", "yes"],
    )

    print("\n\n=== VALIDASI MENYELURUH KE 400 KASUS ===\n")
    run_batch_validation()
