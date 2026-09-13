from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import os
import random
import time
import requests
from urllib.parse import quote

from FlightRadarAPI import FlightRadar24API


# ============================================================
# 1. 環境變數與 targets.txt
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    os.getenv("DISCORD", "")
)


def load_targets(filepath: str = "targets.txt") -> list[str]:
    """
    從 targets.txt 讀取監控清單。

    如果不存在或沒有內容，
    嘗試讀取 TARGET_PLANES 環境變數。
    """

    targets = []

    if os.path.exists(filepath):

        try:

            with open(filepath, "r", encoding="utf-8") as f:

                targets = [
                    line.strip().upper()
                    for line in f
                    if line.strip()
                    and not line.strip().startswith("#")
                ]

            print(
                f"📁 成功從 `{filepath}` 載入 "
                f"{len(targets)} 架目標飛機！"
            )

        except Exception as e:

            print(
                f"⚠️ 讀取 `{filepath}` 失敗: {e}"
            )

    if not targets:

        raw_targets = os.getenv(
            "TARGET_PLANES",
            ""
        )

        if raw_targets and raw_targets.strip():

            cleaned_raw = (
                raw_targets
                .replace("\r", "")
                .replace("\n", ",")
                .replace("，", ",")
                .replace('"', "")
                .replace("'", "")
            )

            targets = [
                t.strip().upper()
                for t in cleaned_raw.split(",")
                if t.strip()
            ]

            print(
                f"📋 成功從環境變數載入 "
                f"{len(targets)} 架目標飛機！"
            )

    # 去除重複
    return list(dict.fromkeys(targets))


TARGETS = load_targets("targets.txt")


# ============================================================
# 2. HTTP Session
# ============================================================

http_session = requests.Session()

adapter = requests.adapters.HTTPAdapter(
    pool_connections=50,
    pool_maxsize=50,
    max_retries=0
)

http_session.mount(
    "https://",
    adapter
)

http_session.mount(
    "http://",
    adapter
)


# ============================================================
# 3. Header
# ============================================================

USER_AGENTS = [

    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),

    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),

    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),

    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
]


def get_headers():

    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": (
            "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Referer": "https://www.flightradar24.com/",
        "Origin": "https://www.flightradar24.com",
        "Connection": "keep-alive",
    }


# ============================================================
# 4. 基本工具
# ============================================================

def normalize_target(value: str) -> str:
    """
    強化版標準化。

    例如：

        B-1234
        B 1234
        b1234

    全部 -> B1234
    """

    if not value:

        return ""

    value = str(value).upper().strip()

    # 去除常見符號
    remove_chars = [
        "-",
        " ",
        "_",
        "/",
        ".",
    ]

    for c in remove_chars:

        value = value.replace(c, "")

    return value


def clean_value(value) -> str:

    if value is None:

        return ""

    if isinstance(value, dict):

        for key in [
            "default",
            "name",
            "value",
            "iata",
            "icao",
            "registration",
            "callsign",
            "number",
            "code",
        ]:

            if key in value and value[key]:

                return str(
                    value[key]
                ).upper().strip()

        return ""

    return str(value).upper().strip()


def format_full_datetime(ts: int | None) -> str:

    if not ts:

        return "未知"

    try:

        tz_tw = timezone(
            timedelta(hours=8)
        )

        dt = datetime.fromtimestamp(
            int(ts),
            tz=tz_tw
        )

        return dt.strftime(
            "%Y-%m-%d %H:%M"
        )

    except Exception:

        return "未知"


# ============================================================
# 5. 台灣判斷
# ============================================================

def check_is_taiwan(
    text_or_code: str
) -> bool:

    if (
        not text_or_code
        or text_or_code == "未知"
    ):

        return False

    s = str(
        text_or_code
    ).upper().strip()

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

    if s in tw_airport_codes:

        return True

    # ICAO 台灣機場
    if (
        len(s) == 4
        and s.startswith("RC")
    ):

        return True

    tw_name_keywords = [

        "TAIPEI",
        "TAIWAN",
        "KAOHSIUNG",
        "TAICHUNG",
        "TAINAN",
        "HUALIEN",
        "TAITUNG",
        "PENGHU",
        "KINMEN",
        "MATSU",

        "台北",
        "台灣",
        "高雄",
        "台中",
        "台南",
        "花蓮",
        "台東",
        "澎湖",
        "金門",
        "馬祖",
    ]

    return any(
        kw in s
        for kw in tw_name_keywords
    )


# ============================================================
# 6. PlaneSpotters
# ============================================================

def fetch_planespotters_image(
    registration: str
) -> str | None:

    if (
        not registration
        or registration == "未知"
    ):

        return None

    try:

        url = (
            "https://api.planespotters.net/pub/photos/reg/"
            f"{registration}"
        )

        res = http_session.get(
            url,
            headers=get_headers(),
            timeout=3
        )

        if res.status_code != 200:

            return None

        photos = (
            res.json().get(
                "photos",
                []
            )
        )

        if not photos:

            return None

        photo = photos[0]

        return (
            photo.get(
                "thumbnail_large",
                {}
            ).get("src")

            or

            photo.get(
                "thumbnail",
                {}
            ).get("src")
        )

    except Exception:

        return None


# ============================================================
# 7. FlightRadar24 Details
# ============================================================

def fetch_direct_clickhandler(
    fr_api_inst,
    flight_obj_or_id
) -> dict | None:

    try:

        # --------------------------------------------------------
        # 建立 Flight object
        # --------------------------------------------------------

        if hasattr(
            flight_obj_or_id,
            "id"
        ):

            flight_obj = (
                flight_obj_or_id
            )

        else:

            class DummyFlight:

                def __init__(self, fid):

                    self.id = fid

            flight_obj = DummyFlight(
                flight_obj_or_id
            )

        # --------------------------------------------------------
        # 取得詳細資料
        # --------------------------------------------------------

        details = (
            fr_api_inst.get_flight_details(
                flight_obj
            )
        )

        if (
            not details
            or not isinstance(details, dict)
        ):

            return None

        # --------------------------------------------------------
        # Airport
        # --------------------------------------------------------

        airport = (
            details.get(
                "airport"
            )
            or {}
        )

        origin_data = (
            airport.get(
                "origin"
            )
            or {}
        )

        destination_data = (
            airport.get(
                "destination"
            )
            or {}
        )

        origin_code = (
            origin_data.get(
                "code"
            )
            or {}
        )

        destination_code = (
            destination_data.get(
                "code"
            )
            or {}
        )

        origin = (
            origin_code.get("iata")
            or origin_code.get("icao")
            or origin_data.get("name")
            or (
                origin_data
                .get("pluginData", {})
                .get("details", {})
                .get("name")
            )
            or "未知"
        )

        destination = (
            destination_code.get("iata")
            or destination_code.get("icao")
            or destination_data.get("name")
            or (
                destination_data
                .get("pluginData", {})
                .get("details", {})
                .get("name")
            )
            or "未知"
        )

        # --------------------------------------------------------
        # Identification
        # --------------------------------------------------------

        ident = (
            details.get(
                "identification"
            )
            or {}
        )

        number_data = (
            ident.get(
                "number"
            )
            or {}
        )

        callsign_data = (
            ident.get(
                "callsign"
            )
            or {}
        )

        f_num = (
            number_data.get("default")
            or number_data.get("display")
            or callsign_data.get("default")
            or callsign_data.get("display")
            or "未知"
        )

        callsign = (
            callsign_data.get("default")
            or callsign_data.get("display")
            or ""
        )

        # --------------------------------------------------------
        # Aircraft
        # --------------------------------------------------------

        aircraft = (
            details.get(
                "aircraft"
            )
            or {}
        )

        f_reg = (
            aircraft.get(
                "registration"
            )
            or aircraft.get(
                "registrationNumber"
            )
            or "未知"
        )

        aircraft_model = (
            aircraft.get(
                "model"
            )
            or {}
        )

        if isinstance(
            aircraft_model,
            dict
        ):

            ac_code = (
                aircraft_model.get("code")
                or aircraft_model.get("text")
                or aircraft_model.get("name")
                or "未知"
            )

        else:

            ac_code = str(
                aircraft_model
            )

        # --------------------------------------------------------
        # Time
        # --------------------------------------------------------

        time_data = (
            details.get(
                "time"
            )
            or {}
        )

        scheduled = (
            time_data.get(
                "scheduled"
            )
            or {}
        )

        estimated = (
            time_data.get(
                "estimated"
            )
            or {}
        )

        real = (
            time_data.get(
                "real"
            )
            or {}
        )

        std_ts = scheduled.get(
            "departure"
        )

        etd_ts = estimated.get(
            "departure"
        )

        atd_ts = real.get(
            "departure"
        )

        # 優先：
        # estimated -> scheduled -> real

        dep_ts = (
            etd_ts
            or std_ts
            or atd_ts
        )

        dep_full = (
            format_full_datetime(
                dep_ts
            )
        )

        # --------------------------------------------------------
        # Image
        # --------------------------------------------------------

        image_url = None

        images = (
            aircraft.get(
                "images"
            )
            or {}
        )

        large_images = (
            images.get("large")
            or images.get("medium")
            or []
        )

        if (
            isinstance(
                large_images,
                list
            )
            and large_images
        ):

            first_image = (
                large_images[0]
            )

            if isinstance(
                first_image,
                dict
            ):

                image_url = (
                    first_image.get(
                        "src"
                    )
                )

        # --------------------------------------------------------
        # PlaneSpotters fallback
        # --------------------------------------------------------

        if (
            not image_url
            and f_reg != "未知"
        ):

            image_url = (
                fetch_planespotters_image(
                    f_reg
                )
            )

        return {

            "origin": origin,

            "destination": destination,

            "f_num": f_num,

            "f_reg": f_reg,

            "callsign": callsign,

            "ac_code": ac_code,

            "dep_ts": dep_ts,

            "dep_time": dep_full,

            "image_url": image_url,
        }

    except Exception:

        return None


# ============================================================
# 8. 從 Flight object 取得所有可能識別資料
# ============================================================

def extract_flight_values(
    flight
) -> dict:

    values = {
        "registration": set(),
        "number": set(),
        "callsign": set(),
        "all": set(),
    }

    if not flight:

        return values

    possible_registration = [
        "registration",
        "aircraft_registration",
        "aircraftRegistration",
        "reg",
    ]

    possible_number = [
        "number",
        "flight_number",
        "flightNumber",
    ]

    possible_callsign = [
        "callsign",
        "callSign",
    ]

    for attr in possible_registration:

        try:

            value = getattr(
                flight,
                attr,
                None
            )

        except Exception:

            value = None

        value = clean_value(
            value
        )

        if value:

            values[
                "registration"
            ].add(value)

            values[
                "all"
            ].add(value)

    for attr in possible_number:

        try:

            value = getattr(
                flight,
                attr,
                None
            )

        except Exception:

            value = None

        value = clean_value(
            value
        )

        if value:

            values[
                "number"
            ].add(value)

            values[
                "all"
            ].add(value)

    for attr in possible_callsign:

        try:

            value = getattr(
                flight,
                attr,
                None
            )

        except Exception:

            value = None

        value = clean_value(
            value
        )

        if value:

            values[
                "callsign"
            ].add(value)

            values[
                "all"
            ].add(value)

    return values


# ============================================================
# 9. 建立高速航班索引
# ============================================================

def build_flight_index(
    all_flights: list
) -> dict:

    """
    建立多重 Hash Map。

    不再只保存一架。
    同一 flight number 可能同時存在多個結果，
    因此使用 list。
    """

    index = {

        "registration": {},
        "number": {},
        "callsign": {},
        "normalized": {},
    }

    for flight in all_flights:

        values = (
            extract_flight_values(
                flight
            )
        )

        # --------------------------------------------------------
        # Registration
        # --------------------------------------------------------

        for value in values[
            "registration"
        ]:

            index[
                "registration"
            ].setdefault(
                value,
                []
            ).append(
                flight
            )

            normalized = (
                normalize_target(
                    value
                )
            )

            if normalized:

                index[
                    "normalized"
                ].setdefault(
                    normalized,
                    []
                ).append(
                    flight
                )

        # --------------------------------------------------------
        # Flight number
        # --------------------------------------------------------

        for value in values[
            "number"
        ]:

            index[
                "number"
            ].setdefault(
                value,
                []
            ).append(
                flight
            )

            normalized = (
                normalize_target(
                    value
                )
            )

            if normalized:

                index[
                    "normalized"
                ].setdefault(
                    normalized,
                    []
                ).append(
                    flight
                )

        # --------------------------------------------------------
        # Callsign
        # --------------------------------------------------------

        for value in values[
            "callsign"
        ]:

            index[
                "callsign"
            ].setdefault(
                value,
                []
            ).append(
                flight
            )

            normalized = (
                normalize_target(
                    value
                )
            )

            if normalized:

                index[
                    "normalized"
                ].setdefault(
                    normalized,
                    []
                ).append(
                    flight
                )

    return index


# ============================================================
# 10. 精準搜尋單一目標
# ============================================================

def find_target_in_index(
    target: str,
    flight_index: dict
):

    target_upper = (
        target
        .upper()
        .strip()
    )

    target_normalized = (
        normalize_target(
            target_upper
        )
    )

    # --------------------------------------------------------
    # Registration
    # --------------------------------------------------------

    candidates = (
        flight_index[
            "registration"
        ].get(
            target_upper,
            []
        )
    )

    if candidates:

        return (
            candidates[0],
            "EXACT_REGISTRATION"
        )

    # --------------------------------------------------------
    # Flight Number
    # --------------------------------------------------------

    candidates = (
        flight_index[
            "number"
        ].get(
            target_upper,
            []
        )
    )

    if candidates:

        return (
            candidates[0],
            "EXACT_FLIGHT_NUMBER"
        )

    # --------------------------------------------------------
    # Callsign
    # --------------------------------------------------------

    candidates = (
        flight_index[
            "callsign"
        ].get(
            target_upper,
            []
        )
    )

    if candidates:

        return (
            candidates[0],
            "EXACT_CALLSIGN"
        )

    # --------------------------------------------------------
    # Normalized
    # --------------------------------------------------------

    if target_normalized:

        candidates = (
            flight_index[
                "normalized"
            ].get(
                target_normalized,
                []
            )
        )

        if candidates:

            return (
                candidates[0],
                "NORMALIZED_MATCH"
            )

    return None, None


# ============================================================
# 11. 建立結果
# ============================================================

def build_result(
    target_raw: str,
    flight,
    details: dict,
    source: str,
    match_type: str
) -> dict | None:

    if not details:

        return None

    orig = (
        details.get(
            "origin",
            "未知"
        )
    )

    dest = (
        details.get(
            "destination",
            "未知"
        )
    )

    is_tw_origin = (
        check_is_taiwan(
            orig
        )
    )

    dep_ts = details.get(
        "dep_ts"
    )

    current_ts = int(
        time.time()
    )

    is_future = bool(
        dep_ts
        and int(dep_ts)
        > current_ts
    )

    f_num = (
        details.get(
            "f_num"
        )
        or "未知"
    )

    if f_num == "未知":

        f_num = (
            getattr(
                flight,
                "number",
                ""
            )
            or getattr(
                flight,
                "callsign",
                ""
            )
            or target_raw
        )

    f_reg = (
        details.get(
            "f_reg"
        )
        or "未知"
    )

    if f_reg == "未知":

        f_reg = (
            getattr(
                flight,
                "registration",
                ""
            )
            or target_raw
        )

    callsign = (
        details.get(
            "callsign"
        )
        or getattr(
            flight,
            "callsign",
            ""
        )
        or ""
    )

    return {

        "target": target_raw,

        "f_num": f_num,

        "f_reg": f_reg,

        "callsign": callsign,

        "ac_code": details.get(
            "ac_code",
            "未知"
        ),

        "route": (
            f"{orig} ➔ {dest}"
        ),

        "dep_time": details.get(
            "dep_time",
            "未知"
        ),

        "dep_ts": dep_ts,

        "is_taiwan_origin": (
            is_tw_origin
        ),

        "is_future": is_future,

        "image_url": details.get(
            "image_url"
        ),

        "source": source,

        "match_type": match_type,
    }


# ============================================================
# 12. Web Search 結果驗證
# ============================================================

def search_item_matches_target(
    target: str,
    item: dict
) -> tuple[bool, str]:

    target_norm = (
        normalize_target(
            target
        )
    )

    exact_fields = []
    normalized_fields = []

    def collect(
        value,
        field_name
    ):

        if value is None:

            return

        if isinstance(
            value,
            dict
        ):

            for v in value.values():

                collect(
                    v,
                    field_name
                )

            return

        if isinstance(
            value,
            list
        ):

            for v in value:

                collect(
                    v,
                    field_name
                )

            return

        text = (
            str(value)
            .upper()
            .strip()
        )

        if not text:

            return

        exact_fields.append(
            (
                field_name,
                text
            )
        )

        normalized_fields.append(
            (
                field_name,
                normalize_target(
                    text
                )
            )
        )

    # --------------------------------------------------------
    # Search result 可能存在的欄位
    # --------------------------------------------------------

    collect(
        item.get(
            "registration"
        ),
        "registration"
    )

    collect(
        item.get(
            "aircraft_registration"
        ),
        "aircraft_registration"
    )

    collect(
        item.get(
            "aircraft"
        ),
        "aircraft"
    )

    collect(
        item.get(
            "identification"
        ),
        "identification"
    )

    collect(
        item.get(
            "number"
        ),
        "number"
    )

    collect(
        item.get(
            "callsign"
        ),
        "callsign"
    )

    collect(
        item.get(
            "title"
        ),
        "title"
    )

    collect(
        item.get(
            "label"
        ),
        "label"
    )

    # --------------------------------------------------------
    # 精準
    # --------------------------------------------------------

    for field, value in exact_fields:

        if value == target.upper():

            return (
                True,
                f"WEB_EXACT_{field.upper()}"
            )

    # --------------------------------------------------------
    # Normalized
    # --------------------------------------------------------

    if target_norm:

        for field, value in normalized_fields:

            if (
                value
                and value == target_norm
            ):

                return (
                    True,
                    f"WEB_NORMALIZED_{field.upper()}"
                )

    return False, ""


# ============================================================
# 13. 驗證 Details
# ============================================================

def details_match_target(
    target: str,
    details: dict
) -> tuple[bool, str]:

    if not details:

        return False, ""

    target_upper = (
        target.upper().strip()
    )

    target_norm = (
        normalize_target(
            target_upper
        )
    )

    values = [

        (
            "registration",
            details.get(
                "f_reg",
                ""
            )
        ),

        (
            "flight_number",
            details.get(
                "f_num",
                ""
            )
        ),

        (
            "callsign",
            details.get(
                "callsign",
                ""
            )
        ),
    ]

    for field, value in values:

        if not value:

            continue

        value_upper = (
            str(value)
            .upper()
            .strip()
        )

        if (
            value_upper
            == target_upper
        ):

            return (
                True,
                f"WEB_DETAILS_EXACT_{field.upper()}"
            )

        if (
            normalize_target(
                value_upper
            )
            == target_norm
        ):

            return (
                True,
                f"WEB_DETAILS_NORMALIZED_{field.upper()}"
            )

    return False, ""


# ============================================================
# 14. Web API 補查
# ============================================================

def web_search_target(
    target_raw: str,
    flight_map_by_id: dict,
    fr_api_inst
) -> dict | None:

    """
    第二階段搜尋。

    與舊版最大的差異：

    1. 不限制 type == live
    2. 搜尋結果本身先驗證
    3. 再取得 details
    4. details 再驗證一次
    5. 可以檢查多個候選
    """

    target_raw = (
        target_raw
        .upper()
        .strip()
    )

    if not target_raw:

        return None

    try:

        encoded_target = quote(
            target_raw,
            safe=""
        )

        search_url = (
            "https://www.flightradar24.com/"
            "v1/search/web/find"
            f"?query={encoded_target}"
        )

        res = http_session.get(
            search_url,
            headers=get_headers(),
            timeout=5
        )

        if res.status_code != 200:

            return None

        data = res.json()

        results = (
            data.get(
                "results",
                []
            )
        )

        if not isinstance(
            results,
            list
        ):

            return None

        if not results:

            return None

        candidates = []

        # ====================================================
        # 第一輪：搜尋結果本身判斷
        # ====================================================

        for item in results:

            if not isinstance(
                item,
                dict
            ):

                continue

            live_id = str(
                item.get(
                    "id",
                    ""
                )
            ).strip()

            if not live_id:

                continue

            matched, match_type = (
                search_item_matches_target(
                    target_raw,
                    item
                )
            )

            # ------------------------------------------------
            # 精準匹配優先
            # ------------------------------------------------

            if matched:

                candidates.append(
                    (
                        0,
                        live_id,
                        item,
                        match_type
                    )
                )

                continue

            # ------------------------------------------------
            # 如果搜尋 API 沒提供識別欄位，
            # 但 type 是 live，也保留候選。
            #
            # 這是為了處理 FR24 搜尋 API
            # 回傳欄位不完整的情況。
            # ------------------------------------------------

            item_type = str(
                item.get(
                    "type",
                    ""
                )
            ).lower()

            if item_type == "live":

                candidates.append(
                    (
                        1,
                        live_id,
                        item,
                        "WEB_LIVE_CANDIDATE"
                    )
                )

        if not candidates:

            # =================================================
            # 第二種方式：
            # 即使搜尋結果沒有 type=live，
            # 只要有 ID 也保留前幾個候選
            # =================================================

            for item in results:

                if not isinstance(
                    item,
                    dict
                ):

                    continue

                live_id = str(
                    item.get(
                        "id",
                        ""
                    )
                ).strip()

                if not live_id:

                    continue

                candidates.append(
                    (
                        2,
                        live_id,
                        item,
                        "WEB_ID_CANDIDATE"
                    )
                )

                if len(candidates) >= 5:

                    break

        # 最多驗證 5 個候選
        candidates.sort(
            key=lambda x: x[0]
        )

        candidates = candidates[:5]

        # ====================================================
        # 逐個 candidate 取得 details
        # ====================================================

        for (
            priority,
            live_id,
            item,
            search_match_type
        ) in candidates:

            target_obj = (
                flight_map_by_id.get(
                    live_id
                )
                or live_id
            )

            details = (
                fetch_direct_clickhandler(
                    fr_api_inst,
                    target_obj
                )
            )

            if not details:

                continue

            # =================================================
            # Details 驗證
            # =================================================

            matched, detail_match_type = (
                details_match_target(
                    target_raw,
                    details
                )
            )

            if matched:

                return build_result(
                    target_raw,
                    target_obj,
                    details,
                    "🔍 FR24 Web API 補查",
                    detail_match_type
                )

            # =================================================
            # 如果搜尋結果已經是精準匹配，
            # 而 details 沒有識別欄位，
            # 仍接受。
            #
            # 這是為了避免 FR24 details API
            # 某些資料欄位缺失造成漏抓。
            # =================================================

            if (
                priority == 0
                and search_match_type.startswith(
                    "WEB_EXACT"
                )
            ):

                return build_result(
                    target_raw,
                    target_obj,
                    details,
                    "🔍 FR24 Web API 搜尋結果精準匹配",
                    search_match_type
                )

        return None

    except Exception:

        return None


# ============================================================
# 15. Discord
# ============================================================

def send_discord_webhook(
    taiwan_flights: list
):

    if not DISCORD_WEBHOOK_URL:

        print(
            "⚠️ 未設定 Discord Webhook URL，"
            "跳過推播。"
        )

        return

    embeds = []

    for f in taiwan_flights:

        embed = {

            "title": (
                f"🚨 彩繪機台灣起飛警報："
                f"{f['f_num']}"
            ),

            "color": 3447003,

            "fields": [

                {
                    "name": "機身註冊號",
                    "value": (
                        f"`{f['f_reg']}` "
                        f"({f['ac_code']})"
                    ),
                    "inline": True,
                },

                {
                    "name": "航線狀況",
                    "value": (
                        f"📍 **{f['route']}**"
                    ),
                    "inline": True,
                },

                {
                    "name": "預計起飛 (UTC+8)",
                    "value": (
                        f"🕒 `{f['dep_time']}`"
                    ),
                    "inline": False,
                },

                {
                    "name": "搜尋方式",
                    "value": (
                        f"`{f.get('match_type', 'UNKNOWN')}`"
                    ),
                    "inline": False,
                },
            ],

            "footer": {

                "text": (
                    "FR24 智慧航班監測系統"
                    " • "
                    f"來源：{f['source']}"
                )
            },
        }

        if f.get(
            "image_url"
        ):

            embed["image"] = {
                "url": f["image_url"]
            }

        embeds.append(
            embed
        )

    # Discord 一次最多 10 embeds

    for i in range(
        0,
        len(embeds),
        10
    ):

        batch = embeds[
            i:i + 10
        ]

        payload = {
            "embeds": batch
        }

        try:

            res = http_session.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=5
            )

            if res.status_code in [
                200,
                204
            ]:

                print(
                    f"✅ 成功推播第 "
                    f"{i // 10 + 1} 批 "
                    f"共 {len(batch)} 架台灣起飛航班！"
                )

            else:

                print(
                    f"❌ Discord 發送失敗，"
                    f"HTTP 狀態碼："
                    f"{res.status_code}"
                )

        except Exception as e:

            print(
                f"❌ Discord 發送異常：{e}"
            )


# ============================================================
# 16. 第一階段：高速掃描
# ============================================================

def fast_scan(
    TARGETS: list,
    fr_api_inst
):

    print(
        "\n⚡ 第一階段："
        "高速一次性掃描開始..."
    )

    start_time = time.time()

    # ========================================================
    # 只呼叫一次 get_flights()
    # ========================================================

    try:

        snapshot = (
            fr_api_inst.get_flights()
            or []
        )

    except Exception as e:

        print(
            f"❌ get_flights() 失敗：{e}"
        )

        snapshot = []

    print(
        f"📡 目前取得 "
        f"{len(snapshot)} 架即時航班"
    )

    # ========================================================
    # ID map
    # ========================================================

    flight_map_by_id = {

        str(
            getattr(
                f,
                "id",
                ""
            )
        ): f

        for f in snapshot

        if getattr(
            f,
            "id",
            ""
        )
    }

    # ========================================================
    # 建立索引
    # ========================================================

    index_start = time.time()

    flight_index = (
        build_flight_index(
            snapshot
        )
    )

    print(
        f"🧠 航班索引建立完成 "
        f"({time.time() - index_start:.2f} 秒)"
    )

    # ========================================================
    # 查詢 targets
    # ========================================================

    matched_dict = {}

    unmatched_targets = []

    for target in TARGETS:

        flight, match_type = (
            find_target_in_index(
                target,
                flight_index
            )
        )

        if not flight:

            unmatched_targets.append(
                target
            )

            continue

        # ====================================================
        # 取得詳細資料
        # ====================================================

        details = (
            fetch_direct_clickhandler(
                fr_api_inst,
                flight
            )
        )

        if not details:

            # 詳細資料失敗，
            # 不直接判定不存在。
            unmatched_targets.append(
                target
            )

            continue

        # ====================================================
        # 再驗證一次
        # ====================================================

        matched, detail_type = (
            details_match_target(
                target,
                details
            )
        )

        # 如果 details 沒有足夠識別資料，
        # 但第一階段 index 已經精準命中，
        # 還是保留。
        if (
            not matched
            and match_type
            not in [
                "EXACT_REGISTRATION",
                "EXACT_FLIGHT_NUMBER",
                "EXACT_CALLSIGN",
                "NORMALIZED_MATCH",
            ]
        ):

            unmatched_targets.append(
                target
            )

            continue

        result = build_result(
            target,
            flight,
            details,
            "📡 FR24 直播廣播",
            (
                detail_type
                if matched
                else match_type
            )
        )

        if result:

            matched_dict[
                target
            ] = result

            print(
                f"  └─ 🟢 {target}"
                f" -> "
                f"{result['f_num']} "
                f"({result['f_reg']}) "
                f"[{result['match_type']}]"
            )

        else:

            unmatched_targets.append(
                target
            )

    elapsed = (
        time.time()
        - start_time
    )

    print(
        f"\n⚡ 第一階段完成："
        f"{elapsed:.2f} 秒"
    )

    print(
        f"   🟢 找到："
        f"{len(matched_dict)}"
    )

    print(
        f"   ❓ 待補查："
        f"{len(unmatched_targets)}"
    )

    return (
        matched_dict,
        unmatched_targets,
        flight_map_by_id
    )


# ============================================================
# 17. 第二階段：高速 Web API 補查
# ============================================================

def deep_scan_unmatched(
    unmatched_targets: list,
    flight_map_by_id: dict,
    fr_api_inst
):

    if not unmatched_targets:

        return {}

    print(
        "\n🔍 第二階段："
        f"開始補查 "
        f"{len(unmatched_targets)} "
        f"架未找到目標..."
    )

    start_time = time.time()

    results = {}

    # ========================================================
    # 提高並行數
    # ========================================================

    max_workers = min(
        12,
        max(
            1,
            len(unmatched_targets)
        )
    )

    print(
        f"🚀 開啟 "
        f"{max_workers} "
        f"個 Web API 補查線程"
    )

    # ========================================================
    # 平行搜尋
    # ========================================================

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_to_target = {

            executor.submit(
                web_search_target,
                target,
                flight_map_by_id,
                fr_api_inst
            ): target

            for target
            in unmatched_targets
        }

        for future in as_completed(
            future_to_target
        ):

            target = (
                future_to_target[
                    future
                ]
            )

            try:

                result = (
                    future.result()
                )

                if result:

                    results[
                        target
                    ] = result

                    print(
                        f"  └─ 🟢 "
                        f"[補查成功] "
                        f"{target}"
                        f" -> "
                        f"{result['f_num']} "
                        f"({result['f_reg']}) "
                        f"[{result['match_type']}]"
                    )

                else:

                    print(
                        f"  └─ ⚪ "
                        f"[無結果] "
                        f"{target}"
                    )

            except Exception as e:

                print(
                    f"  └─ ❌ "
                    f"[補查錯誤] "
                    f"{target}: {e}"
                )

    elapsed = (
        time.time()
        - start_time
    )

    print(
        f"\n🔍 第二階段完成："
        f"{elapsed:.2f} 秒"
    )

    print(
        f"   🟢 補查成功："
        f"{len(results)}"
    )

    print(
        f"   ❌ 仍未找到："
        f"{len(unmatched_targets) - len(results)}"
    )

    return results


# ============================================================
# 18. 最終篩選：台灣起飛
# ============================================================

def filter_taiwan_departures(
    matched_dict: dict,
    minutes_ahead: int = 10
):

    now_ts = int(
        time.time()
    )

    limit_ts = (
        now_ts
        + minutes_ahead * 60
    )

    taiwan_departures = []

    for f in matched_dict.values():

        if not f.get(
            "is_taiwan_origin"
        ):

            continue

        dep_ts = f.get(
            "dep_ts"
        )

        if not dep_ts:

            continue

        try:

            dep_ts = int(
                dep_ts
            )

        except Exception:

            continue

        if (
            now_ts
            <= dep_ts
            < limit_ts
        ):

            taiwan_departures.append(
                f
            )

    taiwan_departures.sort(
        key=lambda x: (
            x.get(
                "dep_ts"
            )
            or 0
        )
    )

    return taiwan_departures


# ============================================================
# 19. 主程式
# ============================================================

def main():

    program_start = time.time()

    print(
        "=" * 70
    )

    print(
        "✈️ FR24 高速智慧航班監測系統"
    )

    print(
        "=" * 70
    )

    # ========================================================
    # 沒有 targets
    # ========================================================

    if not TARGETS:

        print(
            "🛑 沒有偵測到任何監控目標，"
            "程式結束。"
        )

        return

    print(
        f"🎯 監控目標："
        f"{len(TARGETS)} 架"
    )

    # ========================================================
    # 建立 FR24
    # ========================================================

    try:

        fr_api_inst = (
            FlightRadar24API()
        )

    except Exception as e:

        print(
            f"❌ 無法建立 "
            f"FlightRadar24API："
            f"{e}"
        )

        return

    # ========================================================
    # 第一階段
    # ========================================================

    (
        matched_dict,
        unmatched_targets,
        flight_map_by_id
    ) = fast_scan(
        TARGETS,
        fr_api_inst
    )

    first_stage_count = (
        len(matched_dict)
    )

    # ========================================================
    # 第二階段
    # ========================================================

    deep_count = 0

    if unmatched_targets:

        deep_results = (
            deep_scan_unmatched(
                unmatched_targets,
                flight_map_by_id,
                fr_api_inst
            )
        )

        deep_count = (
            len(deep_results)
        )

        matched_dict.update(
            deep_results
        )

    # ========================================================
    # 最終統計
    # ========================================================

    final_matched_count = (
        len(matched_dict)
    )

    final_unmatched = (
        len(TARGETS)
        - final_matched_count
    )

    # ========================================================
    # 台灣起飛
    # ========================================================

    taiwan_departures = (
        filter_taiwan_departures(
            matched_dict,
            minutes_ahead=10
        )
    )

    total_elapsed = (
        time.time()
        - program_start
    )

    # ========================================================
    # 總結
    # ========================================================

    print(
        "\n"
        + "=" * 70
    )

    print(
        "📊 掃描結果總結"
    )

    print(
        "=" * 70
    )

    print(
        f" • 🎯 監控目標數："
        f"{len(TARGETS)} 架"
    )

    print(
        f" • 📡 第一階段直接找到："
        f"{first_stage_count} 架"
    )

    print(
        f" • 🔍 第二階段 Web API 補查："
        f"{deep_count} 架"
    )

    print(
        f" • 🟢 最終成功定位："
        f"{final_matched_count} 架"
    )

    print(
        f" • ❌ 最終未找到："
        f"{final_unmatched} 架"
    )

    print(
        f" • 🛫 未來 10 分鐘內"
        f"自台灣起飛："
        f"{len(taiwan_departures)} 架"
    )

    print(
        f" • ⏱️ 本次總耗時："
        f"{total_elapsed:.2f} 秒"
    )

    print(
        "=" * 70
    )

    # ========================================================
    # 如果達到預期 44 架
    # ========================================================

    if final_matched_count >= 44:

        print(
            "\n🎉 已成功定位至少 44 架監控目標！"
        )

    else:

        print(
            "\n⚠️ 目前仍未達到預期的 44 架。"
        )

        print(
            "   建議查看下面的未找到清單。"
        )

    # ========================================================
    # 顯示台灣起飛航班
    # ========================================================

    if taiwan_departures:

        print(
            "\n🚨 發現即將自台灣起飛的目標："
        )

        for f in taiwan_departures:

            print(
                f"  ✈️ {f['f_num']}"
                f" | {f['f_reg']}"
                f" | {f['ac_code']}"
                f" | {f['route']}"
                f" | {f['dep_time']}"
                f" | {f['match_type']}"
            )

        # ----------------------------------------------------
        # Discord
        # ----------------------------------------------------

        send_discord_webhook(
            taiwan_departures
        )

    else:

        print(
            "\nℹ️ 目前沒有目標班機"
            "將在未來 10 分鐘內"
            "自台灣起飛。"
        )

    # ========================================================
    # 未找到清單
    # ========================================================

    actually_unmatched = [

        t

        for t in TARGETS

        if t not in matched_dict
    ]

    if actually_unmatched:

        print(
            "\n❌ 以下目標目前沒有取得資料："
        )

        for target in actually_unmatched:

            print(
                f"   - {target}"
            )

    else:

        print(
            "\n🎉 所有監控目標都已成功定位！"
        )

    # ========================================================
    # 完成
    # ========================================================

    print(
        "\n✅ 程式執行完成。"
    )


# ============================================================
# 20. Entry Point
# ============================================================

if __name__ == "__main__":

    main()
