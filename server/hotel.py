"""The Lumen — hotel knowledge used to ground qwen3 + the media catalog hint.

Keeps Lumi's facts consistent (menu, venues, policies) instead of improvising, and
tells the model which images it can show (by catalog id) or request fresh.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

HOTEL_PROFILE = """FACTS ABOUT THE LUMEN HOTEL (rely on these; do not invent others):
DINING (room service is 24/7; Aria Restaurant, level 1, dinner 6–11pm):
- Breakfast: Eggs Benedict $24, Buttermilk Pancakes $18, Avocado Toast $16, Lumen Roast coffee $6.
- All day: The Lumen Burger $26, Caesar Salad $17, Club Sandwich $19, Vanilla Cheesecake $12.
VENUES: The Pool (level 3, 6am–10pm); The Spa (level 2, by appointment); The Rooftop Bar
(level 20, 5pm–1am); the Conservatory Lounge (lobby level); Aria Restaurant (level 1).
ROOMS: suites with king beds and city views; checkout 11am.
POLICIES:
- Security camera footage is released ONLY via a police report/court order — never directly to a guest.
- Medical/dietary questions (e.g. sleep aids): defer to on-call medical staff to confirm what's safe.
- Lost items: dispatch staff to search + check lost & found + file a report, and give updates."""


def catalog_items():
    try:
        return json.load(open(os.path.join(HERE, "catalog.json")))["items"]
    except Exception:
        return []


def catalog_hint():
    items = catalog_items()
    if not items:
        return ""
    lines = [f'  {it["id"]} — {it["title"]}: {it["desc"]}' for it in items]
    return "IMAGES you can show on the guest's screen (use these ids):\n" + "\n".join(lines)
