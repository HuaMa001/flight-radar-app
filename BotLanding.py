from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import os
import random
import re
import time
import requests
from FlightRadarAPI import FlightRadar24API

# ============================================================
# 1. 環境變數與 targets.txt 讀取邏輯
# ============================================================
DISCORD_WEBHOOK_URL_TPE = os.getenv("DISCORD_WEBHOOK_URL_TPE", "")
DISCORD_WEBHOOK_URL_TPE_FOREIGN = os.getenv("DISCORD_WEBHOOK_URL_TPE_FOREIGN", "")
DISCORD_WEBHOOK_URL_TSA = os.getenv("DISCORD_WEBHOOK_URL_TSA", "")
DISCORD_WEBHOOK_URL_KHH = os.getenv("DISCORD_WEBHOOK_URL_KHH", "")
DISCORD_WEBHOOK_URL_ELSE = os.getenv("DISCORD_WEBHOOK_URL_ELSE", "")

# 兜底 fallback
DEFAULT_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", os.getenv("DISCORD", ""))


def load_targets(filepath: str = "targets.txt") -> list[str]:
    targets = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                targets = [
                    line.strip().upper()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            print(f"📁 成功從 `{filepath}` 載入 {len(targets)} 架目標飛機！")
        except Exception as e:
            print(f"⚠️ 讀取 `{filepath}` 失敗: {e}")

    if not targets:
        raw_targets = os.getenv("TARGET_PLANES", "")
        if raw_targets and raw_targets.strip():
            cleaned_raw = (
                raw_targets.replace("\r", "")
                .replace("\n", ",")
                .replace("，", ",")
                .replace('"', "")
                .replace("'", "")
            )
            targets = [t.strip().upper() for t in cleaned_raw.split(",") if t.strip()]
            print(f"📋 成功從環境變數載入 {len(targets)} 架目標飛機！")

    return list(dict.fromkeys(targets))


TARGETS = load_targets("targets.txt")


# ============================================================
# 2. HTTP Session (FR24 專用)
# ============================================================
http_session = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=30, pool_maxsize=30, max_retries=1
)
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

# ============================================================
# 2.5 圖片快取
# ============================================================
IMAGE_CACHE_DIR = "image_cache"
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)


def get_image_cache_path(registration: str) -> str | None:
    if not registration or registration == "未知":
        return None
    safe_reg = normalize_target(registration)
    if not safe_reg:
        return None
    return os.path.join(IMAGE_CACHE_DIR, f"{safe_reg}.txt")


def load_cached_image(registration: str) -> str | None:
    path = get_image_cache_path(registration)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            url = f.read().strip()
        if url:
            print(f"     [圖片] 使用快取圖片：{registration}")
            return url
    except Exception as e:
        print(f"     [圖片] 讀取快取失敗：{e}")
    return None


def save_cached_image(registration: str, image_url: str):
    path = get_image_cache_path(registration)
    if not path or not image_url:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(image_url)
        print(f"     [圖片] 已建立圖片快取：{registration}")
    except Exception as e:
        print(f"     [圖片] 儲存快取失敗：{e}")


# ============================================================
# 3. Header
# ============================================================
USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
]


def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


# ============================================================
# 4. 基本工具與分流邏輯
# ============================================================
def normalize_target(value: str) -> str:
    if not value:
        return ""
    return str(value).upper().strip().replace("-", "").replace(" ", "")


def format_full_datetime(ts: int | None) -> str:
    if not ts:
        return "未知"
    try:
        tz_tw = timezone(timedelta(hours=8))
        dt = datetime.fromtimestamp(int(ts), tz=tz_tw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"


def check_is_taiwan(text_or_code: str) -> bool:
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper().strip()
    tw_airport_codes = {
        "TPE",
        "TSA",
        "KHH",
        "RMQ",
        "TNN",
        "HUN",
        "TTT",
        "MZG",
        "KIN",
        "CYI",
        "PIF",
        "LZN",
        "CMJ",
        "RCTP",
        "RCSS",
        "RCKH",
        "RCMQ",
        "RCNN",
        "RCHU",
        "RCFG",
        "RCBS",
        "RCFN",
        "RCKW",
        "RCMT",
        "RCLY",
    }
    if s in tw_airport_codes or (len(s) == 4 and s.startswith("RC")):
        return True
    tw_name_keywords = [
        "TAIPEI",
        "TAIWAN",
        "KAOHSIUNG",
        "TAICHUNG",
        "TAINAN",
        "台北",
        "台灣",
        "高雄",
        "台中",
        "台南",
    ]
    return any(kw in s for kw in tw_name_keywords)


def is_taiwan_registration(reg: str) -> bool:
    if not reg or reg == "未知":
        return False
    return bool(re.fullmatch(r"^B-\d{5}$", reg.upper().strip()))


def get_routing_category(dest_text: str, reg_text: str) -> str:
    d = str(dest_text).upper().strip()
    if "TPE" in d or "RCTP" in d or "TAOYUAN" in d or "桃園" in d:
        return "TPE" if is_taiwan_registration(reg_text) else "TPE_FOREIGN"
    if "TSA" in d or "RCSS" in d or "SONGSHAN" in d or "松山" in d:
        return "TSA"
    if "KHH" in d or "RCKH" in d or "KAOHSIUNG" in d or "高雄" in d:
        return "KHH"
    return "ELSE"


# ============================================================
# 5. 多重圖片獨立請求與備援模組 (含 Wikimedia Commons 備援)
# ============================================================
def fetch_wikimedia_image(registration: str) -> str | None:
    if not registration or registration == "未知":
        return None
    try:
        print(f"     [圖片] 正在向 Wikimedia Commons 搜尋 {registration} 的圖片...")
        search_url = "https://commons.wikimedia.org/w/api.php"
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srnamespace": "6",
            "srsearch": registration,
            "srlimit": 1,
        }
        headers = {
            "User-Agent": (
                "FlightTrackerBot/1.0 (Educational Project; contact via GitHub)"
            )
        }
        res = requests.get(
            search_url, params=search_params, headers=headers, timeout=5
        )

        if res.status_code == 200:
            data = res.json()
            search_results = data.get("query", {}).get("search", [])
            if search_results:
                file_title = search_results[0].get("title")
                info_params = {
                    "action": "query",
                    "format": "json",
                    "titles": file_title,
                    "prop": "imageinfo",
                    "iiprop": "url",
                }
                info_res = requests.get(
                    search_url, params=info_params, headers=headers, timeout=5
                )
                if info_res.status_code == 200:
                    pages = info_res.json().get("query", {}).get("pages", {})
                    for _, page_info in pages.items():
                        imageinfo = page_info.get("imageinfo", [])
                        if imageinfo:
                            print(f"     [圖片] 成功從 Wikimedia Commons 獲取圖片！")
                            return imageinfo[0].get("url")
    except Exception as e:
        print(f"     [圖片] Wikimedia 查詢異常: {e}")
    return None


def fetch_jetphotos_image(registration: str) -> str | None:
    if not registration or registration == "未知":
        return None

    registration = registration.strip().upper()

    try:
        print(f"     [圖片] JetPhotos 搜尋 {registration}...")
        url = f"https://www.jetphotos.com/api/json?reg={registration}"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.jetphotos.com/",
        }

        time.sleep(random.uniform(1.5, 3.0))

        res = requests.get(
            url, headers=headers, timeout=8, allow_redirects=True
        )

        if res.status_code in (403, 429):
            print(
                f"     [圖片] ⚠️ JetPhotos {res.status_code}，"
                f"GitHub Actions 來源可能被限制，跳過"
            )
            return None

        if res.status_code != 200:
            print(f"     [圖片] JetPhotos HTTP {res.status_code}")
            return None

        try:
            data = res.json()
        except Exception:
            print("     [圖片] JetPhotos 回傳不是 JSON")
            return None

        photos = data.get("data", [])
        if not photos:
            print(f"     [圖片] JetPhotos 沒有找到 {registration}")
            return None

        for photo in photos:
            image_url = (
                photo.get("file_url")
                or photo.get("thumbnail_large_url")
                or photo.get("thumbnail_url")
            )
            if image_url:
                print(f"     [圖片] ✅ JetPhotos 成功")
                return image_url

    except requests.RequestException as e:
        print(f"     [圖片] JetPhotos 網路錯誤：{e}")
    except Exception as e:
        print(f"     [圖片] JetPhotos 異常：{e}")

    return None


def fetch_planespotters_image(registration: str) -> str | None:
    if not registration or registration == "未知":
        return None

    registration = registration.strip().upper()

    try:
        print(f"     [圖片] PlaneSpotters 搜尋 {registration}...")
        url = f"https://api.planespotters.net/pub/photos/reg/{registration}"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.planespotters.net/",
        }

        time.sleep(random.uniform(1.5, 3.0))

        res = requests.get(
            url, headers=headers, timeout=8, allow_redirects=True
        )

        if res.status_code in (403, 429):
            print(
                f"     [圖片] ⚠️ PlaneSpotters {res.status_code}，"
                f"跳過並改用 Wikimedia"
            )
            return None

        if res.status_code != 200:
            print(f"     [圖片] PlaneSpotters HTTP {res.status_code}")
            return None

        try:
            data = res.json()
        except Exception:
            print("     [圖片] PlaneSpotters 回傳不是 JSON")
            return None

        photos = data.get("photos", [])
        if photos:
            for photo in photos:
                image_url = (
                    photo.get("thumbnail_large", {}).get("src")
                    or photo.get("thumbnail", {}).get("src")
                )
                if image_url:
                    print(f"     [圖片] ✅ PlaneSpotters 成功")
                    return image_url

    except requests.RequestException as e:
        print(f"     [圖片] PlaneSpotters 網路錯誤：{e}")
    except Exception as e:
        print(f"     [圖片] PlaneSpotters 異常：{e}")

    return None


def get_best_image_for_target(f_reg: str, fr_api_inst) -> str | None:
    if not f_reg or f_reg == "未知":
        return None

    f_reg = f_reg.strip().upper()

    # 0. 先讀本地快取
    cached = load_cached_image(f_reg)
    if cached:
        return cached

    # 1. FR24
    try:
        print(f"     [圖片] FR24 搜尋 {f_reg} 官方圖片...")
        history_url = (
            "https://api.flightradar24.com/common/v1/flight/"
            f"list.json?query={f_reg}"
            "&fetchBy=reg&page=1&limit=3"
        )
        h_res = http_session.get(
            history_url, headers=get_headers(), timeout=6
        )

        if h_res.status_code == 200:
            data = h_res.json()
            flights = (
                data.get("result", {})
                .get("response", {})
                .get("data", [])
            )
            for h_f in flights:
                flight_id = (
                    h_f.get("identification", {})
                    .get("id")
                )
                if not flight_id:
                    continue

                details = fetch_direct_clickhandler(fr_api_inst, flight_id)
                if details:
                    image_url = details.get("image_url")
                    if image_url:
                        print(f"     [圖片] ✅ FR24 官方圖片成功")
                        save_cached_image(f_reg, image_url)
                        return image_url
                break
    except Exception as e:
        print(f"     [圖片] FR24 圖片查詢失敗：{e}")

    # 2. JetPhotos
    image_url = fetch_jetphotos_image(f_reg)
    if image_url:
        save_cached_image(f_reg, image_url)
        return image_url

    # 3. PlaneSpotters
    image_url = fetch_planespotters_image(f_reg)
    if image_url:
        save_cached_image(f_reg, image_url)
        return image_url

    # 4. Wikimedia Commons
    image_url = fetch_wikimedia_image(f_reg)
    if image_url:
        save_cached_image(f_reg, image_url)
        return image_url

    print(f"     [圖片] ❌ {f_reg} 所有圖片來源皆無結果")
    return None


# ============================================================
# 6. FlightRadar24 擷取即時詳細資料
# ============================================================
def fetch_direct_clickhandler(fr_api_inst, flight_obj_or_id) -> dict | None:
    try:
        if hasattr(flight_obj_or_id, "id"):
            details = fr_api_inst.get_flight_details(flight_obj_or_id)
        else:
            class DummyFlight:
                def __init__(self, fid):
                    self.id = fid
            details = fr_api_inst.get_flight_details(DummyFlight(flight_obj_or_id))

        if not details or not isinstance(details, dict):
            return None

        airport = details.get("airport") or {}
        orig_obj = (airport.get("origin") or {}).get("code") or {}
        dest_obj = (airport.get("destination") or {}).get("code") or {}

        origin = (
            orig_obj.get("iata")
            or orig_obj.get("icao")
            or (airport.get("origin") or {}).get("name")
            or "未知"
        )
        destination = (
            dest_obj.get("iata")
            or dest_obj.get("icao")
            or (
                airport.get("destination") or {}
                .get("pluginData", {})
                .get("details", {})
                .get("name")
            )
            or "未知"
        )

        ident = details.get("identification") or {}
        f_num = (
            (ident.get("number") or {}).get("default")
            or (ident.get("callsign") or {}).get("default")
            or "未知"
        )

        ac = details.get("aircraft") or {}
        f_reg = ac.get("registration") or "未知"
        ac_code = (ac.get("model") or {}).get("code") or "未知"

        time_data = details.get("time") or {}
        sta_ts = (time_data.get("scheduled") or {}).get("arrival")
        eta_ts = (time_data.get("estimated") or {}).get("arrival")
        ata_ts = (time_data.get("real") or {}).get("arrival")
        arr_ts = eta_ts or ata_ts or sta_ts
        eta_full = format_full_datetime(arr_ts)

        image_url = None
        images = ac.get("images") or {}
        large_images = images.get("large") or images.get("medium") or []
        if large_images and isinstance(large_images, list) and len(large_images) > 0:
            image_url = large_images[0].get("src")

        return {
            "origin": origin,
            "destination": destination,
            "f_num": f_num,
            "f_reg": f_reg,
            "ac_code": ac_code,
            "arr_ts": arr_ts,
            "eta_time": eta_full,
            "image_url": image_url,
        }
    except Exception:
        return None


# ============================================================
# 7. 建立高速航班索引 & 尋找目標
# ============================================================
def build_flight_index(all_flights: list) -> dict:
    index = {"registration": {}, "number": {}, "callsign": {}, "normalized": {}}
    for f in all_flights:
        f_num = (getattr(f, "number", "") or "").upper().strip()
        f_callsign = (getattr(f, "callsign", "") or "").upper().strip()
        f_reg = (getattr(f, "registration", "") or "").upper().strip()

        for val, key in [
            (f_reg, "registration"),
            (f_num, "number"),
            (f_callsign, "callsign"),
        ]:
            if val:
                index[key][val] = f
                if norm := normalize_target(val):
                    index["normalized"][norm] = f
    return index


def find_target_in_index(target: str, flight_index: dict):
    t_upper = target.upper().strip()
    t_norm = normalize_target(t_upper)
    for key, mt in [
        ("registration", "EXACT_REGISTRATION"),
        ("number", "EXACT_FLIGHT_NUMBER"),
        ("callsign", "EXACT_CALLSIGN"),
    ]:
        if f := flight_index[key].get(t_upper):
            return f, mt
    if t_norm and (f := flight_index["normalized"].get(t_norm)):
        return f, "NORMALIZED_MATCH"
    return None, None


# ============================================================
# 8. 構建標準結果
# ============================================================
def build_result(target_raw: str, flight, details: dict, source: str) -> dict | None:
    if not details:
        return None
    orig, dest, arr_ts = details["origin"], details["destination"], details["arr_ts"]

    f_num = details["f_num"]
    if f_num == "未知":
        f_num = (
            getattr(flight, "number", "")
            or getattr(flight, "callsign", "")
            or target_raw
        )
    f_reg = details["f_reg"]
    if f_reg == "未知":
        f_reg = getattr(flight, "registration", "") or target_raw

    return {
        "target": target_raw,
        "f_num": f_num,
        "f_reg": f_reg,
        "ac_code": details["ac_code"],
        "route": f"{orig} ➔ {dest}",
        "destination": dest,
        "eta_time": details["eta_time"],
        "arr_ts": arr_ts,
        "is_taiwan_dest": check_is_taiwan(dest),
        "image_url": details["image_url"],
        "source": source,
    }


# ============================================================
# 9. 階段 1.5：掃描台灣機場降落時刻表 (無圖片負擔)
# ============================================================
def scan_taiwan_airport_schedules(unmatched_targets: list) -> dict:
    print("\n🛬 階段 1.5：主動掃描台灣四大機場降落時刻表...")
    start_time = time.time()
    airports = ["TPE", "TSA", "KHH", "RMQ"]
    matched = {}
    query_ts = int(time.time()) - (2 * 3600)
    current_ts = int(time.time())

    for apt in airports:
        url = (
            f"https://api.flightradar24.com/common/v1/airport.json?code={apt}"
            f"&plugin[]=schedule&plugin-setting[schedule][mode]=arrivals"
            f"&plugin-setting[schedule][timestamp]={query_ts}&page=1&limit=150"
        )
        try:
            res = http_session.get(url, headers=get_headers(), timeout=5)
            if res.status_code != 200:
                continue

            arrivals = (
                res.json()
                .get("result", {})
                .get("response", {})
                .get("airport", {})
                .get("pluginData", {})
                .get("schedule", {})
                .get("arrivals", {})
                .get("data", [])
            )
            for arr in arrivals:
                flight = arr.get("flight", {})
                f_reg = flight.get("aircraft", {}).get("registration", "")
                f_num = (
                    flight.get("identification", {})
                    .get("number", {})
                    .get("default", "")
                )
                arr_ts = flight.get("time", {}).get("scheduled", {}).get("arrival")

                if arr_ts and int(arr_ts) < current_ts:
                    continue

                for t in unmatched_targets:
                    t_norm = normalize_target(t)
                    if not t_norm:
                        continue

                    if t_norm == normalize_target(f_reg) or t_norm == normalize_target(f_num):
                        orig = (
                            flight.get("airport", {})
                            .get("origin", {})
                            .get("code", {})
                            .get("iata", "未知")
                        )
                        eta_time_str = format_full_datetime(arr_ts)

                        matched[t] = {
                            "target": t,
                            "f_num": f_num or t,
                            "f_reg": f_reg or "未知",
                            "ac_code": (
                                flight.get("aircraft", {})
                                .get("model", {})
                                .get("code", "未知")
                            ),
                            "route": f"{orig} ➔ {apt}",
                            "destination": apt,
                            "eta_time": eta_time_str,
                            "arr_ts": arr_ts,
                            "is_taiwan_dest": True,
                            "image_url": None,
                            "source": f"📅 機場時刻表 ({apt})",
                        }
                        print(
                            f"  └─ 🟢 [時刻表抓取] {t} -> {f_num} ({orig} ➔ {apt}) | 🕒 {eta_time_str}"
                        )
        except Exception as e:
            print(f"⚠️ 讀取機場 {apt} 失敗: {e}")

    print(f"🛬 階段 1.5 完成：{time.time() - start_time:.2f} 秒 (找到 {len(matched)} 架)")
    return matched


# ============================================================
# 10. 使用 Flight List API 精準補查 (無圖片負擔)
# ============================================================
def web_search_target(target_raw: str) -> dict | None:
    target_raw = target_raw.upper().strip()
    current_ts = int(time.time())

    for fetch_by in ["reg", "flight"]:
        url = (
            "https://api.flightradar24.com/common/v1/flight/list.json"
            f"?query={target_raw}&fetchBy={fetch_by}&page=1&limit=15"
        )
        try:
            res = http_session.get(url, headers=get_headers(), timeout=5)
            if res.status_code != 200:
                continue

            flights = res.json().get("result", {}).get("response", {}).get("data", [])
            if not flights:
                continue

            valid_flights = []
            for f in flights:
                orig = f.get("airport", {}).get("origin", {}).get("code", {}).get("iata")
                dest = f.get("airport", {}).get("destination", {}).get("code", {}).get("iata")
                if not orig or not dest:
                    continue

                t_info = f.get("time", {})
                arr_ts = (
                    t_info.get("estimated", {}).get("arrival")
                    or t_info.get("scheduled", {}).get("arrival")
                    or t_info.get("real", {}).get("arrival")
                )

                if arr_ts and int(arr_ts) >= current_ts:
                    valid_flights.append((f, int(arr_ts), dest))

            if not valid_flights:
                continue

            best_flight = None
            tw_flights = [
                (f, ts, dest)
                for f, ts, dest in valid_flights
                if check_is_taiwan(dest) and abs(ts - current_ts) <= (12 * 3600)
            ]

            if tw_flights:
                best_flight = min(tw_flights, key=lambda x: abs(x[1] - current_ts))[0]
            else:
                recent_flights = [
                    (f, ts, dest)
                    for f, ts, dest in valid_flights
                    if abs(ts - current_ts) <= (24 * 3600)
                ]
                if recent_flights:
                    best_flight = min(recent_flights, key=lambda x: abs(x[1] - current_ts))[0]
                else:
                    best_flight = min(valid_flights, key=lambda x: abs(x[1] - current_ts))[0]

            if not best_flight:
                continue

            orig = (
                best_flight.get("airport", {})
                .get("origin", {})
                .get("code", {})
                .get("iata", "未知")
            )
            dest = (
                best_flight.get("airport", {})
                .get("destination", {})
                .get("code", {})
                .get("iata", "未知")
            )
            f_reg = best_flight.get("aircraft", {}).get("registration", "未知")
            ident = best_flight.get("identification", {})
            f_num = (
                ident.get("number", {}).get("default")
                or ident.get("callsign", {}).get("default")
                or target_raw
            )
            ac_code = (
                best_flight.get("aircraft", {})
                .get("model", {})
                .get("code", "未知")
            )

            t_info = best_flight.get("time", {})
            arr_ts = (
                t_info.get("estimated", {}).get("arrival")
                or t_info.get("scheduled", {}).get("arrival")
                or t_info.get("real", {}).get("arrival")
            )

            t_norm = normalize_target(target_raw)
            if not (t_norm == normalize_target(f_reg) or t_norm == normalize_target(f_num)):
                continue

            return {
                "target": target_raw,
                "f_num": f_num,
                "f_reg": f_reg,
                "ac_code": ac_code,
                "route": f"{orig} ➔ {dest}",
                "destination": dest,
                "eta_time": format_full_datetime(arr_ts),
                "arr_ts": arr_ts,
                "is_taiwan_dest": check_is_taiwan(dest),
                "image_url": None,
                "source": f"🔍 航班資料庫 API ({fetch_by.upper()})",
            }
        except Exception:
            continue
    return None


# ============================================================
# 11. Discord 多渠道推播發送 (延遲加載版)
# ============================================================
def send_discord_webhook(taiwan_flights: list):
    webhook_routes = {
        "TPE": DISCORD_WEBHOOK_URL_TPE or DEFAULT_WEBHOOK_URL,
        "TPE_FOREIGN": DISCORD_WEBHOOK_URL_TPE_FOREIGN or DEFAULT_WEBHOOK_URL,
        "TSA": DISCORD_WEBHOOK_URL_TSA or DEFAULT_WEBHOOK_URL,
        "KHH": DISCORD_WEBHOOK_URL_KHH or DEFAULT_WEBHOOK_URL,
        "ELSE": DISCORD_WEBHOOK_URL_ELSE or DEFAULT_WEBHOOK_URL,
    }

    grouped_flights = defaultdict(list)
    for f in taiwan_flights:
        cat = get_routing_category(f.get("destination", ""), f.get("f_reg", ""))
        grouped_flights[cat].append(f)

    for category, flights in grouped_flights.items():
        target_webhook = webhook_routes.get(category)
        if not target_webhook:
            print(
                f"⚠️ 未設定 [{category}] 的 Webhook URL，跳過該分類共 {len(flights)} 架班機。"
            )
            continue

        embeds = []
        for f in flights:
            embed = {
                "title": f"🚨 [{category}] 彩繪機降落警報：{f['f_num']}",
                "color": 15158332,
                "fields": [
                    {
                        "name": "機身註冊號",
                        "value": f"`{f['f_reg']}` ({f['ac_code']})",
                        "inline": True,
                    },
                    {
                        "name": "航線狀況",
                        "value": f"📍 **{f['route']}**",
                        "inline": True,
                    },
                    {
                        "name": "預計抵達 (UTC+8)",
                        "value": f"🕒 `{f['eta_time']}`",
                        "inline": False,
                    },
                ],
                "footer": {
                    "text": f"FR24 智慧航班監測系統 • 來源：{f['source']}"
                },
            }
            if f.get("image_url"):
                embed["image"] = {"url": f["image_url"]}
            embeds.append(embed)

        for i in range(0, len(embeds), 10):
            batch = embeds[i : i + 10]
            try:
                res = requests.post(target_webhook, json={"embeds": batch}, timeout=5)
                if res.status_code in [200, 204]:
                    print(
                        f"✅ 成功推播 [{category}] 第 {i//10 + 1} 批共 {len(batch)} 架航班！"
                    )
                else:
                    print(
                        f"❌ [{category}] Discord 發送失敗，狀態碼: {res.status_code}"
                    )
            except Exception as e:
                print(f"❌ [{category}] Discord 發送異常: {e}")


# ============================================================
# 12. 工作流程 (Scanner)
# ============================================================
def deep_scan_unmatched(unmatched_targets: list):
    if not unmatched_targets:
        return {}
    print(f"\n🔍 第二階段：啟動航班資料庫深層補查 ({len(unmatched_targets)} 架)...")
    start_time = time.time()
    results = {}

    with ThreadPoolExecutor(
        max_workers=min(10, max(1, len(unmatched_targets)))
    ) as executor:
        future_to_target = {
            executor.submit(web_search_target, target): target
            for target in unmatched_targets
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                if res := future.result():
                    results[target] = res
                    print(
                        f"  └─ 🟢 [補查成功] {target} -> {res['f_num']} ({res['route']}) | 🕒 {res['eta_time']}"
                    )
                else:
                    print(f"  └─ ⚪ [無結果] {target}")
            except Exception as e:
                print(f"  └─ ❌ [補查錯誤] {target}: {e}")

    print(
        f"\n🔍 第二階段完成：{time.time() - start_time:.2f} 秒 (補查成功 {len(results)} 架)"
    )
    return results


# ============================================================
# 13. 主程式
# ============================================================
def main():
    program_start = time.time()
    print(
        "=" * 65
        + "\n🛬 FR24 高速智慧航班降落監測系統 (100% 資料庫覆蓋升級版)\n"
        + "=" * 65
    )

    if not TARGETS:
        print("🛑 沒有偵測到任何監控目標，程式結束。")
        return
    print(f"🎯 監控目標：{len(TARGETS)} 架")

    try:
        fr_api_inst = FlightRadar24API()
    except Exception as e:
        print(f"❌ 無法建立 FlightRadar24API：{e}")
        return

    current_ts = int(time.time())

    # === 第一階段：Live 高速掃描 ===
    print("\n⚡ 第一階段：高速一次性 Live 掃描開始...")
    matched_dict, unmatched_targets = {}, []
    snapshot = fr_api_inst.get_flights() or []
    flight_index = build_flight_index(snapshot)

    for target in TARGETS:
        flight, _ = find_target_in_index(target, flight_index)
        if flight and (details := fetch_direct_clickhandler(fr_api_inst, flight)):
            arr_ts = details.get("arr_ts")
            if arr_ts and int(arr_ts) < current_ts:
                continue

            res = build_result(target, flight, details, "📡 FR24 直播廣播")
            if res:
                matched_dict[target] = res
                print(
                    f"  └─ 🟢 [Live掃描] {target} -> {res['f_num']} ({res['route']}) | 🕒 {res['eta_time']}"
                )
                continue
        unmatched_targets.append(target)
    print(f"⚡ 第一階段完成 (找到 {len(matched_dict)} 架)")

    # === 階段 1.5：機場時刻表主動掃描 ===
    if unmatched_targets:
        schedule_matches = scan_taiwan_airport_schedules(unmatched_targets)
        matched_dict.update(schedule_matches)
        unmatched_targets = [t for t in unmatched_targets if t not in schedule_matches]

    # === 第二階段：Flight List API 專屬資料庫補查 ===
    if unmatched_targets:
        deep_results = deep_scan_unmatched(unmatched_targets)
        matched_dict.update(deep_results)

    # === 最終篩選 ===
    taiwan_arrivals = [
        f for f in matched_dict.values() if f.get("is_taiwan_dest")
    ]
    taiwan_arrivals.sort(key=lambda x: (x.get("arr_ts") or 0))
    final_unmatched = len(TARGETS) - len(matched_dict)

    print("\n" + "=" * 65 + "\n📊 掃描結果總結\n" + "=" * 65)
    print(f" • 監控目標數：{len(TARGETS)} 架")
    print(f" • 成功定位：{len(matched_dict)} 架")
    print(f" • ❌ 未找到：{final_unmatched} 架")
    print(f" • 🛬 預計降落台灣：{len(taiwan_arrivals)} 架")
    print(f" • ⏱️ 本次總耗時：{time.time() - program_start:.2f} 秒\n" + "=" * 65)

    # === 最終推播：檢查與獲取圖片 ===
    if taiwan_arrivals:
        print("\n🚨 發現預計降落台灣的目標，開始檢查與獲取圖片並準備推播...")
        for f in taiwan_arrivals:
            if not f.get("image_url"):
                f["image_url"] = get_best_image_for_target(f["f_reg"], fr_api_inst)
            print(
                f"  ✈️ {f['f_num']} | {f['f_reg']} | {f['ac_code']} | {f['route']} | {f['eta_time']}"
            )
        send_discord_webhook(taiwan_arrivals)
    else:
        print("\nℹ️ 目前沒有目標班機預計降落台灣。")

    if unmatched_targets:
        actually_unmatched = [t for t in TARGETS if t not in matched_dict]
        if actually_unmatched:
            print("\n❌ 以下目標目前無近期飛行紀錄 (可能在長程維修中)：")
            for t in actually_unmatched:
                print(f"    - {t}")

    print("\n✅ 程式執行完成。")


if __name__ == "__main__":
    main()
