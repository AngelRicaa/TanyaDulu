import pandas as pd
from collections import Counter

SEVERITY_WEIGHTS = {
    "low": 5,
    "medium": 15,
    "high": 25,
    "critical": 40,
}

THRESHOLDS = {
    "low": (0, 25),
    "medium": (26, 60),
    "high": (61, 9999),
}


def load_risk_factors(path="risk_factors.csv"):
    df = pd.read_csv(path, sep=";")
    unique = df.drop_duplicates(subset="factor_id").set_index("factor_id")
    return unique[["factor", "severity", "description"]].to_dict(orient="index")


def to_rupiah(s):
    s = str(s).strip().lower()
    if "jt" in s:
        return float(s.replace("jt", "")) * 1_000_000
    elif "rb" in s:
        return float(s.replace("rb", "")) * 1_000
    return float(s)


def detect_factors(case, risk_factors, users=None):
    detected = []

    price = to_rupiah(case["price"])
    market = to_rupiah(case["market_price"])
    ratio = price / market if market else 1

    if case["payment"] == "transfer":
        detected.append("R01")

    if case["urgency"] == "yes":
        detected.append("R02")

    if ratio < 0.6:
        detected.append("R03")

    if case["seller_known"] == "no":
        detected.append("R04")

    if case["evidence"] == "no":
        detected.append("R05")

    if users is not None and "seller_user_id" in case:
        user_row = users.loc[users["user_id"] == case["seller_user_id"]]
        if not user_row.empty:
            if user_row.iloc[0]["verified_account"] == False:
                detected.append("R09")
            if user_row.iloc[0]["account_age_category"] in ["very_new", "new"]:
                detected.append("R08")

    return detected


def score_case(detected_factor_ids, risk_factors):
    total = 0
    reasons = []
    for fid in detected_factor_ids:
        info = risk_factors.get(fid)
        if not info:
            continue
        weight = SEVERITY_WEIGHTS.get(info["severity"], 0)
        total += weight
        reasons.append(f"{info['factor']} ({info['severity']}, +{weight})")

    category = "low"
    for cat, (lo, hi) in THRESHOLDS.items():
        if lo <= total <= hi:
            category = cat
            break

    return total, category, reasons


def assess(case, risk_factors, users=None):
    factor_ids = detect_factors(case, risk_factors, users)
    score, category, reasons = score_case(factor_ids, risk_factors)
    return {
        "score": score,
        "category": category,
        "detected_factors": factor_ids,
        "reasons": reasons,
    }


def validate(cases_path="shopping_cases.csv", factors_path="risk_factors.csv", users_path="social_media_users_cleaned.csv"):
    risk_factors = load_risk_factors(factors_path)
    cases = pd.read_csv(cases_path)
    users = pd.read_csv(users_path)

    predicted = []
    for _, case in cases.iterrows():
        result = assess(case, risk_factors, users)
        predicted.append(result["category"])

    cases["predicted_label"] = predicted
    cases["match"] = cases["label"] == cases["predicted_label"]

    accuracy = cases["match"].mean() * 100
    print(f"Total kasus diuji : {len(cases)}")
    print(f"Akurasi vs label  : {accuracy:.2f}%\n")

    print("Confusion (label asli vs prediksi engine):")
    print(pd.crosstab(cases["label"], cases["predicted_label"], margins=True))

    print("\nContoh kasus yang TIDAK cocok (untuk dicek manual):")
    mismatches = cases[~cases["match"]].head(5)
    print(mismatches[["case_id", "product", "price", "market_price",
                       "seller_known", "payment", "urgency", "evidence",
                       "label", "predicted_label"]])

    return cases


TEXT_FACTOR_KEYWORDS = {
    "R06": ["otp", "kode verifikasi"],
    "R07": ["pin atm", "pin mobile banking", "pin kartu", "password"],
    "R01": ["transfer dulu", "transfer langsung", "dp 100%", "transfer sekarang", "harus transfer"],
    "R02": ["buruan", "stok tinggal", "segera", "sebelum kehabisan", "menit lagi", "jam ke depan"],
    "R11": ["foto ktp", "kirim ktp", "data ktp", "identitas pribadi"],
    "R12": ["klik link", "klik tautan", "link berikut", "tautan berikut"],
}


def detect_text_factors(message):
    message_lower = str(message).lower()
    detected = []
    for factor_id, keywords in TEXT_FACTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in message_lower:
                detected.append(factor_id)
                break
    return detected


def assess_text(message, risk_factors):
    factor_ids = detect_text_factors(message)
    score, category, reasons = score_case(factor_ids, risk_factors)
    return {
        "score": score,
        "category": category,
        "detected_factors": factor_ids,
        "reasons": reasons,
    }


def validate_text(messages_path="scam_messages.csv", factors_path="risk_factors.csv"):
    risk_factors = load_risk_factors(factors_path)
    messages = pd.read_csv(messages_path, sep=";")

    predicted = []
    for _, row in messages.iterrows():
        result = assess_text(row["message"], risk_factors)
        predicted.append("scam" if result["score"] > 0 else "legitimate")

    messages["predicted_label"] = predicted
    messages["match"] = messages["label"] == messages["predicted_label"]

    accuracy = messages["match"].mean() * 100
    print(f"Total pesan diuji : {len(messages)}")
    print(f"Akurasi vs label  : {accuracy:.2f}%\n")

    print("Confusion (label asli vs prediksi engine):")
    print(pd.crosstab(messages["label"], messages["predicted_label"], margins=True))

    return messages


if __name__ == "__main__":
    print("=== CONTOH: assess satu kasus manual ===")
    risk_factors = load_risk_factors("risk_factors.csv")
    contoh_kasus = {
        "product": "iPhone 15 Pro",
        "price": "2jt",
        "market_price": "15jt",
        "seller_known": "no",
        "payment": "transfer",
        "urgency": "yes",
        "evidence": "no",
        "seller_user_id": "U00001",
    }
    users = pd.read_csv("social_media_users_cleaned.csv")
    hasil = assess(contoh_kasus, risk_factors, users)
    print(f"Skor     : {hasil['score']}")
    print(f"Kategori : {hasil['category']}")
    print("Alasan   :")
    for r in hasil["reasons"]:
        print(f"  - {r}")

    print("\n\n=== VALIDASI KE shopping_cases.csv ===")
    validate()

    print("\n\n=== CONTOH: assess satu pesan teks bebas ===")
    contoh_pesan = "Mohon kirimkan kode OTP yang baru saja masuk ke HP Anda."
    hasil_teks = assess_text(contoh_pesan, risk_factors)
    print(f"Pesan    : {contoh_pesan}")
    print(f"Skor     : {hasil_teks['score']}")
    print(f"Kategori : {hasil_teks['category']}")

    print("\n\n=== VALIDASI KE scam_messages.csv ===")
    validate_text()
