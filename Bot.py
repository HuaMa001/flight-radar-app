from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import os
import random
import time
import requests
from FlightRadarAPI import FlightRadar24API

# --- 1. 環境變數與 targets.txt 讀取邏輯 ---
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL", os.getenv("DISCORD", "")
)


def load_targets(filepath: str = "targets.txt") -> list[str]:
    """優先從 txt 檔案讀取監控清單，若不存在則嘗試讀取環境變數 TARGET_PLANES"""
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
            targets = [
                t.strip().upper() for t in cleaned_raw.split(",") if t.strip()
            ]
            print(f"📋 成功從環境變數載入 {len(targets)} 架目標飛機！")

    unique_targets = list(dict.fromkeys(targets))
    return unique_targets


TARGETS = load_targets("targets.txt")

http_session = requests.Session()


def get_random_headers():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
        "Origin": "https://www.flightradar24.com",
    }


# --- 2. 核心判斷與查詢邏輯 ---
def format_full_datetime(ts: int | None) -> str:
    if not ts:
        return "未知"
    try:
        tz_tw = timezone(timedelta(hours=8))
        dt = datetime.fromtimestamp(ts, tz=tz_tw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"


def check_is_taiwan(text_or_code: str) -> bool:
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper().strip()

    tw_airport_codes = {
        "TPE", "TSA", "KHH", "RMQ", "TNN", "HUN", "TTT", "MZG", "KIN", "CYI", "PIF", "LZN", "CMJ",
        "RCTP", "RCSS", "RCKH", "RCMQ", "RCNN", "RCHU", "RCFG", "RCBS", "RCFN", "RCKW", "RCMT", "RCLY",
    }

    if s in tw_airport_codes or (len(s) == 4 and s.startswith("RC")):
        return True

    tw_name_keywords = [
        "TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN",
        "台北", "台灣", "高雄", "台中", "台南",
    ]
    return any(kw in s for kw in tw_name_keywords)


def fetch_planespotters_image(registration: str) -> str | None:
    if not registration or registration == "未知":
        return None
    try:
        url = f"https://api.planespotters.net/pub/photos/reg/{registration}"
        res = http_session.get(url, headers=get_random_headers(), timeout=4)
        if res.status_code == 200:
            photos = res.json().get("photos", [])
            if photos:
                return photos[0].get("thumbnail_large", {}).get("src") or photos[0].get("thumbnail", {}).get("src")
    except Exception:
        pass
    return None


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
            or (airport.get("destination") or {})
            .get("pluginData", {})
            .get("details", {})
            .get("name")
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

        eta_full = format_full_datetime(eta_ts or ata_ts or sta_ts)

        image_url = None
        images = ac.get("images") or {}
        large_images = images.get("large") or images.get("medium") or []

        if large_images and isinstance(large_images, list) and len(large_images) > 0:
            image_url = large_images[0].get("src")

        if not image_url and f_reg != "未知":
            image_url = fetch_planespotters_image(f_reg)

        return {
            "origin": origin,
            "destination": destination,
            "f_num": f_num,
            "f_reg": f_reg,
            "ac_code": ac_code,
            "eta_time": eta_full,
            "image_url": image_url,
        }
    except Exception:
        return None


def fetch_multi_zone_snapshot(fr_api_inst) -> list:
    """正確呼叫 API 獲取全球及區域航班快照"""
    all_flights_dict = {}

    # 1. 抓取標準全域快照
    try:
        global_flights = fr_api_inst.get_flights() or []
        for f in global_flights:
            fid = getattr(f, "id", None)
            if fid:
                all_flights_dict[fid] = f
    except Exception:
        pass

    # 2. 抓取特定區域 (亞洲、歐洲、北美) 邊界數據擴充
    try:
        zones = fr_api_inst.get_zones()
        for zone_key in ["asia", "europe", "northamerica"]:
            if zone_key in zones:
                try:
                    bounds = fr_api_inst.get_bounds(zones[zone_key])
                    regional_flights = fr_api_inst.get_flights(bounds=bounds) or []
                    for f in regional_flights:
                        fid = getattr(f, "id", None)
                        if fid and fid not in all_flights_dict:
                            all_flights_dict[fid] = f
                except Exception:
                    pass
    except Exception:
        pass

    return list(all_flights_dict.values())


def search_single_target_worker(target_raw: str, all_flights: list, fr_api_inst) -> dict | None:
    target_clean = target_raw.replace("-", "")
    flight_map_by_id = {
        getattr(f, "id", ""): f for f in all_flights if getattr(f, "id", "")
    }

    # 階段 1：直播廣播數據比對
    for flight in all_flights:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_callsign = (getattr(flight, "callsign", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()

        matched = target_raw in [f_num, f_callsign, f_reg] or target_clean in [
            f_num.replace("-", ""),
            f_callsign.replace("-", ""),
            f_reg.replace("-", ""),
        ]

        if matched:
            details = fetch_direct_clickhandler(fr_api_inst, flight)
            if details:
                dest = details["destination"]
                is_tw = check_is_taiwan(dest)
                return {
                    "target": target_raw,
                    "f_num": details["f_num"] if details["f_num"] != "未知" else (f_num or f_callsign),
                    "f_reg": details["f_reg"] if details["f_reg"] != "未知" else (f_reg or target_raw),
                    "ac_code": details["ac_code"],
                    "route": f"{details['origin']} ➔ {dest}",
                    "eta_time": details["eta_time"],
                    "is_taiwan": is_tw,
                    "image_url": details["image_url"],
                    "source": "📡 直播廣播",
                }

    # 階段 2：Web API 線上反查
    time.sleep(random.uniform(0.1, 0.3))
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"

    try:
        res = http_session.get(search_url, headers=get_random_headers(), timeout=5)
        if res.status_code == 200:
            results = sorted(
                res.json().get("results", []),
                key=lambda x: (x.get("type") != "live", str(x.get("id", ""))),
            )
            for item in results:
                if item.get("type") == "live":
                    live_id = str(item.get("id", "")).strip()
                    if live_id:
                        target_obj = flight_map_by_id.get(live_id, live_id)
                        details = fetch_direct_clickhandler(fr_api_inst, target_obj)

                        if details:
                            dest = details["destination"]
                            is_tw = check_is_taiwan(dest)
                            return {
                                "target": target_raw,
                                "f_num": details["f_num"] if details["f_num"] != "未知" else target_raw,
                                "f_reg": details["f_reg"] if details["f_reg"] != "未知" else target_raw,
                                "ac_code": details["ac_code"],
                                "route": f"{details['origin']} ➔ {dest}",
                                "eta_time": details["eta_time"],
                                "is_taiwan": is_tw,
                                "image_url": details["image_url"],
                                "source": "🔍 Web API 反查",
                            }
    except Exception:
        pass

    return None


# --- 3. Discord 推播 ---
def send_discord_webhook(taiwan_flights: list):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD Webhook URL，跳過推播。")
        return

    embeds = []
    for f in taiwan_flights:
        embed = {
            "title": f"🚨 彩繪機降落台灣警報：{f['f_num']}",
            "color": 15158332,
            "fields": [
                {"name": "機身註冊號", "value": f"`{f['f_reg']}` ({f['ac_code']})", "inline": True},
                {"name": "航線狀況", "value": f"📍 **{f['route']}**", "inline": True},
                {"name": "預計抵達 (UTC+8)", "value": f"🕒 `{f['eta_time']}`", "inline": False},
            ],
            "footer": {"text": f"FR24 智慧航班監測系統 • 來源：{f['source']}"},
        }
        if f.get("image_url"):
            embed["image"] = {"url": f["image_url"]}

        embeds.append(embed)

    for i in range(0, len(embeds), 10):
        batch = embeds[i : i + 10]
        payload = {"embeds": batch}
        try:
            res = http_session.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
            if res.status_code in [200, 204]:
                print(f"✅ 成功推播第 {i//10 + 1} 批共 {len(batch)} 架降落台灣航班！")
            else:
                print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")


# --- 4. 主程式 ---
def main():
    if not TARGETS:
        print("🛑 沒有偵測到任何監控目標，程式結束。")
        return

    print("🚀 開始執行多輪穩定度搜尋...")

    matched_dict = {}
    stable_threshold = 5
    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    fr_api_inst = FlightRadar24API()

    while True:
        current_round += 1
        pending_targets = [t for t in TARGETS if t not in matched_dict]
        current_unmatched_count = len(pending_targets)

        if current_unmatched_count == 0:
            print("🎉 所有目標皆已在空中順利定位！")
            break

        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold:
            print(f"\n✅ 數據已穩定！連續 {stable_threshold} 輪未查到數量維持在 {current_unmatched_count} 架，結束輪詢。")
            break

        print(f"⚡ 第 {current_round:02d} 輪掃描... （待查：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）")

        snapshot = fetch_multi_zone_snapshot(fr_api_inst)
        print(f" └─ 📡 本輪成功融合全域與區域廣播數據，共獲得 {len(snapshot)} 架即時航班數據")

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_target = {
                executor.submit(
                    search_single_target_worker, target, snapshot, fr_api_inst
                ): target
                for target in pending_targets
            }

            for future in as_completed(future_to_target):
                target = future_to_target[future]
                try:
                    res = future.result()
                    if res:
                        matched_dict[target] = res
                except Exception:
                    pass

        time.sleep(1)

    taiwan_flights = [f for f in matched_dict.values() if f["is_taiwan"]]

    print(
        f"\n📊 掃描結果總結：\n"
        f" • 監控目標數：{len(TARGETS)} 架\n"
        f" • 在空中抓到：{len(matched_dict)} 架\n"
        f" • 🇹🇼 預計/已降落台灣：{len(taiwan_flights)} 架\n"
        f" • 尚未起飛/未在空中：{len(TARGETS) - len(matched_dict)} 架"
    )

    if taiwan_flights:
        send_discord_webhook(taiwan_flights)
    else:
        print("ℹ️ 目前沒有目標班機降落台灣，不發送 Discord 警報。")


if __name__ == "__main__":
    main()
    
