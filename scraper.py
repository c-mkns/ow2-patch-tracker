import sys
import os
import json
import requests

import google.generativeai as genai
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
        "parse_mode": "HTML" # Erlaubt uns, Text fett oder kursiv zu machen
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
    genai.configure(api_key=api_key)
    
    # Wir nutzen das Flash-Modell: Pfeilschnell und kostenlos
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Der Prompt zwingt die KI, NUR gültiges JSON auszugeben!
    prompt = f"""
    Du bist ein Experte für das Videospiel Overwatch 2 und analysierst Patch-Notes.
    Hier sind die Rohdaten eines neuen Patches im JSON-Format: 
    {json.dumps(raw_patch_data, ensure_ascii=False)}

    Deine Aufgabe:
    1. Bewerte JEDE Änderung in 'changes' und füge das Feld "type" hinzu. 
       Gültige Werte für "type" sind NUR: "buff", "nerf", "fix", "neutral".
    2. Analysiere das Gesamtbild jedes Helden und füge dem Helden-Objekt das Feld "trend" hinzu.
       Gültige Werte für "trend" sind NUR: "winner", "loser", "balanced", "rework", "bugfixes".
    3. Füge jedem Helden ein Feld "summary" hinzu: Ein kurzer, einzeiliger Satz (auf Deutsch), der zusammenfasst, was mit dem Helden passiert ist (z.B. "Massiver Nerf seiner Heilung, aber leichter Buff für die Mobilität.").

    ANTWORTE AUSSCHLIESSLICH MIT EINEM GÜLTIGEN JSON-OBJEKT, das exakt die gleiche Struktur wie die Rohdaten hat, nur ergänzt um die Felder 'type', 'trend' und 'summary'. Schreibe KEINEN Text vor oder nach dem JSON. Nutze keine Markdown-Blöcke wie ```json.
    """

    try:
        # response_mime_type zwingt die API, wirklich reines JSON zu generieren
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        
        enriched_data = json.loads(response.text)
        print("KI-Analyse erfolgreich abgeschlossen!")
        return enriched_data
    except Exception as e:
        print(f"Fehler bei der KI-Analyse: {e}")
        # Fallback: Falls die KI streikt, geben wir die Rohdaten zurück
        return raw_patch_data
    
def classify_change(note_text):
    text = note_text.lower()
    
    # 1. Bugfixes & System Status (Höchste Priorität)
    if any(kw in text for kw in ["fixed", "resolved", "bug", "issue", "re-enabled", "no longer prevent"]):
        return "fix"

    # --- 2. SONDERFÄLLE (Die Doppel-Verneinungs-Fallen) ---
    
    # Falle A: Wenn eine "Reduzierung" (Cooldown/Strafe) schlechter wird -> Nerf
    if "cooldown reduction" in text or "penalty reduction" in text:
        if any(w in text for w in ["decreased", "reduced", "lower"]):
            return "nerf"
        if any(w in text for w in ["increased", "higher", "greater"]):
            return "buff"
            
    # Falle B: Wenn die "Reduzierung" eines Bonus kleiner wird -> Buff (Sombra)
    if "bonus reduction" in text:
        if any(w in text for w in ["decreased", "reduced", "lower"]):
            return "buff"
        if any(w in text for w in ["increased", "higher", "greater"]):
            return "nerf"

    # --- 3. NORMALE ATTRIBUTE ---

    # Invertierte Attribute (Weniger ist ein Buff, Mehr ist ein Nerf)
    inverted_attributes = [
        "cooldown", "cost", "time", "delay", "spread", "recoil", 
        "penalty", "requirement", "falloff"
    ]
    
    # Standard Attribute (Mehr ist ein Buff, Weniger ist ein Nerf)
    # Neu hinzugefügt: energy, resource, currency, amplification
    standard_attributes = [
        "damage", "healing", "health", "armor", "shields", "overhealth", 
        "ammo", "range", "radius", "size", "speed", "duration", 
        "knockback", "rate", "distance", "energy", "resource", "currency", "amplification"
    ]
    
    words_down = ["reduced", "decreased", "shorter", "slower", "less", "lower"]
    words_up = ["increased", "longer", "faster", "more", "higher", "bonus"]

    # Prüfe zuerst auf invertierte Logik
    for attr in inverted_attributes:
        if attr in text:
            if any(w in text for w in words_down):
                return "buff"
            if any(w in text for w in words_up):
                return "nerf"

    # Prüfe danach auf Standard-Logik
    for attr in standard_attributes:
        if attr in text:
            if any(w in text for w in words_up):
                return "buff"
            if any(w in text for w in words_down):
                return "nerf"

    # 4. Fallback-Keywords für Reworks
    if any(kw in text for kw in ["granted", "now pierces", "cleanses", "added", "new functionality"]):
        return "buff"
    if any(kw in text for kw in ["removed", "no longer"]):
        return "nerf"

    return "neutral"

def parse_patch_notes(html_source):
    soup = BeautifulSoup(html_source, 'html.parser')
    all_patches = soup.find_all('div', class_='PatchNotes-patch')
    
    if not all_patches:
        return None

    for patch_container in all_patches:
        title_tag = patch_container.find('h3', class_='PatchNotes-patchTitle')
        patch_title = title_tag.text.strip() if title_tag else "Unbekannte Version"
        
        hero_sections = patch_container.find_all('div', class_='PatchNotesHeroUpdate')
        
        # Überspringe Patches ohne Helden-Änderungen (z.B. reine System-Hotfixes)
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

            # Allgemeine Updates
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
                                "note": change_text,
                                "type": classify_change(change_text)
                            })

            # Spezifische Fähigkeiten
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
                            "note": change_text,
                            "type": classify_change(change_text)
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
                        # NACHRICHT 1: Kein neuer Patch
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
                
            # NACHRICHT 2: Neuer Patch erfolgreich verarbeitet!
            msg = f"🚨 <b>NEUER OW2 PATCH ENTDECKT!</b> 🚨\nVersion: <i>{final_data['version']}</i>\nKI-Analyse wurde abgeschlossen und Daten auf GitHub aktualisiert! ✅"
            send_telegram_message(msg)
            
            print("Erfolg! Neue data.json wurde gespeichert.")