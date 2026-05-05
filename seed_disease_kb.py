"""
DevloCare AI Health Assistant
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Disease Knowledge Base — Seeding Script

Populates the `disease_kb` table with curated, medically-responsible content
for all 24 diseases supported by the prediction model.

Design notes
------------
- Reuses the **same** database connection settings as the FastAPI app
  (DATABASE_URL from .env via `app.core.config.settings`). That means dev
  uses SQLite, staging/prod can switch to PostgreSQL or Supabase by editing
  `.env` only — no code changes.
- Reuses the project's SQLAlchemy ORM model (`app.db.models.DiseaseKB`) so
  the table schema lives in exactly one place and never drifts from the
  application's expectations.
- **Idempotent**: each disease is inserted only if `name_en` is not already
  present in the DB. Re-running the script is always safe.
- Creates the `disease_kb` table on-the-fly if it does not yet exist (via
  the same `init_db()` used at FastAPI startup).

Usage
-----
    python seed_disease_kb.py

Optional flags::

    python seed_disease_kb.py --force-update   # also overwrite existing rows
    python seed_disease_kb.py --dry-run        # show what would change
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import DiseaseKB
from app.db.session import AsyncSessionLocal, engine, init_db

logger = get_logger("seed_disease_kb")


# ──────────────────────────────────────────────────────────────
#  DISEASE KNOWLEDGE BASE  —  24 diseases, fully curated
#
#  Fields:
#    name_en          → English disease name
#    name_ur          → Urdu name (transliterated where needed)
#    specialist_type  → Relevant medical specialist
#    triage_category  → "self_care" | "see_gp" | "urgent_care"
#    care_tips_en     → 2–4 practical English care instructions
#    care_tips_ur     → Urdu translation of care tips
#    red_flags        → Comma-separated danger signs → go to hospital
# ──────────────────────────────────────────────────────────────
DISEASES: list[dict] = [
    {
        "name_en": "Psoriasis",
        "name_ur": "چنبل",
        "specialist_type": "Dermatologist",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Keep skin well-moisturised using fragrance-free thick creams or ointments, "
            "especially after bathing. Avoid triggers such as stress, alcohol, smoking, and "
            "certain medications like ibuprofen. Do not scratch or pick plaques as this can "
            "worsen the condition. See a dermatologist for prescription treatments such as "
            "topical corticosteroids or phototherapy."
        ),
        "care_tips_ur": (
            "جلد کو خوشبو سے پاک موئسچرائزر سے نم رکھیں، خاص طور پر نہانے کے بعد۔ "
            "تناؤ، شراب، سگریٹ اور بعض ادویات سے پرہیز کریں۔ "
            "خارش نہ کریں کیونکہ اس سے مرض بڑھ سکتا ہے۔ "
            "ماہرِ جلد سے ملیں اور تجویز کردہ دوا استعمال کریں۔"
        ),
        "red_flags": (
            "skin becomes bright red and covers most of the body (erythrodermic psoriasis), "
            "painful swollen joints suggesting psoriatic arthritis, "
            "fever along with widespread redness and pus-filled blisters (pustular psoriasis), "
            "skin infection with increasing warmth and pus"
        ),
    },
    {
        "name_en": "Varicose Veins",
        "name_ur": "ویریکوز رگیں",
        "specialist_type": "Vascular Surgeon",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Elevate your legs above heart level for 15–20 minutes several times a day to reduce "
            "swelling and discomfort. Wear compression stockings as advised and avoid standing or "
            "sitting for prolonged periods. Regular low-impact exercise like walking helps improve "
            "circulation. Maintain a healthy weight and avoid tight clothing around the waist or legs."
        ),
        "care_tips_ur": (
            "دن میں کئی بار 15-20 منٹ کے لیے ٹانگوں کو دل کی سطح سے اوپر اٹھائیں۔ "
            "ڈاکٹر کے مشورے سے کمپریشن موزے پہنیں۔ "
            "چلنے پھرنے کی مشق کریں اور زیادہ دیر کھڑے یا بیٹھے نہ رہیں۔ "
            "صحت مند وزن برقرار رکھیں اور ڈھیلے کپڑے پہنیں۔"
        ),
        "red_flags": (
            "sudden severe pain or swelling in one leg suggesting deep vein thrombosis, "
            "bleeding from a varicose vein that does not stop with pressure, "
            "skin ulcer or open sore near the ankle, "
            "redness warmth and hardness along a vein suggesting superficial thrombophlebitis, "
            "leg turns pale or cold (possible arterial blockage)"
        ),
    },
    {
        "name_en": "Typhoid",
        "name_ur": "ٹائیفائیڈ",
        "specialist_type": "Infectious Disease Specialist / General Physician",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Seek medical care immediately — typhoid requires antibiotic treatment prescribed by a "
            "doctor. Drink plenty of clean, boiled, or bottled water and oral rehydration solution "
            "to prevent dehydration. Eat soft, easily digestible foods and avoid raw or street food. "
            "Rest completely, take paracetamol for fever, and never share utensils with others."
        ),
        "care_tips_ur": (
            "فوری طور پر ڈاکٹر سے ملیں کیونکہ ٹائیفائیڈ میں اینٹی بایوٹک ضروری ہے۔ "
            "صاف اور ابلا ہوا پانی پیتے رہیں اور ORS استعمال کریں۔ "
            "نرم اور آسانی سے ہضم ہونے والی خوراک کھائیں۔ "
            "مکمل آرام کریں اور بخار کے لیے پیراسیٹامول لیں۔"
        ),
        "red_flags": (
            "persistent high fever above 39°C for more than 3 days despite medication, "
            "severe abdominal pain or rigidity (possible intestinal perforation), "
            "blood in stool or vomit, "
            "extreme weakness or inability to stay awake, "
            "confusion or altered consciousness, "
            "signs of shock such as rapid weak pulse and cold skin"
        ),
    },
    {
        "name_en": "Chicken Pox",
        "name_ur": "چکن پاکس / چیچک",
        "specialist_type": "General Physician / Pediatrician",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Keep nails trimmed and avoid scratching blisters to prevent secondary skin infection "
            "and scarring. Apply calamine lotion to soothe itching and take antihistamine tablets "
            "if recommended by a doctor. Stay home and away from unvaccinated individuals, pregnant "
            "women, and immunocompromised people as chickenpox is highly contagious. Take paracetamol "
            "for fever — never give aspirin to children."
        ),
        "care_tips_ur": (
            "ناخن چھوٹے رکھیں اور چھالوں کو نہ کھجائیں تاکہ انفیکشن نہ ہو۔ "
            "خارش کے لیے کیلامین لوشن لگائیں اور ڈاکٹر کے مشورے سے اینٹی ہسٹامین لیں۔ "
            "دوسروں سے دور رہیں کیونکہ یہ بیماری متعدی ہے۔ "
            "بخار کے لیے پیراسیٹامول دیں — بچوں کو ایسپرین ہرگز نہ دیں۔"
        ),
        "red_flags": (
            "high fever above 39°C for more than 4 days, "
            "blisters become very red swollen or filled with pus (bacterial superinfection), "
            "difficulty breathing or chest pain, "
            "severe headache with neck stiffness (possible meningitis), "
            "confusion lethargy or seizures, "
            "rash on eyelids or inside eyes"
        ),
    },
    {
        "name_en": "Impetigo",
        "name_ur": "امپیٹیگو / جلدی پھوڑے",
        "specialist_type": "Dermatologist / General Physician",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Keep the affected area clean by gently washing with soap and water and patting dry. "
            "Do not touch or scratch sores, and cover them with a clean gauze bandage to prevent "
            "spreading. Antibiotic cream or oral antibiotics prescribed by a doctor are usually "
            "required — do not self-medicate. Wash hands frequently and avoid sharing towels, "
            "clothing, or bedding with others."
        ),
        "care_tips_ur": (
            "متاثرہ جگہ کو صابن اور پانی سے صاف رکھیں اور خشک کریں۔ "
            "زخموں کو ہاتھ نہ لگائیں اور صاف پٹی سے ڈھانپ کر رکھیں۔ "
            "ڈاکٹر کی تجویز کردہ اینٹی بایوٹک کریم یا گولیاں استعمال کریں۔ "
            "بار بار ہاتھ دھوئیں اور تولیہ یا کپڑے کسی سے شیئر نہ کریں۔"
        ),
        "red_flags": (
            "sores rapidly spreading to cover large areas of the body, "
            "high fever above 38.5°C, "
            "swollen lymph nodes in the neck or armpits, "
            "sores becoming deep painful ulcers (ecthyma), "
            "decreased urination or puffy face and legs (possible kidney complication)"
        ),
    },
    {
        "name_en": "Dengue",
        "name_ur": "ڈینگی بخار",
        "specialist_type": "Infectious Disease Specialist / General Physician",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Go to a hospital or clinic immediately for blood tests to confirm dengue and monitor "
            "platelet count. Drink plenty of fluids — water, ORS, coconut water — to prevent "
            "dangerous dehydration. Take paracetamol only for fever and pain — never take aspirin, "
            "ibuprofen, or naproxen as these increase bleeding risk. Rest completely and use "
            "mosquito repellent and nets to prevent spreading to others."
        ),
        "care_tips_ur": (
            "فوری طور پر ہسپتال جائیں اور خون کا ٹیسٹ کروائیں۔ "
            "پانی، ORS اور ناریل کا پانی وافر مقدار میں پیتے رہیں۔ "
            "صرف پیراسیٹامول استعمال کریں — ایسپرین یا بروفن بالکل نہ لیں کیونکہ خون بہنے کا خطرہ بڑھتا ہے۔ "
            "مکمل آرام کریں اور مچھر دانی استعمال کریں۔"
        ),
        "red_flags": (
            "bleeding from nose mouth or gums, "
            "blood in urine or black tarry stools, "
            "platelet count falling below 50000, "
            "severe abdominal pain or persistent vomiting, "
            "rapid breathing difficulty or chest pain, "
            "cold clammy skin with rapid weak pulse (dengue shock syndrome), "
            "sudden high fever returning after 24 hours of feeling better"
        ),
    },
    {
        "name_en": "Fungal Infection",
        "name_ur": "فنگل انفیکشن / کھمبی کی بیماری",
        "specialist_type": "Dermatologist",
        "triage_category": "self_care",
        "care_tips_en": (
            "Keep the affected area clean and completely dry — fungi thrive in warm, moist "
            "environments. Apply an over-the-counter antifungal cream (clotrimazole or miconazole) "
            "twice daily for at least 2–4 weeks, even after the rash clears. Wear breathable cotton "
            "clothing and change socks and underwear daily. Avoid sharing towels, shoes, or personal "
            "items, and consult a doctor if there is no improvement after 2 weeks."
        ),
        "care_tips_ur": (
            "متاثرہ جگہ کو صاف اور خشک رکھیں کیونکہ فنگس نمی میں پنپتی ہے۔ "
            "اینٹی فنگل کریم (کلوٹریمازول) دن میں دو بار 2-4 ہفتوں تک لگائیں۔ "
            "سوتی کپڑے پہنیں اور جرابیں روزانہ تبدیل کریں۔ "
            "اگر 2 ہفتوں میں فرق نہ پڑے تو ڈاکٹر سے ملیں۔"
        ),
        "red_flags": (
            "infection spreading rapidly despite 2 weeks of antifungal cream, "
            "fever or pus suggesting bacterial superinfection, "
            "nail infection causing severe pain or complete nail loss, "
            "hair loss in patches (tinea capitis requiring oral medication), "
            "infection in a diabetic or immunocompromised patient not improving"
        ),
    },
    {
        "name_en": "Common Cold",
        "name_ur": "نزلہ زکام",
        "specialist_type": "General Physician",
        "triage_category": "self_care",
        "care_tips_en": (
            "Rest well and drink plenty of warm fluids such as water, herbal tea, or warm broth "
            "to stay hydrated and soothe the throat. Use saline nasal drops or steam inhalation to "
            "relieve congestion. Take paracetamol or ibuprofen for fever or body aches. Cold "
            "symptoms usually resolve in 7–10 days; avoid antibiotics as the common cold is caused "
            "by a virus."
        ),
        "care_tips_ur": (
            "آرام کریں اور گرم پانی، قہوہ یا یخنی وافر مقدار میں پیئیں۔ "
            "ناک بند ہو تو نمکین قطرے یا بھاپ لیں۔ "
            "بخار یا درد کے لیے پیراسیٹامول لیں۔ "
            "یہ وائرل بیماری ہے اس لیے اینٹی بایوٹک نہ لیں — 7-10 دن میں خود ٹھیک ہو جاتی ہے۔"
        ),
        "red_flags": (
            "fever above 39°C lasting more than 3 days, "
            "severe headache with neck stiffness, "
            "difficulty breathing or chest pain, "
            "symptoms suddenly worsening after initial improvement (possible secondary infection), "
            "ear pain or significant hearing loss, "
            "confusion or extreme lethargy especially in elderly or children"
        ),
    },
    {
        "name_en": "Pneumonia",
        "name_ur": "نمونیہ",
        "specialist_type": "Pulmonologist / General Physician",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Seek immediate medical attention — pneumonia requires a doctor's evaluation, chest "
            "X-ray, and often antibiotic treatment. Rest as much as possible and drink plenty of "
            "fluids to loosen mucus. Take prescribed antibiotics for the full course even if you "
            "feel better earlier. Use paracetamol for fever; avoid cold environments and smoking."
        ),
        "care_tips_ur": (
            "فوری ڈاکٹر سے ملیں — نمونیہ میں سینے کا ایکسرے اور اینٹی بایوٹک ضروری ہو سکتی ہیں۔ "
            "مکمل آرام کریں اور بلغم ڈھیلا کرنے کے لیے وافر پانی پیئیں۔ "
            "ڈاکٹر کی دوا پورے دورانیے تک لیں چاہے آرام محسوس ہو۔ "
            "بخار کے لیے پیراسیٹامول لیں اور سگریٹ سے پرہیز کریں۔"
        ),
        "red_flags": (
            "severe difficulty breathing or rapid breathing above 30 breaths per minute, "
            "oxygen saturation below 94% on pulse oximeter, "
            "bluish lips or fingertips (cyanosis), "
            "confusion or altered consciousness, "
            "blood in coughed-up mucus, "
            "high fever above 39.5°C unresponsive to paracetamol, "
            "chest pain severe enough to prevent deep breathing"
        ),
    },
    {
        "name_en": "Dimorphic Hemorrhoids (Piles)",
        "name_ur": "بواسیر",
        "specialist_type": "General Surgeon / Gastroenterologist",
        "triage_category": "self_care",
        "care_tips_en": (
            "Increase dietary fibre by eating more fruits, vegetables, and whole grains, and drink "
            "at least 8 glasses of water daily to soften stools and reduce straining. Soak in a "
            "warm sitz bath for 10–15 minutes two to three times a day to relieve pain and swelling. "
            "Use over-the-counter haemorrhoid creams for temporary relief and avoid prolonged sitting "
            "on the toilet. See a doctor if symptoms persist beyond 2 weeks or rectal bleeding occurs."
        ),
        "care_tips_ur": (
            "فائبر والی خوراک کھائیں — پھل، سبزیاں اور اناج — اور روزانہ 8 گلاس پانی پیئیں۔ "
            "دن میں 2-3 بار گرم پانی میں 10-15 منٹ بیٹھیں تاکہ درد اور سوجن کم ہو۔ "
            "ہیمرائیڈ کریم عارضی آرام کے لیے استعمال کریں۔ "
            "اگر 2 ہفتوں میں آرام نہ ہو یا خون آئے تو ڈاکٹر سے ملیں۔"
        ),
        "red_flags": (
            "heavy rectal bleeding or blood clots in stool, "
            "haemorrhoid becoming hard very painful and cannot be pushed back (strangulated pile), "
            "fever and severe rectal pain suggesting abscess, "
            "significant unexplained weight loss alongside rectal bleeding (rule out colorectal cancer), "
            "anaemia symptoms such as extreme fatigue and pale skin from chronic bleeding"
        ),
    },
    {
        "name_en": "Arthritis",
        "name_ur": "گٹھیا / جوڑوں کا درد",
        "specialist_type": "Rheumatologist / Orthopedic Specialist",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Apply warm compresses to stiff joints in the morning and cold packs after activity to "
            "reduce swelling. Engage in gentle low-impact exercise such as swimming or walking to "
            "maintain joint mobility — avoid high-impact activities during flares. Take anti-"
            "inflammatory medication (ibuprofen) only with food and as directed by your doctor. "
            "Maintain a healthy weight to reduce pressure on joints, particularly the knees and hips."
        ),
        "care_tips_ur": (
            "صبح کے وقت جوڑوں پر گرم سینک کریں اور ورزش کے بعد ٹھنڈی پٹی لگائیں۔ "
            "تیراکی یا چہل قدمی جیسی ہلکی ورزش جوڑوں کو متحرک رکھتی ہے۔ "
            "سوزش کی دوا صرف ڈاکٹر کی ہدایت سے کھانے کے ساتھ لیں۔ "
            "صحت مند وزن رکھیں تاکہ گھٹنوں اور کولہوں پر دباؤ کم ہو۔"
        ),
        "red_flags": (
            "sudden severe joint swelling with redness and fever (possible septic arthritis — emergency), "
            "joint becomes completely immobile, "
            "unexplained weight loss and fatigue alongside joint pain, "
            "new rash on cheeks or skin with joint pain (possible lupus), "
            "eye redness or pain alongside joint symptoms (uveitis)"
        ),
    },
    {
        "name_en": "Acne",
        "name_ur": "مہاسے / کیل",
        "specialist_type": "Dermatologist",
        "triage_category": "self_care",
        "care_tips_en": (
            "Wash the affected area twice daily with a gentle, non-comedogenic cleanser — avoid "
            "scrubbing hard as this irritates skin. Do not pop or squeeze pimples as this leads to "
            "scarring and deeper infection. Use oil-free, non-comedogenic moisturiser and sunscreen. "
            "For persistent or severe acne, consult a dermatologist for topical retinoids, "
            "benzoyl peroxide, or antibiotic treatment."
        ),
        "care_tips_ur": (
            "دن میں دو بار ہلکے فیس واش سے منہ دھوئیں — زور سے نہ رگڑیں۔ "
            "پھنسیوں کو نہ دبائیں کیونکہ اس سے نشان پڑتے ہیں۔ "
            "آئل فری موئسچرائزر اور سن اسکرین استعمال کریں۔ "
            "اگر مہاسے شدید ہوں تو ڈرماٹولوجسٹ سے مشورہ کریں۔"
        ),
        "red_flags": (
            "large painful cysts or nodules covering most of the face or back (nodulocystic acne), "
            "acne accompanied by irregular periods and excess facial hair in women (possible PCOS), "
            "severe scarring or deep pitting developing rapidly, "
            "signs of skin infection with fever and spreading redness"
        ),
    },
    {
        "name_en": "Bronchial Asthma",
        "name_ur": "دمہ",
        "specialist_type": "Pulmonologist / Allergist",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Always carry your reliever inhaler (salbutamol/blue inhaler) and use it at the first "
            "sign of breathing difficulty. Identify and avoid your triggers — dust, smoke, pet "
            "dander, strong smells, or cold air. Take controller medications (inhaled corticosteroids) "
            "every day as prescribed, even when feeling well. Seek emergency care immediately if "
            "your inhaler is not relieving symptoms within 15–20 minutes."
        ),
        "care_tips_ur": (
            "ریلیور انہیلر ہمیشہ ساتھ رکھیں اور سانس لینے میں تکلیف ہوتے ہی استعمال کریں۔ "
            "اپنے محرکات — دھول، دھواں، پالتو جانور — سے بچیں۔ "
            "کنٹرولر دوا ڈاکٹر کی ہدایت سے روزانہ لیں چاہے ٹھیک محسوس ہو۔ "
            "اگر انہیلر 15-20 منٹ میں آرام نہ دے تو فوری ہسپتال جائیں۔"
        ),
        "red_flags": (
            "severe breathlessness — unable to speak in full sentences, "
            "lips or fingertips turning blue (cyanosis), "
            "reliever inhaler giving no relief after 3 puffs, "
            "breathing rate above 25 breaths per minute, "
            "silent chest (no wheezing sounds despite severe distress — very dangerous), "
            "child showing chest wall retractions or nasal flaring, "
            "pulse above 120 beats per minute"
        ),
    },
    {
        "name_en": "Hypertension",
        "name_ur": "ہائی بلڈ پریشر",
        "specialist_type": "Cardiologist / General Physician",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Take blood pressure medications exactly as prescribed and never skip doses — stopping "
            "abruptly can be dangerous. Reduce salt intake to less than 5g per day, avoid processed "
            "foods, and eat plenty of fruits and vegetables. Exercise moderately for at least 30 "
            "minutes most days, avoid smoking, and limit alcohol. Monitor blood pressure at home "
            "regularly and keep a log to share with your doctor."
        ),
        "care_tips_ur": (
            "بلڈ پریشر کی دوا بالکل ڈاکٹر کی ہدایت کے مطابق لیں اور کبھی بند نہ کریں۔ "
            "نمک کم کریں، پروسیسڈ کھانوں سے پرہیز کریں اور پھل سبزیاں کھائیں۔ "
            "روزانہ 30 منٹ ہلکی ورزش کریں، سگریٹ بند کریں اور شراب سے پرہیز کریں۔ "
            "گھر میں بلڈ پریشر چیک کریں اور ریکارڈ رکھیں۔"
        ),
        "red_flags": (
            "blood pressure reading above 180/120 mmHg, "
            "severe headache at the back of the head with visual disturbances (hypertensive crisis), "
            "chest pain or pressure (possible heart attack), "
            "sudden weakness or numbness on one side of the body (possible stroke), "
            "sudden confusion or difficulty speaking, "
            "shortness of breath at rest, "
            "blood in urine"
        ),
    },
    {
        "name_en": "Migraine",
        "name_ur": "آدھے سر کا درد / مائیگرین",
        "specialist_type": "Neurologist / General Physician",
        "triage_category": "see_gp",
        "care_tips_en": (
            "At the onset of a migraine, rest in a dark quiet room and apply a cold or warm compress "
            "to your head or neck. Take pain relief (ibuprofen or paracetamol) as early as possible "
            "at the first sign of an attack — waiting makes it less effective. Stay hydrated and "
            "avoid known triggers such as bright lights, strong smells, skipping meals, or poor "
            "sleep. If attacks are frequent or severe, see a neurologist for preventive medication."
        ),
        "care_tips_ur": (
            "مائیگرین شروع ہوتے ہی اندھیرے اور خاموش کمرے میں آرام کریں۔ "
            "جلد از جلد درد کی دوا (آئبوپروفن یا پیراسیٹامول) لیں۔ "
            "ہائیڈریٹ رہیں اور محرکات — تیز روشنی، بھوک، نیند کی کمی — سے بچیں۔ "
            "اگر درد اکثر ہو تو نیورولوجسٹ سے ملیں۔"
        ),
        "red_flags": (
            "sudden thunderclap headache — worst headache of life — (possible subarachnoid haemorrhage), "
            "headache with fever and neck stiffness (possible meningitis), "
            "headache after a head injury, "
            "new neurological symptoms such as weakness vision loss or speech difficulty, "
            "headache progressively worsening over days or weeks, "
            "first ever severe headache after age 50"
        ),
    },
    {
        "name_en": "Cervical Spondylosis",
        "name_ur": "گردن کا گٹھیا / سرواکل سپونڈیلوسس",
        "specialist_type": "Orthopedic Surgeon / Neurologist / Physiotherapist",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Apply a warm heating pad to the neck for 15–20 minutes to relieve stiffness, especially "
            "in the morning. Maintain good posture at your workstation — keep screens at eye level "
            "and take breaks every 30–45 minutes to stretch your neck. A physiotherapist-prescribed "
            "exercise programme can significantly improve symptoms over time. Avoid sleeping on your "
            "stomach or using pillows that are too high or too flat."
        ),
        "care_tips_ur": (
            "گردن پر 15-20 منٹ گرم پٹی لگائیں خاص طور پر صبح کے وقت۔ "
            "کام کرتے وقت صحیح پوزیشن رکھیں — اسکرین آنکھوں کی سطح پر ہو۔ "
            "فزیوتھیراپسٹ کی بتائی گئی مشقیں باقاعدگی سے کریں۔ "
            "پیٹ کے بل نہ سوئیں اور مناسب تکیہ استعمال کریں۔"
        ),
        "red_flags": (
            "sudden loss of bladder or bowel control (spinal cord compression emergency), "
            "severe weakness or paralysis in arms or legs, "
            "numbness or tingling spreading to both arms or legs, "
            "difficulty walking or balance problems, "
            "neck pain after trauma or fall"
        ),
    },
    {
        "name_en": "Jaundice",
        "name_ur": "یرقان",
        "specialist_type": "Gastroenterologist / Hepatologist",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Visit a doctor immediately for blood tests to identify the cause — jaundice is a "
            "symptom, not a disease, and the underlying cause must be treated. Rest fully, stay "
            "well-hydrated with clean water, and eat small light meals that are low in fat. Avoid "
            "all alcohol completely as it puts additional stress on the liver. Do not take any "
            "herbal remedies without medical advice as some can worsen liver damage."
        ),
        "care_tips_ur": (
            "فوری طور پر ڈاکٹر سے ملیں اور خون کے ٹیسٹ کروائیں کیونکہ یرقان ایک علامت ہے۔ "
            "مکمل آرام کریں، صاف پانی پیئیں اور ہلکی کم چکنائی والی خوراک کھائیں۔ "
            "شراب سے مکمل پرہیز کریں۔ "
            "بغیر ڈاکٹر کے مشورے کے کوئی جڑی بوٹی استعمال نہ کریں۔"
        ),
        "red_flags": (
            "severe confusion agitation or drowsiness (hepatic encephalopathy), "
            "vomiting blood or blood in stool, "
            "rapidly deepening jaundice over 24–48 hours, "
            "fever with right upper abdominal pain and jaundice (Charcot's triad — possible cholangitis), "
            "swollen abdomen with fluid (ascites), "
            "jaundice in a newborn that appears within 24 hours of birth or persists beyond 2 weeks"
        ),
    },
    {
        "name_en": "Malaria",
        "name_ur": "ملیریا",
        "specialist_type": "Infectious Disease Specialist / General Physician",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Go to a clinic or hospital immediately for a rapid diagnostic test — malaria "
            "requires specific prescription antimalarial drugs and must not be treated with home "
            "remedies alone. Take all prescribed medications for the complete course, even if "
            "fever stops. Drink plenty of fluids to prevent dehydration and use paracetamol to "
            "manage fever. Prevent mosquito bites with insecticide-treated nets and repellents."
        ),
        "care_tips_ur": (
            "فوری طور پر کلینک جائیں اور ملیریا ٹیسٹ کروائیں — خصوصی دوا ضروری ہے۔ "
            "ڈاکٹر کی دی گئی اینٹی ملیریل دوا پورا دورانیہ لیں چاہے بخار بند ہو جائے۔ "
            "وافر پانی پیئیں اور بخار کے لیے پیراسیٹامول لیں۔ "
            "مچھردانی اور مچھر بھگانے والی کریم استعمال کریں۔"
        ),
        "red_flags": (
            "high fever with shaking chills not improving with medication, "
            "altered consciousness confusion or seizures (cerebral malaria), "
            "very dark or cola-coloured urine (blackwater fever), "
            "severe anaemia with extreme pallor and weakness, "
            "rapid breathing and severe difficulty breathing, "
            "inability to keep oral medications down due to persistent vomiting, "
            "jaundice with high fever"
        ),
    },
    {
        "name_en": "Urinary Tract Infection",
        "name_ur": "پیشاب کی نالی کا انفیکشن",
        "specialist_type": "Urologist / General Physician",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Drink at least 2–3 litres of water per day to flush bacteria from the urinary tract. "
            "Urinate frequently and do not hold urine for long periods. See a doctor for a urine "
            "culture and appropriate antibiotic prescription — do not self-medicate, as the wrong "
            "antibiotic can worsen antibiotic resistance. Wipe from front to back after using the "
            "toilet and urinate after sexual intercourse to help prevent recurrence."
        ),
        "care_tips_ur": (
            "روزانہ 2-3 لیٹر پانی پیئیں تاکہ بیکٹیریا باہر نکل سکے۔ "
            "پیشاب کو روکے نہ رکھیں اور بار بار جائیں۔ "
            "ڈاکٹر سے پیشاب ٹیسٹ کروائیں اور تجویز کردہ اینٹی بایوٹک لیں — خود دوا نہ کریں۔ "
            "ٹوائلٹ کے بعد آگے سے پیچھے کی طرف صاف کریں۔"
        ),
        "red_flags": (
            "high fever above 38.5°C with back or flank pain (possible kidney infection — pyelonephritis), "
            "nausea and vomiting alongside urinary symptoms, "
            "blood in urine, "
            "symptoms not improving after 3 days of antibiotics, "
            "UTI symptoms in a pregnant woman, "
            "UTI in men or children (unusual and needs further investigation), "
            "recurrent UTIs more than 3 per year"
        ),
    },
    {
        "name_en": "Allergy",
        "name_ur": "الرجی",
        "specialist_type": "Allergist / Immunologist",
        "triage_category": "self_care",
        "care_tips_en": (
            "Identify and avoid your specific allergen triggers — common ones include dust mites, "
            "pollen, certain foods, or pet dander. For mild symptoms such as runny nose or itchy "
            "eyes, take non-drowsy antihistamine tablets (cetirizine or loratadine) available "
            "over-the-counter. Use nasal saline sprays and keep windows closed during high pollen "
            "seasons. See a doctor if symptoms are severe, frequent, or affecting daily life."
        ),
        "care_tips_ur": (
            "اپنے محرکات — دھول، جرگ، خوراک، پالتو جانور — کی پہچان کریں اور ان سے بچیں۔ "
            "ہلکی علامات کے لیے اینٹی ہسٹامین گولی (سیٹریزین) لیں۔ "
            "ناک کے لیے نمکین اسپرے استعمال کریں اور جرگ کے موسم میں کھڑکیاں بند رکھیں۔ "
            "اگر علامات شدید ہوں تو ڈاکٹر سے ملیں۔"
        ),
        "red_flags": (
            "sudden swelling of lips tongue or throat (angioedema), "
            "difficulty swallowing or breathing after exposure to an allergen (anaphylaxis — call emergency services immediately), "
            "hives spreading rapidly across the body with dizziness or fainting, "
            "severe wheezing or asthma attack triggered by an allergen, "
            "rapid drop in blood pressure with confusion (anaphylactic shock)"
        ),
    },
    {
        "name_en": "Gastroesophageal Reflux Disease",
        "name_ur": "معدے کا تیزاب / جی ای آر ڈی",
        "specialist_type": "Gastroenterologist",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Eat smaller, more frequent meals and avoid lying down for at least 2–3 hours after "
            "eating. Elevate the head of your bed by 15–20 cm to reduce nighttime reflux. Avoid "
            "common triggers such as spicy foods, fatty foods, coffee, chocolate, carbonated drinks, "
            "and alcohol. Over-the-counter antacids or H2 blockers can provide temporary relief, "
            "but see a doctor for persistent symptoms as ongoing GERD can damage the oesophagus."
        ),
        "care_tips_ur": (
            "کم مقدار میں زیادہ بار کھانا کھائیں اور کھانے کے 2-3 گھنٹے بعد تک نہ لیٹیں۔ "
            "رات کو سوتے وقت بستر کا سر 15-20 سینٹی میٹر اونچا رکھیں۔ "
            "مسالیدار، چکنا، کافی، سوڈا اور شراب سے پرہیز کریں۔ "
            "مستقل تکلیف ہو تو ڈاکٹر سے ملیں کیونکہ علاج نہ ہونے سے غذائی نالی کو نقصان ہو سکتا ہے۔"
        ),
        "red_flags": (
            "difficulty or pain when swallowing (dysphagia), "
            "vomiting blood or material that looks like coffee grounds, "
            "unexplained significant weight loss, "
            "black tarry stools (possible upper GI bleeding), "
            "chest pain that could be confused with heart attack, "
            "persistent vomiting, "
            "symptoms not improving after 2 weeks of antacid therapy"
        ),
    },
    {
        "name_en": "Drug Reaction",
        "name_ur": "دوا کا ردعمل / دوائی کی الرجی",
        "specialist_type": "Emergency Physician / Allergist / Dermatologist",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Stop taking the suspected drug immediately and seek medical advice — do not restart "
            "without a doctor's clearance. Document the name of the drug, dose, and time of onset "
            "of your reaction to share with your doctor or emergency team. Wear a medical alert "
            "bracelet noting your drug allergy. Mild reactions such as a minor rash may be managed "
            "with antihistamines under medical guidance, but any breathing difficulty or swelling "
            "of the face or throat requires emergency care immediately."
        ),
        "care_tips_ur": (
            "مشکوک دوا فوری بند کریں اور ڈاکٹر سے ملیں — ڈاکٹر کی اجازت کے بغیر دوبارہ نہ لیں۔ "
            "دوا کا نام، خوراک اور علامات کے شروع ہونے کا وقت نوٹ کریں۔ "
            "اپنی دوائی الرجی کا کارڈ یا بریسلٹ ساتھ رکھیں۔ "
            "منہ یا گلے کی سوجن یا سانس میں تکلیف ہو تو فوری ہنگامی طبی مدد لیں۔"
        ),
        "red_flags": (
            "difficulty breathing or throat swelling (anaphylaxis), "
            "widespread painful blistering rash with skin peeling (Stevens-Johnson Syndrome — emergency), "
            "high fever with widespread red rash and internal organ symptoms, "
            "severe facial swelling, "
            "rapid drop in blood pressure or fainting, "
            "blistering inside the mouth eyes or genitals, "
            "dark urine with yellowing of eyes (drug-induced liver injury)"
        ),
    },
    {
        "name_en": "Peptic Ulcer Disease",
        "name_ur": "معدے کا السر",
        "specialist_type": "Gastroenterologist",
        "triage_category": "see_gp",
        "care_tips_en": (
            "Avoid NSAIDs (ibuprofen, aspirin, naproxen) and take prescribed medications such as "
            "proton pump inhibitors regularly. Eat small regular meals to buffer stomach acid and "
            "avoid spicy foods, coffee, alcohol, and smoking, which all worsen ulcers. If H. pylori "
            "bacteria is confirmed, complete the full course of triple antibiotic therapy as "
            "prescribed. Do not take antacids as a substitute for seeing a doctor — proper diagnosis "
            "and treatment are essential."
        ),
        "care_tips_ur": (
            "آئبوپروفن، ایسپرین جیسی دوائیں بند کریں اور ڈاکٹر کی تجویز کردہ دوا باقاعدگی سے لیں۔ "
            "چھوٹے اور باقاعدہ کھانے کھائیں — مسالیدار، کافی اور شراب سے پرہیز کریں۔ "
            "H. pylori کی تصدیق ہو تو پورا اینٹی بایوٹک کورس مکمل کریں۔ "
            "اینٹاسڈ عارضی آرام دیتا ہے — مستقل علاج کے لیے ڈاکٹر سے ملیں۔"
        ),
        "red_flags": (
            "vomiting blood or dark material resembling coffee grounds, "
            "black tarry or maroon-coloured stools (upper GI bleeding), "
            "sudden severe sharp abdominal pain (possible ulcer perforation), "
            "abdomen becoming rigid and board-like, "
            "rapid heart rate and low blood pressure with paleness (signs of shock), "
            "unintentional weight loss"
        ),
    },
    {
        "name_en": "Diabetes",
        "name_ur": "ذیابیطس / شوگر",
        "specialist_type": "Endocrinologist / General Physician",
        "triage_category": "urgent_care",
        "care_tips_en": (
            "Monitor your blood sugar levels regularly at home and keep a log for your doctor. "
            "Follow a balanced diet low in refined sugars and carbohydrates, with plenty of "
            "vegetables, whole grains, and lean protein. Take all prescribed medications or insulin "
            "on time and never skip doses. Exercise at least 30 minutes most days to improve insulin "
            "sensitivity, and attend all scheduled check-ups to screen for eye, kidney, nerve, and "
            "heart complications."
        ),
        "care_tips_ur": (
            "گھر میں باقاعدگی سے بلڈ شوگر چیک کریں اور ریکارڈ رکھیں۔ "
            "شکر اور میدے سے پرہیز کریں — سبزیاں، اناج اور پروٹین کھائیں۔ "
            "انسولین یا دوا وقت پر لیں اور کبھی نہ بھولیں۔ "
            "روزانہ 30 منٹ ورزش کریں اور آنکھوں، گردوں اور اعصاب کا باقاعدہ معائنہ کروائیں۔"
        ),
        "red_flags": (
            "blood sugar above 300 mg/dL with nausea vomiting and fruity breath (diabetic ketoacidosis), "
            "blood sugar below 70 mg/dL with shaking sweating confusion or loss of consciousness (severe hypoglycaemia), "
            "sudden severe chest pain (diabetic patients have higher heart attack risk), "
            "sudden loss of vision or floaters in the eye (diabetic retinopathy complication), "
            "non-healing wound or ulcer especially on the foot, "
            "decreased urination with swelling (diabetic kidney failure)"
        ),
    },
]


# ──────────────────────────────────────────────────────────────
#  SEEDER
# ──────────────────────────────────────────────────────────────
async def seed(force_update: bool = False, dry_run: bool = False) -> tuple[int, int, int]:
    """
    Insert all 24 diseases into `disease_kb`.

    Returns ``(inserted, updated, skipped)`` so the caller can print a summary.

    - ``force_update`` — when True, existing rows are overwritten with the
      curated content (useful after the seed list itself has been edited).
    - ``dry_run``      — when True, no changes are committed; useful for CI.
    """
    inserted = updated = skipped = 0

    async with AsyncSessionLocal() as session:
        # Pre-fetch existing rows in one query, indexed by lowercase name_en.
        existing_rows = (await session.execute(select(DiseaseKB))).scalars().all()
        existing: dict[str, DiseaseKB] = {
            row.name_en.lower(): row for row in existing_rows
        }
        logger.info(
            "Pre-seed snapshot: %d disease_kb rows already in %s.",
            len(existing), settings.DATABASE_URL.split("@")[-1],
        )

        for disease in DISEASES:
            key = disease["name_en"].lower()
            row = existing.get(key)

            if row is None:
                logger.info("INSERT %s", disease["name_en"])
                if not dry_run:
                    session.add(DiseaseKB(
                        name_en=disease["name_en"],
                        name_ur=disease["name_ur"],
                        specialist_type=disease["specialist_type"],
                        triage_category=disease["triage_category"],
                        care_tips_en=disease["care_tips_en"],
                        care_tips_ur=disease["care_tips_ur"],
                        red_flags=disease["red_flags"],
                    ))
                inserted += 1
                continue

            if not force_update:
                logger.info("SKIP   %s (already present)", disease["name_en"])
                skipped += 1
                continue

            logger.info("UPDATE %s", disease["name_en"])
            if not dry_run:
                row.name_ur = disease["name_ur"]
                row.specialist_type = disease["specialist_type"]
                row.triage_category = disease["triage_category"]
                row.care_tips_en = disease["care_tips_en"]
                row.care_tips_ur = disease["care_tips_ur"]
                row.red_flags = disease["red_flags"]
                row.updated_at = datetime.now(timezone.utc)
            updated += 1

        if dry_run:
            logger.info("Dry run — rolling back without committing.")
            await session.rollback()
        else:
            await session.commit()

    return inserted, updated, skipped


# ──────────────────────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────────────────────
async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the disease_kb table.")
    parser.add_argument(
        "--force-update", action="store_true",
        help="Overwrite existing rows with the curated content.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing to the database.",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  DevloCare — Disease Knowledge Base Seeder")
    print(f"  DATABASE_URL: {settings.DATABASE_URL}")
    print(f"  Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode        : "
          f"{'DRY RUN' if args.dry_run else 'WRITE'}"
          f"{' + force-update' if args.force_update else ''}")
    print("=" * 70)

    try:
        # Idempotently make sure the table exists (works for SQLite + Postgres).
        await init_db()

        inserted, updated, skipped = await seed(
            force_update=args.force_update,
            dry_run=args.dry_run,
        )

        # Final integrity check — count what's actually in the DB now.
        async with AsyncSessionLocal() as session:
            total = (await session.execute(
                select(func.count(DiseaseKB.disease_id))
            )).scalar() or 0
    finally:
        await engine.dispose()

    print("\n" + "=" * 70)
    print(f"  Inserted: {inserted}   Updated: {updated}   Skipped: {skipped}")
    print(f"  Total rows in disease_kb after run: {total}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
