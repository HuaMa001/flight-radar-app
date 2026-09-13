from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import os
import random
import time
import requests
from FlightRadarAPI import FlightRadar24API


# ============================================================
# 1. 環境變數與 targets.txt
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    os.getenv("DISCORD", "")
)

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
                raw_targets
                .replace("\r", "")
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
adapter = requests.adapters.HTTPAdapter(pool_connections=30, pool_maxsize=30, max_retries=1)
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)


# ============================================================
# 3. Header
# ============================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
        "Origin": "https://www.flightradar24.com",
        "Connection": "keep-alive",
    }


# ============================================================
# 4. 基本工具
# ============================================================

def normalize_target(value: str) -> str:
    if not value: return ""
    return str(value).upper().strip().replace("-", "").replace(" ", "")

def format_full_datetime(ts: int | None) -> str:
    if not ts: return "未知"
    try:
        tz_tw = timezone(timedelta(hours=8))
        dt = datetime.fromtimestamp(int(ts), tz=tz_tw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"

def check_is_taiwan(text_or_code: str) -> bool:
    if not text_or_code or text_or_code == "未知": return False
    s = str(text_or_code).upper().strip()
    tw_airport_codes = {
        "TPE", "TSA", "KHH", "RMQ", "TNN", "HUN", "TTT", "MZG",
        "KIN", "CYI", "PIF", "LZN", "CMJ", "RCTP", "RCSS", "RCKH",
        "RCMQ", "RCNN", "RCHU", "RCFG", "RCBS", "RCFN", "RCKW",
        "RCMT", "RCLY",
    }
    if s in tw_airport_codes: return True
    if len(s) == 4 and s.startswith("RC"): return True
    tw_name_keywords = [
        "TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN",
        "HUALIEN", "TAITUNG", "PENGHU", "KINMEN", "MATSU",
        "台北", "台灣", "高雄", "台中", "台南", "花蓮", "台東"
    ]
    return any(kw in s for kw in tw_name_keywords)


# ============================================================
# 5. PlaneSpotters 圖片兜底
# ============================================================

def fetch_planespotters_image(registration: str) -> str | None:
    if not registration or registration == "未知":
        return None
        
    try:
        url = f"https://api.planespotters.net/pub/photos/reg/{registration.strip()}"
        clean_headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json"
        }
        res = requests.get(url, headers=clean_headers, timeout=5)
        if res.status_code == 200:
            photos = res.json().get("photos", [])
            if photos:
                return photos[0].get("thumbnail_large", {}).get("src") or photos[0].get("thumbnail", {}).get("src")
    except Exception as e:
        print(f"⚠️ 獲取 {registration} 圖片發生異常: {e}")
        
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
            orig_obj.get("iata") or orig_obj.get("icao") or 
            (airport.get("origin") or {}).get("name") or "未知"
        )
        destination = (
            dest_obj.get("iata") or dest_obj.get("icao") or 
            (airport.get("destination") or {}).get("pluginData", {}).get("details", {}).get("name") or "未知"
        )

        ident = details.get("identification") or {}
        f_num = (ident.get("number") or {}).get("default") or (ident.get("callsign") or {}).get("default") or "未知"

        ac = details.get("aircraft") or {}
        f_reg = ac.get("registration") or "未知"
        ac_code = (ac.get("model") or {}).get("code") or "未知"

        # ----------------------------------------------------
        # 僅擷取「起飛時間」(已刪除抵達時間)
        # ----------------------------------------------------
        time_data = details.get("time") or {}
        std_ts = (time_data.get("scheduled") or {}).get("departure")
        etd_ts = (time_data.get("estimated") or {}).get("departure")
        atd_ts = (time_data.get("real") or {}).get("departure")
        dep_ts = etd_ts or std_ts or atd_ts
        dep_full = format_full_datetime(dep_ts)

        # ----------------------------------------------------
        # 圖片擷取優先使用 FR24 官方大圖 (JetPhotos)
        # ----------------------------------------------------
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
            "dep_ts": dep_ts,
            "dep_time": dep_full,
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

        for val, key in [(f_reg, "registration"), (f_num, "number"), (f_callsign, "callsign")]:
            if val:
                index[key][val] = f
                if norm := normalize_target(val):
                    index["normalized"][norm] = f
    return index

def find_target_in_index(target: str, flight_index: dict):
    t_upper = target.upper().strip()
    t_norm = normalize_target(t_upper)
    for key, mt in [("registration", "EXACT_REGISTRATION"), ("number", "EXACT_FLIGHT_NUMBER"), ("callsign", "EXACT_CALLSIGN")]:
        if f := flight_index[key].get(t_upper): return f, mt
    if t_norm and (f := flight_index["normalized"].get(t_norm)):
        return f, "NORMALIZED_MATCH"
    return None, None


# ============================================================
# 8. 構建標準結果
# ============================================================

def build_result(target_raw: str, flight, details: dict, source: str) -> dict | None:
    if not details: return None
    orig, dest, dep_ts = details["origin"], details["destination"], details["dep_ts"]
    
    f_num = details["f_num"]
    if f_num == "未知": f_num = getattr(flight, "number", "") or getattr(flight, "callsign", "") or target_raw
    f_reg = details["f_reg"]
    if f_reg == "未知": f_reg = getattr(flight, "registration", "") or target_raw

    return {
        "target": target_raw, "f_num": f_num, "f_reg": f_reg, "ac_code": details["ac_code"],
        "route": f"{orig} ➔ {dest}", 
        "dep_time": details["dep_time"], "dep_ts": dep_ts,
        "is_taiwan_origin": check_is_taiwan(orig), 
        "is_future": bool(dep_ts and int(dep_ts) > int(time.time())),
        "image_url": details["image_url"], "source": source
    }


# ============================================================
# 9. 階段 1.5：掃描台灣機場起飛時刻表
# ============================================================

def scan_taiwan_airport_schedules(unmatched_targets: list, fr_api_inst) -> dict:
    print("\n🛫 階段 1.5：主動掃描台灣四大機場起飛時刻表...")
    start_time = time.time()
    airports = ["TPE", "TSA", "KHH", "RMQ"]
    matched = {}

    query_ts = int(time.time()) - (2 * 3600)

    for apt in airports:
        url = f"https://api.flightradar24.com/common/v1/airport.json?code={apt}&plugin[]=schedule&plugin-setting[schedule][mode]=departures&plugin-setting[schedule][timestamp]={query_ts}&page=1&limit=150"
        try:
            res = http_session.get(url, headers=get_headers(), timeout=5)
            if res.status_code != 200: continue

            departures = res.json().get("result", {}).get("response", {}).get("airport", {}).get("pluginData", {}).get("schedule", {}).get("departures", {}).get("data", [])
            for dep in departures:
                flight = dep.get("flight", {})
                f_reg = flight.get("aircraft", {}).get("registration", "")
                f_num = flight.get("identification", {}).get("number", {}).get("default", "")

                for t in unmatched_targets:
                    t_norm = normalize_target(t)
                    if not t_norm: continue

                    if t_norm == normalize_target(f_reg) or t_norm == normalize_target(f_num):
                        dest = flight.get("airport", {}).get("destination", {}).get("code", {}).get("iata", "未知")
                        dep_ts = flight.get("time", {}).get("scheduled", {}).get("departure")
                        
                        # 深度圖片反查：利用隱藏 flight id 優先獲取 FR24 JetPhotos 官方圖
                        image_url = None
                        flight_id = flight.get("identification", {}).get("id")
                        if flight_id:
                            try:
                                deep_details = fetch_direct_clickhandler(fr_api_inst, flight_id)
                                if deep_details and deep_details.get("image_url"):
                                    image_url = deep_details["image_url"]
                            except Exception:
                                pass
                        
                        if not image_url and f_reg:
                            image_url = fetch_planespotters_image(f_reg)
                        
                        matched[t] = {
                            "target": t, "f_num": f_num or t, "f_reg": f_reg or "未知",
                            "ac_code": flight.get("aircraft", {}).get("model", {}).get("code", "未知"),
                            "route": f"{apt} ➔ {dest}", 
                            "dep_time": format_full_datetime(dep_ts), "dep_ts": dep_ts,
                            "is_taiwan_origin": True,
                            "is_future": bool(dep_ts and int(dep_ts) > int(time.time())),
                            "image_url": image_url,
                            "source": f"📅 機場時刻表 ({apt})"
                        }
                        print(f"  └─ 🟢 [時刻表抓取] {t} -> {f_num} ({f_reg})")
        except Exception as e:
            print(f"⚠️ 讀取機場 {apt} 失敗: {e}")
            
    print(f"🛫 階段 1.5 完成：{time.time() - start_time:.2f} 秒 (找到 {len(matched)} 架)")
    return matched


# ============================================================
# 10. 使用 Flight List API 精準補查
# ============================================================

def web_search_target(target_raw: str, fr_api_inst) -> dict | None:
    target_raw = target_raw.upper().strip()

    for fetch_by in ["reg", "flight"]:
        url = f"https://api.flightradar24.com/common/v1/flight/list.json?query={target_raw}&fetchBy={fetch_by}&page=1&limit=15"
        try:
            res = http_session.get(url, headers=get_headers(), timeout=5)
            if res.status_code != 200: continue
            
            flights = res.json().get("result", {}).get("response", {}).get("data", [])
            if not flights: continue

            current_ts = int(time.time())
            valid_flights = []

            for f in flights:
                orig = f.get("airport", {}).get("origin", {}).get("code", {}).get("iata")
                dest = f.get("airport", {}).get("destination", {}).get("code", {}).get("iata")
                if not orig or not dest: continue

                t_info = f.get("time", {})
                dep_ts = t_info.get("estimated", {}).get("departure") or t_info.get("scheduled", {}).get("departure") or t_info.get("real", {}).get("departure")
                
                if dep_ts:
                    valid_flights.append((f, int(dep_ts), orig))

            if not valid_flights: continue

            best_flight = None

            tw_flights = [
                (f, ts) for f, ts, orig in valid_flights 
                if check_is_taiwan(orig) and abs(ts - current_ts) <= (12 * 3600)
            ]

            if tw_flights:
                best_flight = min(tw_flights, key=lambda x: abs(x[1] - current_ts))[0]
            else:
                recent_flights = [
                    (f, ts) for f, ts, orig in valid_flights 
                    if abs(ts - current_ts) <= (24 * 3600)
                ]
                if recent_flights:
                    best_flight = min(recent_flights, key=lambda x: abs(x[1] - current_ts))[0]
                else:
                    best_flight = min(valid_flights, key=lambda x: abs(x[1] - current_ts))[0]
            
            if not best_flight: continue

            orig = best_flight.get("airport", {}).get("origin", {}).get("code", {}).get("iata", "未知")
            dest = best_flight.get("airport", {}).get("destination", {}).get("code", {}).get("iata", "未知")
            f_reg = best_flight.get("aircraft", {}).get("registration", "未知")
            ident = best_flight.get("identification", {})
            f_num = ident.get("number", {}).get("default") or ident.get("callsign", {}).get("default") or target_raw
            ac_code = best_flight.get("aircraft", {}).get("model", {}).get("code", "未知")
            
            t_info = best_flight.get("time", {})
            dep_ts = t_info.get("estimated", {}).get("departure") or t_info.get("scheduled", {}).get("departure") or t_info.get("real", {}).get("departure")

            t_norm = normalize_target(target_raw)
            if not (t_norm == normalize_target(f_reg) or t_norm == normalize_target(f_num)):
                continue

            # 深度圖片反查：利用隱藏 flight id 優先獲取 FR24 JetPhotos 官方圖
            image_url = None
            flight_id = best_flight.get("identification", {}).get("id")
            if flight_id:
                try:
                    deep_details = fetch_direct_clickhandler(fr_api_inst, flight_id)
                    if deep_details and deep_details.get("image_url"):
                        image_url = deep_details["image_url"]
                except Exception:
                    pass
            
            if not image_url and f_reg != "未知":
                image_url = fetch_planespotters_image(f_reg)

            return {
                "target": target_raw, "f_num": f_num, "f_reg": f_reg, "ac_code": ac_code,
                "route": f"{orig} ➔ {dest}", 
                "dep_time": format_full_datetime(dep_ts), "dep_ts": dep_ts,
                "is_taiwan_origin": check_is_taiwan(orig),
                "is_future": bool(dep_ts and int(dep_ts) > current_ts),
                "image_url": image_url,
                "source": f"🔍 航班資料庫 API ({fetch_by.upper()})"
            }
        except Exception:
            continue
            
    return None


# ============================================================
# 11. Discord 與推播 (清爽排版：刪除抵達時間與搜尋方式)
# ============================================================

def send_discord_webhook(taiwan_flights: list):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD Webhook URL，跳過推播。")
        return

    embeds = []
    for f in taiwan_flights:
        embed = {
            "title": f"🚨 彩繪機台灣起飛警報：{f['f_num']}",
           "color": 0x3498DB, 
            "fields": [
                {
                    "name": "機身註冊號", 
                    "value": f"`{f['f_reg']}` ({f['ac_code']})", 
                    "inline": True
                },
                {
                    "name": "航線狀況", 
                    "value": f"📍 **{f['route']}**", 
                    "inline": True
                },
                {
                    "name": "預計起飛 (UTC+8)", 
                    "value": f"🕒 `{f['dep_time']}`", 
                    "inline": False
                }
            ],
            "footer": {
                "text": f"FR24 智慧航班監測系統 • 來源：{f['source']}"
            },
        }

        # 圖片處理
        if f.get("image_url"):
            embed["image"] = {"url": f["image_url"]}

        embeds.append(embed)

    for i in range(0, len(embeds), 10):
        batch = embeds[i : i + 10]
        payload = {"embeds": batch}
        
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
            if res.status_code in [200, 204]:
                print(f"✅ 成功推播第 {i // 10 + 1} 批共 {len(batch)} 架台灣起飛航班！")
            else:
                print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")


# ============================================================
# 12. 工作流程 (Scanner)
# ============================================================

def deep_scan_unmatched(unmatched_targets: list, fr_api_inst):
    if not unmatched_targets: return {}
    print(f"\n🔍 第二階段：啟動航班資料庫深層補查 ({len(unmatched_targets)} 架)...")
    start_time = time.time()
    results = {}
    
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(unmatched_targets)))) as executor:
        future_to_target = {executor.submit(web_search_target, target, fr_api_inst): target for target in unmatched_targets}
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                if res := future.result():
                    results[target] = res
                    print(f"  └─ 🟢 [補查成功] {target} -> {res['f_num']} ({res['route']})")
                else:
                    print(f"  └─ ⚪ [無結果] {target}")
            except Exception as e:
                print(f"  └─ ❌ [補查錯誤] {target}: {e}")

    print(f"\n🔍 第二階段完成：{time.time() - start_time:.2f} 秒 (補查成功 {len(results)} 架)")
    return results


# ============================================================
# 13. 最終篩選 (起飛時間：現在 - 10分 <= 起飛 <= 現在 + 10分)
# ============================================================

def filter_taiwan_departures(matched_dict: dict, minutes_ahead: int = 10, minutes_behind: int = 10):
    now_ts = int(time.time())
    
    upper_limit_ts = now_ts + (minutes_ahead * 60)
    lower_limit_ts = now_ts - (minutes_behind * 60)

    taiwan_departures = []
    
    for f in matched_dict.values():
        if not f.get("is_taiwan_origin"): 
            continue
            
        try:
            dep_ts = int(f.get("dep_ts", 0))
            if lower_limit_ts <= dep_ts <= upper_limit_ts: 
                taiwan_departures.append(f)
                
        except Exception:
            continue
            
    taiwan_departures.sort(key=lambda x: (x.get("dep_ts") or 0))
    return taiwan_departures


# ============================================================
# 14. 主程式
# ============================================================

def main():
    program_start = time.time()
    print("=" * 65 + "\n✈️ FR24 高速智慧航班起飛監測系統 (100% 資料庫覆蓋升級版)\n" + "=" * 65)

    if not TARGETS:
        print("🛑 沒有偵測到任何監控目標，程式結束。")
        return
    print(f"🎯 監控目標：{len(TARGETS)} 架")

    try: fr_api_inst = FlightRadar24API()
    except Exception as e:
        print(f"❌ 無法建立 FlightRadar24API：{e}")
        return

    # === 第一階段：Live 高速掃描 ===
    print("\n⚡ 第一階段：高速一次性 Live 掃描開始...")
    matched_dict, unmatched_targets = {}, []
    snapshot = fr_api_inst.get_flights() or []
    flight_index = build_flight_index(snapshot)
    
    for target in TARGETS:
        flight, match_type = find_target_in_index(target, flight_index)
        if flight and (details := fetch_direct_clickhandler(fr_api_inst, flight)):
            res = build_result(target, flight, details, "📡 FR24 直播廣播")
            if res:
                matched_dict[target] = res
                print(f"  └─ 🟢 {target} -> {res['f_num']} ({res['f_reg']}) [{match_type}]")
                continue
        unmatched_targets.append(target)
    print(f"⚡ 第一階段完成 (找到 {len(matched_dict)} 架)")

    # === 階段 1.5：機場時刻表主動掃描 ===
    if unmatched_targets:
        schedule_matches = scan_taiwan_airport_schedules(unmatched_targets, fr_api_inst)
        matched_dict.update(schedule_matches)
        unmatched_targets = [t for t in unmatched_targets if t not in schedule_matches]

    # === 第二階段：Flight List API 專屬資料庫補查 ===
    if unmatched_targets:
        deep_results = deep_scan_unmatched(unmatched_targets, fr_api_inst)
        matched_dict.update(deep_results)

    # === 最終篩選 & 推播 ===
    taiwan_departures = filter_taiwan_departures(matched_dict, minutes_ahead=10, minutes_behind=10)
    final_unmatched = len(TARGETS) - len(matched_dict)

    print("\n" + "=" * 65 + "\n📊 掃描結果總結\n" + "=" * 65)
    print(f" • 監控目標數：{len(TARGETS)} 架")
    print(f" • 成功定位：{len(matched_dict)} 架")
    print(f" • ❌ 未找到：{final_unmatched} 架")
    print(f" • 🛫 符合條件起飛區間：{len(taiwan_departures)} 架")
    print(f" • ⏱️ 本次總耗時：{time.time() - program_start:.2f} 秒\n" + "=" * 65)

    if taiwan_departures:
        print("\n🚨 發現符合時間區間起飛的目標：")
        for f in taiwan_departures:
            print(f"  ✈️ {f['f_num']} | {f['f_reg']} | {f['ac_code']} | {f['route']} | {f['dep_time']}")
        send_discord_webhook(taiwan_departures)
    else:
        print("\nℹ️ 目前沒有目標班機將在 前後 10 分鐘內 自台灣起飛。")

    if unmatched_targets:
        actually_unmatched = [t for t in TARGETS if t not in matched_dict]
        if actually_unmatched:
            print("\n❌ 以下目標目前無近期飛行紀錄 (可能在長程維修中)：")
            for t in actually_unmatched: print(f"   - {t}")

    print("\n✅ 程式執行完成。")


if __name__ == "__main__":
    main()
