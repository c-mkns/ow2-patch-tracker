from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json

URL = "https://overwatch.blizzard.com/en-us/news/patch-notes/"

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
        data = parse_patch_notes(html)
        if data:
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print("Erfolg! data.json wurde erstellt.")