def call_gemini(prompt):
    """Calls Gemini. Ensure GEMINI_API_KEY is in your GitHub Secrets."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: 
        return '{"error": "No GEMINI_API_KEY found"}'
    
    # Updated to the latest available model and endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        print(f"Gemini API Error Response: {r.text}")
        return '{"error": "Gemini API failed"}'
