import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

print("🔍 Testing Gemini API Key...")

# 1. Load environment variables
load_dotenv(".env")
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ ERROR: GEMINI_API_KEY is not set in your .env file.")
    exit(1)

if not api_key.startswith("AI"):
    print("⚠️ WARNING: Your API key doesn't look like a standard Google AI Studio key (should start with 'AI').")

# 2. Configure Gemini
try:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
except Exception as e:
    print(f"❌ ERROR configuring Gemini: {e}")
    exit(1)

# 3. Test Standard Text Generation
print("\n📝 Test 1: Standard Text Generation")
try:
    resp = model.generate_content("Reply with exactly the word 'SUCCESS'.")
    if "SUCCESS" in resp.text:
        print("✅ Passed!")
    else:
        print(f"⚠️ Passed with unexpected output: {resp.text}")
except Exception as e:
    print(f"❌ Failed: {e}")
    print("\nIf you see a '400 Bad Request', check if your region is supported or if the model name is valid.")
    exit(1)

# 4. Test Strict JSON Schema Generation
print("\n⚙️ Test 2: Strict JSON Schema Enforcement")
schema = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "message": {"type": "string"}
    },
    "required": ["status", "message"]
}

try:
    resp = model.generate_content(
        "Reply with status 'ok' and message 'all systems go'.",
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": schema
        }
    )
    # Validate it's actually parsable JSON
    data = json.loads(resp.text)
    if data.get("status") == "ok":
        print("✅ Passed!")
    else:
        print(f"⚠️ Passed with unexpected JSON: {data}")
except Exception as e:
    print(f"❌ Failed: {e}")
    print("\nIf this fails but Test 1 succeeded, your Google Cloud/AI Studio tier might not support strict JSON schema enforcement.")
    exit(1)

print("\n🚀 All API tests passed! Your key is fully compatible with wa-slash-commands.")
