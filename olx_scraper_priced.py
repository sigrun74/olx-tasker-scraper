"""
olx_scraper.py — скрейпер olx.kz для датасета Task-er
Берёт данные прямо с карточек листинга (быстро, без захода в каждое объявление)

Установка:
    pip install selenium webdriver-manager beautifulsoup4

Запуск:
    python olx_scraper.py --category santehnika --pages 3
    python olx_scraper.py --pages 10
    python olx_scraper.py --output data.csv
"""

import argparse
import csv
import logging
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

BASE_URL = "https://www.olx.kz"

CATEGORIES = {
    "santehnika": {"slug": "/uslugi/remont-i-stroitelstvo/santehnika-kommunikatsii/", "direction_mapped": "Сантехника"},
    "elektrika": {"slug": "/uslugi/remont-i-stroitelstvo/elektrika/", "direction_mapped": "Электрика"},
    "otdelka": {"slug": "/uslugi/remont-i-stroitelstvo/otdelka-remont/", "direction_mapped": "Отделка и ремонт"},
    "klimat": {"slug": "/uslugi/remont-i-stroitelstvo/ventilyatsiya-konditsionirovanie/", "direction_mapped": "Климатическое оборудование"},
    "stroitelstvo": {"slug": "/uslugi/remont-i-stroitelstvo/stroitelnye-uslugi/", "direction_mapped": "Строительство"},
    "malyarnyye": {"slug": "/uslugi/remont-i-stroitelstvo/malyarnye-raboty/", "direction_mapped": "Малярные работы"},
    "plitka": {"slug": "/uslugi/remont-i-stroitelstvo/ukladka-plitki/", "direction_mapped": "Укладка плитки"},
    "napolnyye": {"slug": "/uslugi/remont-i-stroitelstvo/napolnye-raboty/", "direction_mapped": "Напольные работы"},
    "gipsokarton": {"slug": "/uslugi/remont-i-stroitelstvo/gipsokartonnye-raboty/", "direction_mapped": "Гипсокартон"},
    "okna": {"slug": "/uslugi/remont-i-stroitelstvo/okna-dveri-balkony/", "direction_mapped": "Окна и двери"},
    "svarochnye": {"slug": "/uslugi/remont-i-stroitelstvo/svarochnye-raboty/", "direction_mapped": "Сварочные работы"},
    "krovlya": {"slug": "/uslugi/remont-i-stroitelstvo/krovelnye-raboty/", "direction_mapped": "Кровельные работы"},
    "mebel": {"slug": "/uslugi/remont-i-stroitelstvo/izgotovleniye-mebeli-na-zakaz/", "direction_mapped": "Мебель на заказ"},
    "montazh": {"slug": "/uslugi/remont-i-stroitelstvo/montazhnye-raboty/", "direction_mapped": "Монтажные работы"},
    "dizayn": {"slug": "/uslugi/remont-i-stroitelstvo/dizayn-arhitektura/", "direction_mapped": "Дизайн и архитектура"},
    "stolyarnyye": {"slug": "/uslugi/remont-i-stroitelstvo/stolyarnye-raboty/", "direction_mapped": "Столярные работы"},
    "oboi": {"slug": "/uslugi/remont-i-stroitelstvo/pokleyka-oboev/", "direction_mapped": "Поклейка обоев"},
    "doma": {"slug": "/uslugi/remont-i-stroitelstvo/stroitelstvo-domov-kottedzhey/", "direction_mapped": "Строительство домов и коттеджей"},
}

ZONE_MAP: dict = {}

OUTPUT_FIELDS = [
    "listing_id", "url", "scraped_at", "category_l2", "direction_mapped",
    "title", "price_raw", "price_value", "price_currency", "price_type",
    "price_from_desc", "description", "city", "district", "zone_mapped", "seller_type",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("olx_scraper")


def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=ru-RU")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
    return driver


def parse_price(raw):
    if not raw:
        return {"price_value": None, "price_currency": None, "price_type": None}
    low = raw.lower().strip()
    price_type = "fixed"
    if any(w in low for w in ["договор"]):
        price_type = "negotiable"
    elif "/час" in low or "в час" in low:
        price_type = "per_hour"
    elif "м²" in low or "кв.м" in low:
        price_type = "per_m2"
    if price_type == "negotiable":
        return {"price_value": None, "price_currency": "KZT", "price_type": "negotiable"}
    currency = "USD" if ("$" in raw or "usd" in low) else "KZT"
    clean = re.sub(r"[^\d]", "", raw)
    value = float(clean) if clean else None
    return {"price_value": value, "price_currency": currency, "price_type": price_type}


def parse_cards(html, category_key):
    soup = BeautifulSoup(html, "html.parser")
    cat_info = CATEGORIES[category_key]
    records = []
    now = datetime.now(timezone.utc).isoformat()

    cards = soup.select("div[data-cy='l-card']")
    log.info("  карточек найдено: %d", len(cards))

    for card in cards:
        try:
            a = card.select_one("a[href]")
            if not a:
                continue
            url = a["href"]
            if not url.startswith("http"):
                url = BASE_URL + url

            m = re.search(r"-ID(\w+)\.html", url)
            listing_id = m.group(1) if m else url

            # Заголовок — пробуем все варианты
            title = ""
            for sel in ["h4", "h3", "h6", "[data-cy='ad-card-title']", "a > div"]:
                el = card.select_one(sel)
                if el:
                    t = el.get_text(strip=True)
                    if len(t) > 3:
                        title = t
                        break
            # Fallback — alt у картинки
            if not title:
                img = card.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip()
            if not title:
                continue

            # Цена — olx использует разные атрибуты
            price_el = (
                card.select_one("[data-testid='ad-price']") or
                card.select_one("[class*='Price']") or
                card.select_one("[class*='price']") or
                card.select_one("strong") or
                card.select_one("b")
            )
            price_raw = ""
            if price_el:
                t = price_el.get_text(strip=True)
                # Берём только если содержит цифры или слово "договор"
                if re.search(r"\d|договор", t, re.IGNORECASE):
                    price_raw = t
            price_parsed = parse_price(price_raw)

            # Локация
            loc_el = card.select_one("p[data-testid='location-date']") or card.select_one("[data-testid='location-date']")
            city, district = "", ""
            if loc_el:
                loc_text = loc_el.get_text(strip=True)
                # Убираем дату: "Алматы, Бостандыкский район - Сегодня в 10:12"
                loc_clean = re.split(r"\s*[-–]\s*(Сегодня|Вчера|\d)", loc_text)[0].strip()
                parts = [p.strip() for p in loc_clean.split(",")]
                city = parts[0] if parts else ""
                district = parts[1] if len(parts) > 1 else ""

            records.append({
                "listing_id": listing_id,
                "url": url,
                "scraped_at": now,
                "category_l2": cat_info["direction_mapped"],
                "direction_mapped": cat_info["direction_mapped"],
                "title": title,
                "price_raw": price_raw,
                **price_parsed,
                "city": city,
                "district": district,
                "zone_mapped": ZONE_MAP.get(district, ""),
                "seller_type": "company" if card.select_one("[data-testid='adCard-featured']") else "individual",
            })
        except Exception as e:
            log.warning("  ошибка карточки: %s", e)

    return records


def is_driver_alive(driver) -> bool:
    try:
        _ = driver.current_url
        return True
    except Exception:
        return False


def load_page(driver, url, retries=2):
    for attempt in range(retries):
        try:
            if not is_driver_alive(driver):
                log.warning("  браузер упал, перезапускаем...")
                try:
                    driver.quit()
                except Exception:
                    pass
                new_driver = make_driver()
                # Меняем объект через __class__ trick не работает — возвращаем None со спец флагом
                return None, new_driver
            driver.get(url)
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            try:
                WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-cy='l-card']"))
                )
            except Exception:
                pass
            time.sleep(random.uniform(3.5, 5.0))
            return driver.page_source, driver
        except Exception as e:
            err = str(e)
            if "invalid session id" in err or "no such window" in err:
                log.warning("  сессия упала, перезапускаем Chrome...")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = make_driver()
                time.sleep(3)
            else:
                log.warning("  ошибка загрузки: %s", err[:120])
                return None, driver
    return None, driver


def has_next_page(html):
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.select_one("[data-cy='pagination-forward']") or soup.select_one("a[data-cy='page-link-next']"))



# Категории где мало объявлений — заходим за описанием и ценой
DEEP_CATEGORIES = {"gipsokarton", "landshaft", "stolyarnyye", "montazh", "dizayn", "oboi", "doma", "elektrika", "plitka", "santehnika", "otdelka", "malyarnyye", "napolnyye", "okna"}

PRICE_PATTERNS = [
    r'(\d[\d\s]{1,8})\s*(тг|тнг|тенге|₸)',
    r'от\s*(\d[\d\s]{1,6})\s*(тг|тнг|тенге|₸)',
    r'(\d[\d\s]{1,8})\s*(?:тг|тнг|тенге|₸)?\s*/\s*(?:м²|кв|час|м2)',
]

def extract_price_from_text(text: str) -> dict:
    """Ищет цену в тексте описания."""
    low = text.lower()
    if any(w in low for w in ["договор", "по договорен"]):
        return {"price_from_desc": "договорная", "price_value": None, "price_currency": "KZT", "price_type": "negotiable"}
    for pat in PRICE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = re.sub(r"\s", "", m.group(1))
            try:
                value = float(raw)
                price_type = "per_m2" if "м²" in text[m.start():m.end()+5] or "м2" in text[m.start():m.end()+5] else (
                    "per_hour" if "час" in text[m.start():m.end()+5] else "fixed"
                )
                return {"price_from_desc": m.group(0).strip(), "price_value": value, "price_currency": "KZT", "price_type": price_type}
            except Exception:
                continue
    return {"price_from_desc": "", "price_value": None, "price_currency": None, "price_type": None}


def parse_ad_page(driver, url) -> dict:
    """Заходит на страницу объявления и берёт описание + цену."""
    try:
        html, driver = load_page(driver, url)
        if not html:
            return {"description": "", "price_from_desc": "", "price_value": None, "price_currency": None, "price_type": None}
        soup = BeautifulSoup(html, "html.parser")

        # Описание
        desc_el = soup.select_one("div[data-cy='ad_description']") or soup.select_one("[data-testid='ad_description']")
        description = desc_el.get_text(separator=" ", strip=True)[:1500] if desc_el else ""

        # Цена со страницы объявления
        price_el = soup.select_one("[data-testid='ad-price-container']") or soup.select_one("[data-cy='ad-price']")
        price_raw = price_el.get_text(strip=True) if price_el else ""

        if price_raw and re.search(r"\d", price_raw):
            parsed = {"price_from_desc": price_raw, **{k: v for k, v in __import__("builtins").__dict__.items() if False}}
            # parse price_raw manually
            low = price_raw.lower()
            price_type = "negotiable" if "договор" in low else ("per_hour" if "час" in low else ("per_m2" if "м²" in low else "fixed"))
            clean = re.sub(r"[^\d]", "", price_raw)
            value = float(clean) if clean else None
            return {"description": description, "price_from_desc": price_raw, "price_value": value, "price_currency": "KZT", "price_type": price_type}

        # Если цены нет на странице — ищем в тексте описания
        price_data = extract_price_from_text(description)
        return {"description": description, **price_data}

    except Exception as e:
        log.warning("  ошибка парсинга объявления: %s", e)
        return {"description": "", "price_from_desc": "", "price_value": None, "price_currency": None, "price_type": None}

def scrape_category(driver, category_key, max_pages, writer, seen_ids) -> tuple:
    cat = CATEGORIES[category_key]
    collected = 0

    for page_num in range(1, max_pages + 1):
        url = BASE_URL + cat["slug"]
        if page_num > 1:
            url += f"?page={page_num}"

        log.info("[%s] страница %d/%d → %s", category_key, page_num, max_pages, url)
        html, driver = load_page(driver, url)
        if not html:
            break

        records = parse_cards(html, category_key)
        if not records:
            log.info("  пусто, конец категории")
            break

        new = 0
        do_deep = category_key in DEEP_CATEGORIES
        for rec in records:
            if rec["listing_id"] not in seen_ids:
                seen_ids.add(rec["listing_id"])
                if do_deep:
                    ad_data = parse_ad_page(driver, rec["url"])
                    # Обновляем только если нашли данные
                    if ad_data.get("description"):
                        rec["description"] = ad_data["description"]
                    if ad_data.get("price_from_desc"):
                        rec["price_from_desc"] = ad_data["price_from_desc"]
                        rec["price_value"] = ad_data.get("price_value")
                        rec["price_currency"] = ad_data.get("price_currency")
                        rec["price_type"] = ad_data.get("price_type")
                    time.sleep(random.uniform(1.5, 2.5))
                writer.writerow(rec)
                new += 1

        collected += new
        log.info("  новых: %d | итого в категории: %d", new, collected)

        if not has_next_page(html) or page_num >= max_pages:
            break
        time.sleep(random.uniform(1.5, 3.0))

    return collected, driver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="all", help="one category, all, or comma-separated list")
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--output", default="olx_listings.csv")
    args = parser.parse_args()

    if args.category == "all":
        categories = list(CATEGORIES.keys())
    else:
        categories = [c.strip() for c in args.category.split(",") if c.strip()]

    for key in categories:
        if key not in CATEGORIES:
            parser.error(f"Неизвестная категория: {key}")

    output_path = Path(args.output)
    is_new = not output_path.exists()

    log.info("Запускаем Chrome...")
    driver = make_driver()

    try:
        log.info("Прогрев...")
        driver.get("https://www.olx.kz/")
        time.sleep(random.uniform(3.0, 5.0))

        seen_ids: set = set()
        if output_path.exists():
            try:
                with output_path.open("r", newline="", encoding="utf-8-sig") as existing_f:
                    reader = csv.DictReader(existing_f)
                    for row in reader:
                        listing_id = row.get("listing_id")
                        if listing_id:
                            seen_ids.add(listing_id)
                log.info("Уже есть %d listing_id в %s — будем пропускать дубли", len(seen_ids), output_path)
            except Exception as e:
                log.warning("Не удалось прочитать существующий CSV для дедупликации: %s", e)

        with output_path.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
            if is_new:
                writer.writeheader()

            total = 0
            for cat_key in categories:
                log.info("=== Категория: %s ===", cat_key)
                n, driver = scrape_category(driver, cat_key, args.pages, writer, seen_ids)
                total += n
                log.info("  %s готово: %d записей", cat_key, n)
                time.sleep(random.uniform(2.0, 4.0))

        log.info("Готово. Всего записей: %d → %s", total, output_path)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
