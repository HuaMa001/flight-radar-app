import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from FlightRadarAPI import FlightRadar24API

# 👈 1. 已改為讀取名為 DISCORD 的 Secret
DISCORD_WEBHOOK_URL = os.getenv("DISCORD", "")

# 監控目標清單 (機身號 / 註冊號)
TARGETS = [
    "B-KQU", "B-LRJ", "B-LJE", "HL7628", "B-18918", "B-18311", "B-18007", "B-5390",
    "JA872A", "B-17812", "B-16715", "JA880A", "JA731A", "JA875A", "JA614A", "9V-SWI",
    "9V-SWJ", "B-2032", "B-6091", "B-6093", "HL7732", "HL8071", "HS-TKQ", "HL7783",
    "VN-A897", "VN-A327", "B-6538", "PK-GMH", "PH-BVD", "9V-OJJ", "JA73AB", "JA894A",
    "B-18101", "A6-EXR", "A6-EES", "A6-EET", "A6-EEP", "A6-DDE", "A6-BLV", "A6-BMH",
    "LX-NCL", "LX-VCF", "HL7423", "HL7419", "JA12KZ", "N771CK", "N454PA", "N249BA"
]

fr_api = FlightRadar24API()


def check_is_taiwan(text_or_code: str) -> bool:
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper()
    tw_keywords = [
        "RC",
        "TPE",
        "TSA",
        "KHH",
        "RMQ",
        "TNN",
        "HUN",
        "TTT",
        "MZG",
        "KIN",
        "TAIPEI",
        "TAIWAN",
        "KAOHSIUNG",
    ]
    return any(kw in s for kw in tw_keywords)


def send_discord_webhook(taiwan_flights: list):
    """專用 Discord Embed 卡片發送函式"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未偵測到 DISCORD 金鑰設定，請檢查 GitHub Secrets！")
        return

    fields = []
    for f in taiwan_flights:
        fields.append({
            "name": f"✈️ 航班：{f['f_num']} (機身號: {f['f_reg']})",
            "value": f"📍 **航線：** {f['route']}",
            "inline": False,
        })

    # 建立 Discord 嵌入式美化訊息 (Embed)
    payload = {
        "embeds": [{
            "title": "🚨 FlightRadar24 彩繪機降落台灣警報",
            "description": (
                f"當前共有 **{len(taiwan_flights)}** 架目標班機預計或已降落台灣！"
            ),
            "color": 15158332,  # 警報紅色
            "fields": fields,
            "footer": {"text": "FlightRadar24 智慧航班監測系統"},
        }]
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if res.status_code in [200, 204]:
            print("✅ 成功發送 Discord Webhook 通知！")
        else:
            print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
    except Exception as e:
        print(f"❌ Discord 發送異常: {e}")


def fetch_details(flight_obj):
    try:
        details = fr_api.get_flight_details(flight_obj)
        if not details or not isinstance(details, dict):
            return None

        airport = details.get("airport") or {}
        orig_obj = (airport.get("origin") or {}).get("code") or {}
        dest_obj = (airport.get("destination") or {}).get("code") or {}

        origin = orig_obj.get("iata") or orig_obj.get("icao") or "未知"
        destination = dest_obj.get("iata") or dest_obj.get("icao") or "未知"

        ident = details.get("identification") or {}
        f_num = (ident.get("number") or {}).get("default") or "未知"
        ac = details.get("aircraft") or {}
        f_reg = ac.get("registration") or "未知"

        return {
            "f_num": f_num,
            "f_reg": f_reg,
            "route": f"{origin} ➔ {destination}",
            "is_taiwan": check_is_taiwan(destination),
        }
    except Exception:
        return None


def scan_single_target(target, snapshot):
    target_clean = target.replace("-", "")
    for flight in snapshot:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()
        if target in [f_num, f_reg] or target_clean in [
            f_num.replace("-", ""),
            f_reg.replace("-", ""),
        ]:
            details = fetch_details(flight)
            if details:
                return details
    return None


def main():
    print("🚀 開始掃描全球空域航班...")
    snapshot = fr_api.get_flights() or []
    taiwan_flights = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(scan_single_target, target, snapshot): target
            for target in TARGETS
        }
        for future in as_completed(futures):
            res = future.result()
            if res and res["is_taiwan"]:
                taiwan_flights.append(res)

    print(
        f"✅ 掃描完成！共找到 {len(taiwan_flights)} 架降落台灣的目標班機。"
    )

    if taiwan_flights:
        send_discord_webhook(taiwan_flights)

    # 👈 2. 測試用強制發送通知（測試成功後可將下面兩行刪除）
    test_data = [{"f_num": "TEST888", "f_reg": "B-TEST", "route": "TPE ➔ KHH (連線測試成功)"}]
    send_discord_webhook(test_data)


if __name__ == "__main__":
    main()
    
