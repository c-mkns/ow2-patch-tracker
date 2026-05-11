import sys
import os
import json
import requests

from google import genai
from google.genai import types

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

URL = "https://overwatch.blizzard.com/en-us/news/patch-notes/"

def send_telegram_message(text):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        print("Telegram-Daten fehlen. Überspringe Benachrichtigung.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML" 
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

def fetch_html_with_playwright():
    print("Starte unsichtbaren Browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL)
        
        try:
            page.wait_for_selector('.PatchNotes-patch', timeout=15000)
            print("Seite erfolgreich geladen!")
        except Exception:
            print("Fehler: Timeout beim Warten auf die Patch-Notes.")
            browser.close()
            return None
            
        html_content = page.content()
        browser.close()
        return html_content

def enrich_data_with_ai(raw_patch_data):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Kein API-Key gefunden. KI-Analyse wird übersprungen.")
        return raw_patch_data

    print("Starte KI-Analyse mit Gemini...")
    client = genai.Client(api_key=api_key)
    
    # Dein optimierter, professioneller Prompt
    prompt = f"""### ROLE
You are a Senior Gameplay Balance Analyst for Overwatch 2 with deep knowledge of hero mechanics, meta-trends, and break-points.

### TASK
Analyze and enrich the provided Overwatch 2 patch notes JSON. Your goal is to evaluate the gameplay impact of each change and provide a professional assessment of the power shift for each hero.

### INPUT DATA
The following JSON contains the raw patch notes:
{json.dumps(raw_patch_data, ensure_ascii=False)}

### INSTRUCTIONS
1. **Verify/Enrich "type" (Changes Level):** Within each object in the "changes" array, ensure the "type" field is accurate. If it is missing or incorrect based on gameplay impact, set it to exactly one of: "buff", "nerf", "fix", "neutral".
   
2. **Determine "trend" (Hero Level):** Add a "trend" field to each hero object based on the net impact of all their changes:
   - "winner": Significant increase in power or viability.
   - "loser": Significant decrease in power or viability.
   - "balanced": Changes cancel each other out or are minor.
   - "rework": Fundamental mechanics changed, shifting how the hero is played.
   - "bugfixes": Only technical fixes with no direct balance intent.

3. **Write "summary" (Hero Level):** Add a "summary" field to each hero object. Provide a professional, one-sentence analysis in English explaining the gameplay consequences (e.g., "Trading sustain for higher burst potential makes them more lethal but punishable.").

### CONSTRAINTS
- Maintain the EXACT original JSON structure (root keys, hero names, etc.).
- Do not add any keys other than the ones requested.
- Ensure the "summary" is concise and technically accurate for an Overwatch player.
- Ensure the output is valid, minified or pretty-printed JSON.

### OUTPUT FORMAT
Respond ONLY with the updated JSON object. 
NO markdown code blocks (no ```json). 
NO conversational filler. 
NO explanations."""

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        enriched_data = json.loads(response.text)
        print("KI-Analyse erfolgreich abgeschlossen!")
        return enriched_data
    except Exception as e:
        print(f"CRITICAL ERROR bei der KI-Analyse: {e}")
        return raw_patch_data

def parse_patch_notes(html_source):
    soup = BeautifulSoup(html_source, 'html.parser')
    all_patches = soup.find_all('div', class_='PatchNotes-patch')
    
    if not all_patches:
        return None

    for patch_container in all_patches:
        title_tag = patch_container.find('h3', class_='PatchNotes-patchTitle')
        patch_title = title_tag.text.strip() if title_tag else "Unbekannte Version"
        
        hero_sections = patch_container.find_all('div', class_='PatchNotesHeroUpdate')
        
        if len(hero_sections) == 0:
            continue
            
        date_tag = patch_container.find('div', class_='PatchNotes-date')
        
        patch_data = {
            "version": patch_title,
            "date": date_tag.text.strip() if date_tag else "Unbekanntes Datum",
            "heroes": {}
        }

        for hero in hero_sections:
            name_tag = hero.find('h5', class_='PatchNotesHeroUpdate-name')
            if not name_tag:
                continue
                
            hero_name = name_tag.text.strip()
            
            if hero_name not in patch_data["heroes"]:
                patch_data["heroes"][hero_name] = []

            general_updates = hero.find('div', class_='PatchNotesHeroUpdate-generalUpdates')
            if general_updates:
                current_context = "General / Base Stats"
                for element in general_updates.children:
                    if element.name == 'h2':
                        current_context = element.text.strip()
                    elif element.name == 'ul':
                        for li in element.find_all('li'):
                            change_text = " ".join(li.text.split())
                            patch_data["heroes"][hero_name].append({
                                "ability": current_context,
                                "note": change_text
                            })

            ability_updates = hero.find_all('div', class_='PatchNotesAbilityUpdate')
            for ability in ability_updates:
                ability_name_tag = ability.find('div', class_='PatchNotesAbilityUpdate-name')
                ability_name = ability_name_tag.text.strip() if ability_name_tag else "Unknown Ability"
                
                ul_tag = ability.find('ul')
                if ul_tag:
                    for li in ul_tag.find_all('li'):
                        change_text = " ".join(li.text.split())
                        patch_data["heroes"][hero_name].append({
                            "ability": ability_name,
                            "note": change_text
                        })

        final_output = {
            "version": patch_data["version"],
            "date": patch_data["date"],
            "heroes": [{"name": k, "changes": v} for k, v in patch_data["heroes"].items() if v]
        }
        
        return final_output
    return None

if __name__ == "__main__":
    html = fetch_html_with_playwright()
    if html:
        raw_data = parse_patch_notes(html) 
        
        if raw_data:
            try:
                with open("data.json", "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    
                    if old_data.get("version") == raw_data["version"]:
                        msg = f"ℹ️ <b>OW2 Patch Tracker</b>\nCheck durchgeführt. Kein neuer Patch gefunden. Aktuellste Version bleibt: <i>{raw_data['version']}</i>"
                        send_telegram_message(msg)
                        
                        print(f"Kein neuer Patch. Beende Skript.")
                        sys.exit(0)
            except FileNotFoundError:
                print("Keine alte data.json gefunden.")

            print("🚀 Neuer Patch entdeckt! Wecke die KI für die Analyse auf...")
            final_data = enrich_data_with_ai(raw_data)
            
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(final_data, f, ensure_ascii=False, indent=4)
                
            msg = f"🚨 <b>NEUER OW2 PATCH ENTDECKT!</b> 🚨\nVersion: <i>{final_data['version']}</i>\nKI-Analyse wurde abgeschlossen und Daten auf GitHub aktualisiert! ✅"
            send_telegram_message(msg)
            
            print("Erfolg! Neue data.json wurde gespeichert.")