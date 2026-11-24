import requests
import gzip
import json
import os
import logging
import uuid
import time
import shutil
import random
import re
import xml.etree.ElementTree as ET
import urllib3
from io import BytesIO
from datetime import datetime
from urllib.parse import unquote, urlparse, urlunparse
from bs4 import BeautifulSoup

# Disable the InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
OUTPUT_DIR = "playlists"
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
REQUEST_TIMEOUT = 30 

REGION_MAP = {
    'us': 'United States', 'gb': 'United Kingdom', 'ca': 'Canada',
    'de': 'Germany', 'at': 'Austria', 'ch': 'Switzerland',
    'es': 'Spain', 'fr': 'France', 'it': 'Italy', 'br': 'Brazil',
    'mx': 'Mexico', 'ar': 'Argentina', 'cl': 'Chile', 'co': 'Colombia',
    'pe': 'Peru', 'se': 'Sweden', 'no': 'Norway', 'dk': 'Denmark',
    'in': 'India', 'jp': 'Japan', 'kr': 'South Korea', 'au': 'Australia'
}

TOP_REGIONS = ['United States', 'Canada', 'United Kingdom']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def snapshot_files(prefixes):
    """Return the set of existing filenames in OUTPUT_DIR matching any prefix."""
    existing = set()
    for fname in os.listdir(OUTPUT_DIR):
        if any(fname.startswith(p) for p in prefixes):
            existing.add(fname)
    return existing

def remove_files(filenames):
    for fname in filenames:
        path = os.path.join(OUTPUT_DIR, fname)
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)
        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")

def fetch_url(url, is_json=True, is_gzipped=False, headers=None, stream=False, retries=3):
    headers = headers or {'User-Agent': USER_AGENT}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream)
            if response.status_code == 429:
                time.sleep((i + 1) * 10 + random.uniform(0, 5))
                continue
            response.raise_for_status()
            content = response.content
            if is_gzipped:
                try:
                    with gzip.GzipFile(fileobj=BytesIO(content), mode='rb') as f:
                        content = f.read()
                    content = content.decode('utf-8')
                except:
                    content = content.decode('utf-8')
            else:
                content = content.decode('utf-8')
            return json.loads(content) if is_json else content
        except Exception as e:
            logger.warning(f"Fetch failed (attempt {i+1}): {e}")
            if i < retries - 1: time.sleep(5)
    return None

def write_m3u_file(filename, content):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def format_extinf(channel_id, tvg_id, tvg_chno, tvg_name, tvg_logo, group_title, display_name):
    chno_str = str(tvg_chno) if tvg_chno and str(tvg_chno).isdigit() else ""
    return (f'#EXTINF:-1 channel-id="{channel_id}" tvg-id="{tvg_id}" tvg-chno="{chno_str}" '
            f'tvg-name="{tvg_name.replace(chr(34), chr(39))}" tvg-logo="{tvg_logo}" '
            f'group-title="{group_title.replace(chr(34), chr(39))}",{display_name.replace(",", "")}\n')

# --- Standard Services ---

def get_anonymous_token(region: str = 'us') -> str | None:
    headers = {
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
        'X-Plex-Product': 'Plex Web',
        'X-Plex-Version': '4.150.0',
        'X-Plex-Client-Identifier': str(uuid.uuid4()).replace('-', ''),
        'X-Plex-Platform': 'Web',
    }
    x_forward_ips = {'us': '76.81.9.69'}
    if region in x_forward_ips: headers['X-Forwarded-For'] = x_forward_ips[region]
    params = {'X-Plex-Product': 'Plex Web', 'X-Plex-Client-Identifier': headers['X-Plex-Client-Identifier']}
    try:
        resp = requests.post('https://clients.plex.tv/api/v2/users/anonymous', headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get('authToken')
    except: return None

def generate_pluto_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'regions' not in data: return

    for region in list(data['regions'].keys()) + ['all']:
        is_all = region == 'all'
        output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/{region}.xml.gz"\n']
        channels = {}

        if is_all:
            for r_code, r_data in data['regions'].items():
                country_name = REGION_MAP.get(r_code.lower(), r_code.upper())
                for c_id, c_info in r_data.get('channels', {}).items():
                    channels[f"{c_id}-{r_code}"] = {
                        **c_info,
                        'original_id': c_id,
                        'country_group': country_name,
                        'service_group': c_info.get('group', 'Other')
                    }
        else:
            region_data = data['regions'].get(region, {}).get('channels', {})
            country_name = REGION_MAP.get(region.lower(), region.upper())
            for c_id, c_info in region_data.items():
                channels[c_id] = {
                    **c_info,
                    'original_id': c_id,
                    'country_group': country_name,
                    'service_group': c_info.get('group', 'Other')
                }

        sorted_channels = sorted(
            channels.items(),
            key=lambda x: (0 if x[1]['country_group'] in TOP_REGIONS else 1, x[1].get('name', ''))
        )

        for c_id, ch in sorted_channels:
            group_title = ch['country_group'] if is_all else ch['service_group']

            output_lines.extend([
                format_extinf(
                    c_id,
                    ch['original_id'],
                    ch.get('chno'),
                    ch['name'],
                    ch['logo'],
                    group_title,
                    ch['name']
                ),
                f"https://jmp2.uk/plu-{ch['original_id']}.m3u8\n"
            ])

        write_m3u_file(f"plutotv_{region}.m3u", "".join(output_lines))

def generate_plex_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Plex/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'channels' not in data: return
    found_regions = set()
    for ch in data['channels'].values(): found_regions.update(ch.get('regions', []))
    for region in list(found_regions) + ['all']:
        token = get_anonymous_token(region if region != 'all' else 'us')
        if not token: continue
        output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/Plex/{region}.xml.gz"\n']
        channel_list = []
        for c_id, ch in data['channels'].items():
            if region == 'all' or region in ch.get('regions', []):
                group = REGION_MAP.get(region.lower(), region.upper()) if region != 'all' else REGION_MAP.get(ch.get('regions', [''])[0].lower(), ch.get('regions', ['Other'])[0].upper())
                channel_list.append((group, ch['name'].lower(), format_extinf(c_id, c_id, ch.get('chno'), ch['name'], ch.get('logo', ''), group, ch['name']), f"https://epg.provider.plex.tv/library/parts/{c_id}/?X-Plex-Token={token}\n"))
        if channel_list:
            channel_list.sort(key=lambda x: (0 if x[0] in TOP_REGIONS else 1, x[1]))
            for _, _, extinf, url in channel_list: output_lines.extend([extinf, url])
            write_m3u_file(f"plex_{region}.m3u", "".join(output_lines))

def generate_samsungtvplus_m3u():
    data = fetch_url('https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/SamsungTVPlus/.channels.json.gz', is_json=True, is_gzipped=True)
    if not data or 'regions' not in data: return
    slug_template = data.get('slug', '{id}.m3u8')

    for region in list(data['regions'].keys()) + ['all']:
        is_all = region == 'all'
        output_lines = [f'#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/SamsungTVPlus/{region}.xml.gz"\n']
        channels = {}

        if is_all:
            for r_code, r_info in data['regions'].items():
                country_name = REGION_MAP.get(r_code.lower(), r_code.upper())
                for c_id, c_info in r_info.get('channels', {}).items():
                    channels[f"{c_id}-{r_code}"] = {
                        **c_info,
                        'original_id': c_id,
                        'country_group': country_name,
                        'service_group': c_info.get('group', 'Other')
                    }
        else:
            region_data = data['regions'].get(region, {}).get('channels', {})
            country_name = REGION_MAP.get(region.lower(), region.upper())
            for c_id, c_info in region_data.items():
                channels[c_id] = {
                    **c_info,
                    'original_id': c_id,
                    'country_group': country_name,
                    'service_group': c_info.get('group', 'Other')
                }

        sorted_channels = sorted(
            channels.items(),
            key=lambda x: (0 if x[1]['country_group'] in TOP_REGIONS else 1, x[1].get('name', '').lower())
        )

        for c_id, ch in sorted_channels:
            group_title = ch['country_group'] if is_all else ch['service_group']

            output_lines.extend([
                format_extinf(
                    c_id,
                    ch['original_id'],
                    ch.get('chno'),
                    ch['name'],
                    ch['logo'],
                    group_title,
                    ch['name']
                ),
                f"https://jmp2.uk/{slug_template.replace('{id}', ch['original_id'])}\n"
            ])

        write_m3u_file(f"samsungtvplus_{region}.m3u", "".join(output_lines))

def generate_roku_m3u():
    data = fetch_url('https://i.mjh.nz/Roku/.channels.json', is_json=True)
    if not data: return

    # Map granular Roku genre tags to consolidated group names
    ROKU_GROUP_MAP = {
        # News & Weather
        'News': 'News', 'Newsmagazine': 'News', 'Special': 'News', 'Politics': 'News',
        'Weather': 'Weather',

        # Sports (general)
        'Sports': 'Sports', 'Sports Talk': 'Sports', 'Olympics': 'Sports',
        'Action Sports': 'Sports', 'Action': 'Sports',

        # Sports (specific — fold into Sports)
        'Baseball': 'Sports', 'Basketball': 'Sports', 'Football': 'Sports',
        'Soccer': 'Sports', 'Hockey': 'Sports', 'Tennis': 'Sports', 'Golf': 'Sports',
        'Boxing': 'Sports', 'Mixed Martial Arts': 'Sports', 'Martial Arts': 'Sports',
        'Wrestling': 'Sports', 'Rugby': 'Sports', 'Volleyball': 'Sports',
        'Skateboarding': 'Sports', 'Snowboarding': 'Sports', 'Surfing': 'Sports',
        'Cycling': 'Sports', 'Bicycle': 'Sports', 'Bmx Racing': 'Sports',
        'Bullfighting': 'Sports', 'Rodeo': 'Sports', 'Western': 'Sports',
        'Fishing': 'Sports', 'Hunting': 'Sports', 'Outdoors': 'Sports',
        'Boat Racing': 'Sports', 'Drag Racing': 'Sports', 'Motorsports': 'Sports',
        'Motorcycle': 'Sports', 'Motorcycle Racing': 'Sports',
        'Judo': 'Sports', 'Karate': 'Sports', 'Billiards': 'Sports',

        # Auto
        'Auto': 'Auto & Motorsports', 'Auto Racing': 'Auto & Motorsports',

        # Movies
        'Adventure': 'Movies', 'Thriller': 'Movies', 'Suspense': 'Movies',
        'Science Fiction': 'Movies', 'Fantasy': 'Movies', 'Horror': 'Movies',

        # TV / Entertainment
        'Entertainment': 'TV & Entertainment', 'Sitcom': 'TV & Entertainment',
        'Drama': 'TV & Entertainment', 'Soap': 'TV & Entertainment',
        'Talk': 'TV & Entertainment', 'Reality': 'TV & Entertainment',
        'Comedy Drama': 'TV & Entertainment', 'History': 'TV & Entertainment',

        # Comedy
        'Comedy': 'Comedy', 'Romantic Comedy': 'Comedy',

        # Romance
        'Romance': 'Romance',

        # Documentary
        'Documentary': 'Documentary', 'Nature': 'Documentary',

        # Music
        'Music': 'Music',

        # Anime
        'Anime': 'Anime',

        # Gaming & Tech
        'Gaming': 'Gaming & Tech', 'Computers': 'Gaming & Tech',
        'Esports': 'Gaming & Tech',

        # Faith
        'Faith': 'Faith & Family', 'Religious': 'Faith & Family',
        'Family': 'Faith & Family',

        # Health
        'Health': 'Health', 'Medical': 'Health',
    }

    channels = data.get('channels', {})

    group_map = {}
    for c_id, ch in channels.items():
        raw_group = ch['groups'][0] if ch.get('groups') else 'Other'
        group = ROKU_GROUP_MAP.get(raw_group, raw_group)
        group_map.setdefault(group, []).append((c_id, ch))

    output_lines = ['#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml.gz"\n']
    for group in sorted(group_map.keys()):
        for c_id, ch in sorted(group_map[group], key=lambda x: x[1].get('name', '').lower()):
            output_lines.extend([
                format_extinf(c_id, c_id, ch.get('chno'), ch['name'], ch['logo'], group, ch['name']),
                f"https://jmp2.uk/rok-{c_id}.m3u8\n"
            ])

    write_m3u_file("roku_all.m3u", "".join(output_lines))

# --- Tubi Scraping Logic ---

TUBI_LIVE_PAGE_URL = "https://tubitv.com/live"
TUBI_EPG_ENDPOINTS = [
    "https://tubitv.com/oz/epg/programming",
    "https://epg-cdn.production-public.tubi.io/content/epg/programming",
]
TUBI_CONTAINER_CANDIDATES = [
    "https://tubitv.com/oz/containers/linear",
    "https://tubitv.com/oz/containers/tubitv_us_linear",
    "https://tubitv.com/oz/containers?container_id=tubitv_us_linear",
]
TUBI_EPG_BATCH_SIZE = 50
TUBI_GROUP_TITLE = "Fast Tubi"
TUBI_CACHE_MIN_IDS = 100
TUBI_FALLBACK_FILE = "tubi_fallback_ids.json"
TUBI_EPG_URL_TVG = ("https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/"
                    "main/playlists/tubi_epg.xml")

TUBI_ACCOUNT_BASE = "https://account.production-public.tubi.io"
TUBI_SIGNING_KEY_URL = f"{TUBI_ACCOUNT_BASE}/device/anonymous/signing_key"
TUBI_TOKEN_URL = f"{TUBI_ACCOUNT_BASE}/device/anonymous/token"

TUBI_SEED_IDS = [
    555107, 555108, 555109, 555110, 555111, 555112, 555113, 555114, 555115, 555116,
    555117, 555118, 555119, 555120, 555121, 555122, 555123, 555124, 555126, 555127,
    555129, 555130, 555382, 556174, 557344, 557345, 559144, 560215, 571664, 578082,
    578083, 578084, 578085, 578086, 586032, 586034, 586035, 586036, 588301, 591002,
    597678, 603163, 603169, 603170, 603171, 603175, 603178, 603181, 603194, 603904,
    603905, 603906, 603907, 603908, 603909, 603910, 603911, 603914, 603915, 603917,
    603918, 610463, 610464, 610465, 610466, 610467, 610468, 610469, 610470, 610471,
    610472, 613683, 613695, 613758, 613759, 613761, 613762, 613763, 613765, 618762,
    618763, 618764, 618765, 618766, 618767, 618768, 618769, 623575, 628892, 628893,
    628896, 629323, 641290, 641492, 641496, 644781, 644786, 644787, 644788, 644789,
    646485, 646488, 650208, 650241, 650246, 650251, 650252, 650666, 653199, 653200,
    653208, 656574, 656575, 658746, 658748, 660350, 670587, 670602, 670603, 670604,
    671073, 671083, 673411, 673498, 673499, 673500, 677011, 677785, 677790, 680705,
    682057, 682059, 682634, 684164, 684165, 684167, 684170, 685306, 685307, 685308,
    685318, 687448, 687449, 687450, 687451, 687452, 687453, 687454, 687455, 687458,
    687459, 687460, 687468, 687470, 687474, 688740, 688992, 689270, 689435, 689437,
    689473, 691129, 692051, 692057, 692073, 692074, 692086, 692087, 692090, 692114,
    692262, 694174, 700406, 700407, 700412, 700414, 700415, 700418, 702891, 702892,
    711410, 715938, 715939, 715942, 715945, 715946, 715947, 715948, 715949, 715950,
    715951, 715952, 721794, 721975, 724209, 724210, 400000000, 400000006, 400000008,
    400000011, 400000012, 400000024, 400000028, 400000030, 400000031, 400000033, 400000045,
    400000046, 400000048, 400000055, 400000056, 400000059, 400000062, 400000063, 400000064,
    400000065, 400000066, 400000067, 400000068, 400000069, 400000070, 400000071, 400000073,
    400000074, 400000083, 400000085, 400000086, 400000087, 400000088, 400000089, 400000090,
    400000091, 400000092, 400000093, 400000094, 400000095, 400000096, 400000097, 400000098,
    400000099, 400000103, 400000104, 400000105, 400000106, 400000108, 400000111, 400000112,
    400000116, 400000117, 400000119, 400000121, 400000155, 400000164, 400000169, 400000179,
    400000195, 400000196, 400000247, 400000249, 400000251, 400000285, 400000286, 400000287,
    400000288, 400000289, 400000290, 400000291, 400000292, 400000293, 400000294, 400000296,
    400000299, 400000300, 400000301, 400000302, 400000303, 400000304,
]


def _tubi_get_anonymous_token():
    import hashlib, hmac, base64
    device_id = str(uuid.uuid4())
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(device_id.encode()).digest()
    ).decode().rstrip("=")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://tubitv.com",
        "Referer": "https://tubitv.com/",
    }
    try:
        r = requests.post(
            TUBI_SIGNING_KEY_URL,
            json={"device_id": device_id, "challenge": challenge, "version": "1.0.0"},
            headers=headers, timeout=20, verify=False,
        )
        if r.status_code != 200:
            print(f"Tubi token: signing_key HTTP {r.status_code}")
            return None
        signing_key = r.json().get("key")
        if not signing_key:
            print("Tubi token: signing_key response missing 'key'")
            return None
    except Exception as e:
        print(f"Tubi token: signing_key error: {e}")
        return None
    body = json.dumps({"device_id": device_id, "challenge": challenge})
    body_bytes = body.encode("utf-8")
    digest = hmac.new(signing_key.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    params = {
        "X-Tubi-Algorithm": "TUBI-HMAC-SHA256",
        "X-Tubi-Date": datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
        "X-Tubi-Expires": "30",
        "X-Tubi-SignedHeaders": "content-type",
        "X-Tubi-Signature": digest,
    }
    try:
        r = requests.post(
            TUBI_TOKEN_URL, params=params, data=body_bytes,
            headers=headers, timeout=20, verify=False,
        )
        if r.status_code != 200:
            print(f"Tubi token: token exchange HTTP {r.status_code} - {r.text[:150]}")
            return None
        token = r.json().get("access_token")
        if not token:
            print("Tubi token: token response missing 'access_token'")
            return None
        print("Tubi token: obtained anonymous access token")
        return token
    except Exception as e:
        print(f"Tubi token: token exchange error: {e}")
        return None


def _tubi_balanced_object(text, start):
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return text[start:i + 1]
    return None


def _tubi_clean_js(js):
    js = re.sub(r'new Date\("([^"]*)"\)', r'"\1"', js)
    js = re.sub(r"\bundefined\b", "null", js)
    js = re.sub(r"\bNaN\b", "null", js)
    return js


def _tubi_extract_page_json(html):
    blobs = []
    m = re.search(r"window\.__data\s*=\s*", html)
    if m:
        i = html.find("{", m.end())
        if i != -1:
            obj = _tubi_balanced_object(html, i)
            if obj: blobs.append(obj)
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m: blobs.append(m.group(1))
    for m in re.finditer(r"<script[^>]*>\s*(\{.*?\})\s*</script>", html, re.S):
        if "epg" in m.group(1) or "contentIds" in m.group(1):
            blobs.append(m.group(1))
    for b in blobs:
        try: return json.loads(_tubi_clean_js(b))
        except json.JSONDecodeError: continue
    return None


def _tubi_find_key(obj, wanted):
    if isinstance(obj, dict):
        if wanted in obj: return obj[wanted]
        for v in obj.values():
            r = _tubi_find_key(v, wanted)
            if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = _tubi_find_key(v, wanted)
            if r is not None: return r
    return None


def _tubi_to_id(v):
    if isinstance(v, dict):
        v = v.get("content_id") or v.get("id")
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _tubi_ids_from_page_data(data):
    by_container = _tubi_find_key(data, "contentIdsByContainer")
    ids, cats = [], {}
    if isinstance(by_container, dict):
        container_lists = list(by_container.values())
    elif isinstance(by_container, list):
        container_lists = by_container
    else:
        container_lists = []
    for cat_list in container_lists:
        if not isinstance(cat_list, list): continue
        for cat in cat_list:
            if not isinstance(cat, dict): continue
            name = cat.get("name") or "Other"
            for entry in cat.get("contents", []):
                cid = _tubi_to_id(entry)
                if cid:
                    ids.append(cid)
                    cats.setdefault(str(cid), name)
    return list(dict.fromkeys(ids)), cats


def _tubi_ids_from_container_json(data):
    ids = []
    rows = data.get("rows") if isinstance(data, dict) else None
    for row in rows or []:
        for item in row.get("contents", []):
            cid = _tubi_to_id(item)
            if cid: ids.append(cid)
    if not ids and isinstance(data, dict):
        for item in data.get("contents", []):
            cid = _tubi_to_id(item)
            if cid: ids.append(cid)
    return list(dict.fromkeys(ids))


def _tubi_rows_from_epg(data):
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("contents") or data.get("data") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def _tubi_discover_ids():
    print("Tubi: fetching live page...")
    token = _tubi_get_anonymous_token()
    auth_headers = {"User-Agent": USER_AGENT}
    if token:
        auth_headers["Authorization"] = f"Bearer {token}"
    else:
        print("Tubi: no token obtained, proceeding unauthenticated (may fail)")
    try:
        r = requests.get(TUBI_LIVE_PAGE_URL, headers=auth_headers, timeout=25, verify=False)
        if r.status_code == 200:
            data = _tubi_extract_page_json(r.text)
            if data is not None:
                ids, cats = _tubi_ids_from_page_data(data)
                print(f"Tubi: live page -> {len(ids)} IDs")
                if ids: return ids, cats
            else:
                print("Tubi: live page loaded but no embedded JSON (format changed?)")
        else:
            print(f"Tubi: live page HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"Tubi: live page error: {e}")
    for url in TUBI_CONTAINER_CANDIDATES:
        try:
            r = requests.get(url, headers=auth_headers, timeout=25, verify=False)
            if r.status_code != 200:
                print(f"Tubi: {url} HTTP {r.status_code}")
                continue
            ids = _tubi_ids_from_container_json(r.json())
            print(f"Tubi: {url} -> {len(ids)} IDs")
            if ids: return ids, {}
        except (requests.RequestException, ValueError) as e:
            print(f"Tubi: {url} error: {e}")
    return [], {}


def _tubi_load_cached_ids():
    if os.path.exists(TUBI_FALLBACK_FILE):
        try:
            with open(TUBI_FALLBACK_FILE, encoding="utf-8") as f:
                ids = [int(x) for x in json.load(f).get("ids", [])]
            if ids:
                print(f"Tubi: using {len(ids)} fallback IDs from {TUBI_FALLBACK_FILE}")
                return ids
        except Exception as e:
            print(f"Tubi: could not read {TUBI_FALLBACK_FILE}: {e}")
    print(f"Tubi: {TUBI_FALLBACK_FILE} unavailable - using {len(TUBI_SEED_IDS)} built-in seed IDs")
    return list(TUBI_SEED_IDS)


def _tubi_save_cached_ids(ids):
    existing = 0
    if os.path.exists(TUBI_FALLBACK_FILE):
        try:
            with open(TUBI_FALLBACK_FILE, encoding="utf-8") as f:
                existing = len(json.load(f).get("ids", []))
        except Exception:
            existing = 0
    if len(ids) < max(TUBI_CACHE_MIN_IDS, existing):
        print(f"Tubi: not overwriting cache (found {len(ids)} < existing {existing})")
        return
    try:
        with open(TUBI_FALLBACK_FILE, "w", encoding="utf-8") as f:
            json.dump({"updated": datetime.utcnow().isoformat() + "Z",
                       "count": len(ids), "ids": sorted(set(ids))}, f, indent=2)
        print(f"Tubi: cached {len(ids)} IDs to {TUBI_FALLBACK_FILE}")
    except Exception as e:
        print(f"Tubi: could not write {TUBI_FALLBACK_FILE}: {e}")


def _tubi_fetch_epg(ids):
    all_rows, seen = [], set()
    batches = [ids[i:i + TUBI_EPG_BATCH_SIZE] for i in range(0, len(ids), TUBI_EPG_BATCH_SIZE)]
    working = None
    for n, batch in enumerate(batches, 1):
        params = {"content_id": ",".join(map(str, batch))}
        endpoints = [working] if working else TUBI_EPG_ENDPOINTS
        got = []
        for url in endpoints:
            try:
                r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT},
                                 timeout=40, verify=False)
                if r.status_code != 200:
                    print(f"Tubi: EPG batch {n}/{len(batches)} {urlparse(url).netloc} HTTP {r.status_code}")
                    continue
                got = _tubi_rows_from_epg(r.json())
                if got:
                    working = url
                    break
            except (requests.RequestException, ValueError) as e:
                print(f"Tubi: EPG batch {n}/{len(batches)} {urlparse(url).netloc} error: {e}")
        new = 0
        for row in got:
            cid = str(row.get("content_id") or row.get("id") or "")
            if cid and cid not in seen:
                seen.add(cid)
                all_rows.append(row)
                new += 1
        print(f"Tubi: EPG batch {n}/{len(batches)}: +{new}")
    print(f"Tubi: EPG total {len(all_rows)} channels")
    return all_rows


def _tubi_clean_stream_url(url):
    p = urlparse(unquote(url))
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _tubi_pick_manifest(channel):
    resources = channel.get("video_resources") or []
    candidates = []
    for res in resources:
        url = (res.get("manifest") or {}).get("url")
        if url:
            drm = bool(res.get("license_server") or res.get("drm"))
            candidates.append((drm, url))
    candidates.sort(key=lambda x: x[0])
    return _tubi_clean_stream_url(candidates[0][1]) if candidates else ""


def _tubi_thumbnail(channel):
    imgs = channel.get("images") or {}
    thumb = imgs.get("thumbnail") or imgs.get("landscape") or []
    if isinstance(thumb, str): return thumb
    return thumb[0] if thumb else ""


def _tubi_xmltv_time(iso):
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(iso, fmt).strftime("%Y%m%d%H%M%S +0000")
        except (ValueError, TypeError):
            pass
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y%m%d%H%M%S +0000")
    except (ValueError, TypeError):
        return iso or ""


def generate_tubi_m3u():
    ids, categories = _tubi_discover_ids()
    if ids:
        _tubi_save_cached_ids(ids)
    else:
        print("Tubi: live discovery failed - falling back to cached/fallback IDs")
        ids = _tubi_load_cached_ids()

    ids = list(dict.fromkeys(ids))
    print(f"Tubi: querying {len(ids)} channel IDs")

    rows = _tubi_fetch_epg(ids)
    if not rows:
        raise RuntimeError("Tubi EPG returned nothing (geo-block or endpoint change?)")

    lines = [f'#EXTM3U url-tvg="{TUBI_EPG_URL_TVG}"',
             f"# Generated {datetime.utcnow().isoformat()}Z"]
    seen_urls = set()
    for ch in sorted(rows, key=lambda c: (c.get("title") or "").lower()):
        url = _tubi_pick_manifest(ch)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        cid = str(ch.get("content_id") or ch.get("id") or "")
        name = (ch.get("title") or "Unknown").replace(",", " ")
        group = categories.get(cid, TUBI_GROUP_TITLE)
        lines.append(f'#EXTINF:-1 tvg-id="{cid}" tvg-logo="{_tubi_thumbnail(ch)}" '
                     f'group-title="{group}",{name}')
        lines.append(url)
    write_m3u_file("tubi_all.m3u", "\n".join(lines) + "\n")

    root = ET.Element("tv", {"generator-info-name": "generate_playlists-tubi"})
    for ch in rows:
        cid = str(ch.get("content_id") or ch.get("id") or "")
        c = ET.SubElement(root, "channel", id=cid)
        ET.SubElement(c, "display-name").text = ch.get("title") or "Unknown"
        if _tubi_thumbnail(ch):
            ET.SubElement(c, "icon", src=_tubi_thumbnail(ch))
    for ch in rows:
        cid = str(ch.get("content_id") or ch.get("id") or "")
        for prog in ch.get("programs") or []:
            p = ET.SubElement(root, "programme", channel=cid,
                              start=_tubi_xmltv_time(prog.get("start_time")),
                              stop=_tubi_xmltv_time(prog.get("end_time")))
            ET.SubElement(p, "title").text = prog.get("title") or ""
            if prog.get("description"):
                ET.SubElement(p, "desc").text = prog["description"]
    ET.ElementTree(root).write(
        os.path.join(OUTPUT_DIR, "tubi_epg.xml"),
        encoding="utf-8", xml_declaration=True
    )

    print(f"Tubi: wrote {len(seen_urls)} channels -> playlists/tubi_all.m3u, playlists/tubi_epg.xml")

# Prefixes each generator owns. Used to replace only that generator's files
# on success, and to leave them alone on failure.
GENERATOR_PREFIXES = {
    "pluto":         ("plutotv_",),
    "plex":          ("plex_",),
    "samsungtvplus": ("samsungtvplus_",),
    "tubi":          ("tubi_",),
    "roku":          ("roku_",),
}

if __name__ == "__main__":
    ensure_output_dir()

    generators = [
        ("pluto",         generate_pluto_m3u),
        ("plex",          generate_plex_m3u),
        ("samsungtvplus", generate_samsungtvplus_m3u),
        ("tubi",          generate_tubi_m3u),
        ("roku",          generate_roku_m3u),
    ]

    for name, func in generators:
        prefixes = GENERATOR_PREFIXES[name]
        before = snapshot_files(prefixes)
        try:
            func()
            after = snapshot_files(prefixes)
            # Delete only this generator's own stale files it did NOT rewrite
            # this run (e.g. a region dropped upstream). Never touches others.
            stale = (before - after)
            remove_files(stale)
            if stale:
                logger.info(f"[OK] {name} (pruned {len(stale)} stale file(s))")
            else:
                logger.info(f"[OK] {name}")
        except Exception as e:
            msg = f"{name} generator failed and was skipped: {e}"
            logger.warning(f"[BROKEN] {msg}")
            print(f"::warning title=Generator failed::{msg}")
            # Deliberately do nothing to files: leave last-good output intact.
