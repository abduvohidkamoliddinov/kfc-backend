import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

MENU_FILE = DATA_DIR / "menu.json"
_lock = threading.Lock()

def _default_menu():
    return {
        "meta": {"next_category_id": 1, "next_item_id": 1},
        "categories": [],  # {id:int, slug:str, name_uz:str, name_ru:str}
        "items": []        # {id:int, category_slug:str, name_uz:str, name_ru:str, price:int, image_url:str|None, desc_uz:str, desc_ru:str}
    }

def load_menu():
    with _lock:
        if not MENU_FILE.exists():
            save_menu(_default_menu())
        try:
            return json.loads(MENU_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = _default_menu()
            save_menu(data)
            return data

def save_menu(data):
    with _lock:
        MENU_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _slugify(s: str) -> str:
    s = s.strip().lower()
    keep = []
    for ch in s:
        if ch.isalnum():
            keep.append(ch)
        elif ch in [" ", "-", "_"]:
            keep.append("-")
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "category"

def add_category(name_uz: str, name_ru: str | None = None, slug: str | None = None):
    data = load_menu()
    cid = data["meta"]["next_category_id"]
    data["meta"]["next_category_id"] += 1

    slug_final = _slugify(slug or name_uz)
    # slug unique bo‘lsin
    existing = {c["slug"] for c in data["categories"]}
    base = slug_final
    i = 2
    while slug_final in existing:
        slug_final = f"{base}-{i}"
        i += 1

    cat = {
        "id": cid,
        "slug": slug_final,
        "name_uz": name_uz,
        "name_ru": (name_ru or name_uz),
    }
    data["categories"].append(cat)
    save_menu(data)
    return cat

def update_category(slug: str, name_uz: str, name_ru: str | None = None):
    data = load_menu()
    for c in data["categories"]:
        if c["slug"] == slug:
            c["name_uz"] = name_uz
            c["name_ru"] = (name_ru or name_uz)
            save_menu(data)
            return c
    return None

def delete_category(slug: str):
    data = load_menu()
    data["categories"] = [c for c in data["categories"] if c["slug"] != slug]
    data["items"] = [i for i in data["items"] if i.get("category_slug") != slug]
    save_menu(data)

def add_item(category_slug: str, name_uz: str, name_ru: str, price: int,
             image_url: str | None = None, desc_uz: str = "", desc_ru: str = ""):
    data = load_menu()
    iid = data["meta"]["next_item_id"]
    data["meta"]["next_item_id"] += 1

    item = {
        "id": iid,
        "category_slug": category_slug,
        "name_uz": name_uz,
        "name_ru": name_ru,
        "price": int(price),
        "image_url": image_url,
        "desc_uz": desc_uz,
        "desc_ru": desc_ru,
    }
    data["items"].append(item)
    save_menu(data)
    return item

def update_item(item_id: int, patch: dict):
    data = load_menu()
    for it in data["items"]:
        if it["id"] == int(item_id):
            for k, v in patch.items():
                if v is None:
                    continue
                if k == "price":
                    it[k] = int(v)
                else:
                    it[k] = v
            save_menu(data)
            return it
    return None

def delete_item(item_id: int):
    data = load_menu()
    data["items"] = [i for i in data["items"] if i["id"] != int(item_id)]
    save_menu(data)
