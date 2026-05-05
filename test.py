import requests
import json
import time

# ================================
# CONFIG
# ================================
API_URL = "http://192.168.100.29:8000/api/v1/predict/text"

TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI1IiwiaWF0IjoxNzc3OTI4NTk5LCJleHAiOjE3Nzc5NTczOTksInR5cGUiOiJhY2Nlc3MiLCJ1c2VybmFtZSI6InN1cGVyYWRtaW4iLCJpc19hZG1pbiI6dHJ1ZSwiaXNfc3VwZXJfYWRtaW4iOnRydWV9.Le6SWhBOOJX1N9dHLmTlfbVt5NkL2PuW2EVBUppc6uQ"

HEADERS = {
    "Authorization": TOKEN,
    "Content-Type": "application/json"
}
print("SCRIPT STARTED")
# ================================
# TEST DATASET
# ================================
test_cases = [
    {"expected":"Acne","text":"I have many pimples on my face with oily skin","age":22,"sex":"Male","duration":"3 months","severity":"mild"},
    {"expected":"Allergy","text":"I keep sneezing with itchy eyes and skin rash","age":25,"sex":"Female","duration":"4 days","severity":"mild"},
    {"expected":"Arthritis","text":"My knee joints hurt and feel stiff especially in the morning","age":56,"sex":"Female","duration":"6 months","severity":"moderate"},
    {"expected":"Bronchial Asthma","text":"I am having wheezing and shortness of breath especially at night","age":19,"sex":"Male","duration":"2 days","severity":"severe"},
    {"expected":"Cervical Spondylosis","text":"I have neck pain that goes to my shoulders with numbness","age":48,"sex":"Male","duration":"1 month","severity":"moderate"},
    {"expected":"Chicken Pox","text":"I have fever with itchy red blisters all over my body","age":12,"sex":"Male","duration":"3 days","severity":"moderate"},
    {"expected":"Common Cold","text":"I have sneezing runny nose and mild sore throat","age":30,"sex":"Female","duration":"2 days","severity":"mild"},
    {"expected":"Dengue","text":"High fever with severe body pain and pain behind eyes","age":27,"sex":"Male","duration":"4 days","severity":"severe"},
    {"expected":"Diabetes","text":"I feel very thirsty and urinate again and again with weakness","age":44,"sex":"Female","duration":"1 month","severity":"moderate"},
    {"expected":"Dimorphic Hemorrhoids (Piles)","text":"I have pain while passing stool with bleeding","age":38,"sex":"Male","duration":"1 week","severity":"moderate"},
    {"expected":"Drug Reaction","text":"After taking medicine I got rash and itching all over body","age":34,"sex":"Female","duration":"1 day","severity":"moderate"},
    {"expected":"Fungal Infection","text":"I have circular itchy rash in groin area","age":29,"sex":"Male","duration":"10 days","severity":"mild"},
    {"expected":"Gastroesophageal Reflux Disease","text":"I feel burning in chest after meals with sour taste in mouth","age":35,"sex":"Male","duration":"2 weeks","severity":"moderate"},
    {"expected":"Hypertension","text":"I have headache dizziness and very high blood pressure reading","age":52,"sex":"Female","duration":"2 days","severity":"severe"},
    {"expected":"Impetigo","text":"My child has honey colored crusted sores around mouth","age":8,"sex":"Male","duration":"3 days","severity":"moderate"},
    {"expected":"Jaundice","text":"My eyes and skin are turning yellow with dark urine","age":40,"sex":"Male","duration":"1 week","severity":"moderate"},
    {"expected":"Malaria","text":"I get fever with chills and sweating every few hours","age":23,"sex":"Female","duration":"5 days","severity":"severe"},
    {"expected":"Migraine","text":"Severe one sided headache with nausea and light sensitivity","age":28,"sex":"Female","duration":"1 day","severity":"severe"},
    {"expected":"Peptic Ulcer Disease","text":"Burning stomach pain that gets worse when hungry","age":41,"sex":"Male","duration":"3 weeks","severity":"moderate"},
    {"expected":"Pneumonia","text":"High fever with chest cough and difficulty breathing","age":60,"sex":"Male","duration":"4 days","severity":"severe"},
    {"expected":"Psoriasis","text":"Thick silvery skin patches on elbows with itching","age":37,"sex":"Female","duration":"6 months","severity":"mild"},
    {"expected":"Typhoid","text":"I have continuous fever with stomach pain and weakness","age":31,"sex":"Male","duration":"6 days","severity":"severe"},
    {"expected":"Urinary Tract Infection","text":"Burning urine with lower belly pain and frequent urination","age":26,"sex":"Female","duration":"3 days","severity":"moderate"},
    {"expected":"Varicose Veins","text":"I have swollen twisted blue veins in my legs with aching","age":46,"sex":"Female","duration":"8 months","severity":"moderate"}
]

print("SCRIPT STARTED")

print("TEST CASES LOADED")




# ================================
# RUN AUDIT
# ================================
results = []

for i, case in enumerate(test_cases, 1):
    payload = {
        "text": case["text"],
        "age": case["age"],
        "sex": case["sex"],
        "duration": case["duration"],
        "severity": case["severity"],
        "pregnancy": False,
        "chronic_disease": ""
    }

    print(f"\n[{i}/24] Testing: {case['expected']}")
    print("Query:", case["text"])

    try:
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=30)
        data = response.json()

        result = {
            "expected": case["expected"],
            "query": case["text"],
            "response": data
        }

        results.append(result)

        # quick console summary
        top = data.get("top_predictions", [])
        if top:
            print("Top Returned:", top[0])
        else:
            print("No top_predictions found")

    except Exception as e:
        print("ERROR:", str(e))
        results.append({
            "expected": case["expected"],
            "query": case["text"],
            "error": str(e)
        })

    time.sleep(1)

# ================================
# SAVE FILE
# ================================
with open("devlocare_audit_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=4, ensure_ascii=False)

print("\nDONE. Saved to devlocare_audit_results.json")