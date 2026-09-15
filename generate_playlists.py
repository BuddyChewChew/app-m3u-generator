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

def cleanup_output_dir():
    if os.path.exists(OUTPUT_DIR):
        logger.info(f"Cleaning up old playlists in {OUTPUT_DIR}...")
        for filename in os.listdir(OUTPUT_DIR):
            file_path = os.path.join(OUTPUT_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                logger.error(f"Failed to delete {file_path}: {e}")
    else:
        os.makedirs(OUTPUT_DIR)

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

TUBI_LIVE_PAGE_URL  = "https://tubitv.com/live"
TUBI_CONTAINERS_URL = "https://tubitv.com/oz/containers/linear"
TUBI_EPG_URL        = "https://tubitv.com/oz/epg/programming"
TUBI_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'application/json, text/html, */*',
}

_SOCKS_READY = None

def ensure_socks_support():
    """
    requests needs PySocks to use socks4:// proxies. CI runners frequently ship
    without it, producing "Missing dependencies for SOCKS support" on every
    proxy attempt. Detect that and try a one-time install so proxies can run.
    """
    global _SOCKS_READY
    if _SOCKS_READY is not None:
        return _SOCKS_READY

    try:
        import socks  # noqa: F401  (provided by PySocks)
        _SOCKS_READY = True
        return True
    except Exception:
        pass

    try:
        import subprocess
        import sys
        logger.info("PySocks not found; attempting to install it for SOCKS proxy support...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "PySocks"])
        import socks  # noqa: F401
        _SOCKS_READY = True
    except Exception as e:
        logger.warning(f"Could not enable SOCKS proxy support: {e}")
        _SOCKS_READY = False

    return _SOCKS_READY

def _tubi_request_kwargs(proxy):
    kwargs = {"headers": TUBI_HEADERS, "verify": False, "timeout": 20}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return kwargs

def get_proxies(country_code):
    url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country={country_code}&ssl=all&anonymity=elite"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            proxy_list = response.text.splitlines()
            return [f"socks4://{proxy}" for proxy in proxy_list if proxy.strip()]
        logger.warning(f"Proxy fetch returned {response.status_code}")
    except Exception as e:
        logger.warning(f"Proxy fetch error: {e}")
    return []

def fetch_channel_ids_via_api(proxy):
    """
    Preferred strategy: hit /oz/containers/linear which returns a JSON list of
    live channels with content_id values ready to pass to the EPG endpoint.
    Returns a list of int content_ids, or [] on failure.
    """
    try:
        response = requests.get(TUBI_CONTAINERS_URL, **_tubi_request_kwargs(proxy))
        if response.status_code != 200:
            logger.warning(f"containers/linear -> {response.status_code} (proxy={proxy})")
            return []
        data = response.json()
        channel_ids = []
        # Response shape: {"rows": [{"contents": [{"content_id": ...}, ...]}]}
        for row in data.get("rows", []):
            for item in row.get("contents", []):
                cid = _content_id_of(item)
                if cid is not None:
                    channel_ids.append(cid)
        # Alternate flat shape: {"contents": [...]}
        if not channel_ids:
            for item in data.get("contents", []):
                cid = _content_id_of(item)
                if cid is not None:
                    channel_ids.append(cid)
        logger.info(f"API strategy: found {len(channel_ids)} channel IDs")
        return channel_ids
    except Exception as e:
        logger.warning(f"API strategy error: {e}")
        return []

def fetch_channel_list(proxy, retries=3):
    """
    Fallback strategy: load /live and extract the window.__data JSON blob.
    Returns the parsed object, or {} on failure.
    """
    for attempt in range(retries):
        try:
            response = requests.get(TUBI_LIVE_PAGE_URL, **_tubi_request_kwargs(proxy))
            if response.status_code != 200:
                logger.warning(f"HTML fetch -> {response.status_code} attempt {attempt + 1} (proxy={proxy})")
                continue

            html_content = response.content.decode('utf-8', errors='replace')
            soup = BeautifulSoup(html_content, "html.parser")

            target_script = None
            for script in soup.find_all("script"):
                text = script.string or ""
                if "window.__data" in text:
                    target_script = text
                    break

            if not target_script:
                # Alternate embed names Tubi has used
                for script in soup.find_all("script"):
                    text = script.string or ""
                    if text.strip().startswith("{") and '"epg"' in text:
                        target_script = text
                        break

            if not target_script:
                logger.warning(f"HTML strategy: no window.__data found (attempt {attempt + 1})")
                continue

            start_index = target_script.find("{")
            end_index = target_script.rfind("}") + 1
            json_string = target_script[start_index:end_index]
            json_string = json_string.replace('undefined', 'null')
            json_string = re.sub(r'new Date\("([^"]*)"\)', r'"\1"', json_string)
            logger.info("HTML strategy: successfully decoded window.__data")
            return json.loads(json_string)
        except Exception as e:
            logger.warning(f"HTML strategy error (attempt {attempt + 1}): {e}")
    return {}

def _content_id_of(entry):
    """Normalise a Tubi container entry (int, str, or dict) into an int content id."""
    if isinstance(entry, bool):
        return None
    if isinstance(entry, int):
        return entry
    if isinstance(entry, dict):
        value = entry.get('content_id') or entry.get('contentId') or entry.get('id')
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    if isinstance(entry, str) and entry.strip().isdigit():
        return int(entry)
    return None

def _iter_container_categories(json_data):
    """
    Recursively yield any dict that looks like a Tubi EPG container/category
    (i.e. contains a 'contents' list). Used as a fallback when the blob no
    longer matches the legacy epg.contentIdsByContainer shape.
    """
    stack = [json_data]
    visited = set()
    while stack:
        node = stack.pop()
        if isinstance(node, (dict, list)):
            node_id = id(node)
            if node_id in visited:
                continue
            visited.add(node_id)
        if isinstance(node, dict):
            if isinstance(node.get('contents'), list):
                yield node
            for value in node.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            for value in node:
                if isinstance(value, (dict, list)):
                    stack.append(value)

def extract_ids_from_html_data(json_data):
    """Pull the content_id list out of the window.__data blob."""
    channel_ids = []

    # Preferred: known EPG container path.
    container = json_data if isinstance(json_data, dict) else {}
    content_ids_by_container = container.get('epg', {}).get('contentIdsByContainer', {})
    for container_list in content_ids_by_container.values():
        for category in container_list:
            for entry in category.get('contents', []):
                cid = _content_id_of(entry)
                if cid is not None:
                    channel_ids.append(cid)

    # Fallback: recursively scan the whole blob for any 'contents' arrays.
    if not channel_ids:
        for category in _iter_container_categories(json_data):
            for entry in category.get('contents', []):
                cid = _content_id_of(entry)
                if cid is not None:
                    channel_ids.append(cid)

    # Deduplicate while preserving order.
    seen = set()
    unique_ids = []
    for cid in channel_ids:
        if cid not in seen:
            seen.add(cid)
            unique_ids.append(cid)
    return unique_ids

def create_group_mapping(json_data):
    group_mapping = {}

    def _add_category(category):
        group_name = category.get('name') or category.get('title') or 'Other'
        for entry in category.get('contents', []):
            cid = _content_id_of(entry)
            if cid is not None:
                group_mapping[str(cid)] = group_name

    container = json_data if isinstance(json_data, dict) else {}
    content_ids_by_container = container.get('epg', {}).get('contentIdsByContainer', {})
    for container_list in content_ids_by_container.values():
        for category in container_list:
            _add_category(category)

    if not group_mapping:
        for category in _iter_container_categories(json_data):
            _add_category(category)

    return group_mapping

def fetch_epg_data(channel_list):
    """
    Fetch EPG rows for a list of content_ids.
    Batches into groups of 150 to stay within URL length limits.
    Returns a list of EPG row dicts.
    """
    if not channel_list:
        return []

    epg_data = []
    group_size = 150
    grouped_ids = [channel_list[i:i + group_size] for i in range(0, len(channel_list), group_size)]
    for group in grouped_ids:
        params = {"content_id": ','.join(map(str, group))}
        try:
            response = requests.get(TUBI_EPG_URL, params=params, headers=TUBI_HEADERS, timeout=20)
            if response.status_code != 200:
                logger.warning(f"EPG batch failed: {response.status_code}")
                continue
            epg_data.extend(response.json().get('rows', []))
        except Exception as e:
            logger.warning(f"EPG batch error: {e}")

    logger.info(f"EPG fetch: {len(epg_data)} rows returned")
    return epg_data

def clean_stream_url(url):
    parsed_url = urlparse(unquote(url))
    return urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, '', '', ''))

def create_m3u_playlist(epg_data, group_mapping):
    output_lines = ['#EXTM3U url-tvg="tubi_epg.xml"']
    seen_urls = set()
    for elem in sorted(epg_data, key=lambda x: x.get('title', '').lower()):
        channel_name = (elem.get('title') or 'Unknown Channel').encode('utf-8', errors='ignore').decode('utf-8')
        tvg_id = str(elem.get('content_id', ''))
        logo_url = (elem.get('images', {}).get('thumbnail') or [None])[0] or ''
        group_title = group_mapping.get(tvg_id, 'Other').encode('utf-8', errors='ignore').decode('utf-8')

        video_resources = elem.get('video_resources') or []
        if not video_resources:
            continue
        raw_url = (video_resources[0].get('manifest') or {}).get('url', '')
        clean_url = clean_stream_url(raw_url)
        if not clean_url or clean_url in seen_urls:
            continue

        output_lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo_url}" group-title="{group_title}",{channel_name}'
        )
        output_lines.append(clean_url)
        seen_urls.add(clean_url)

    return "\n".join(output_lines) + "\n"

def create_epg_xml(epg_data):
    root = ET.Element("tv")
    for station in epg_data:
        cid = str(station.get("content_id", ""))
        channel = ET.SubElement(root, "channel", id=cid)
        ET.SubElement(channel, "display-name").text = station.get("title", "Unknown Title")
        thumb = (station.get("images", {}).get("thumbnail") or [None])[0]
        if thumb:
            ET.SubElement(channel, "icon", src=thumb)

        for program in station.get('programs', []):
            programme = ET.SubElement(root, "programme", channel=cid)
            start = program.get("start_time", "")
            stop = program.get("end_time", "")
            try:
                dt_start = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
                dt_stop = datetime.strptime(stop, "%Y-%m-%dT%H:%M:%SZ")
                programme.set("start", dt_start.strftime("%Y%m%d%H%M%S +0000"))
                programme.set("stop", dt_stop.strftime("%Y%m%d%H%M%S +0000"))
            except Exception:
                programme.set("start", start)
                programme.set("stop", stop)
            ET.SubElement(programme, "title").text = program.get("title", "")
            if program.get("description"):
                ET.SubElement(programme, "desc").text = program.get("description", "")
    return ET.ElementTree(root)

def generate_tubi_m3u():
    # Make sure socks4:// proxies can be used at all (CI runners lack PySocks).
    socks_ok = ensure_socks_support()
    if socks_ok:
        proxies = get_proxies("US")
    else:
        proxies = []
        logger.warning("Tubi: SOCKS support unavailable; skipping proxies and using direct connection.")

    # Ordered connection options: US proxies first, then a direct connection.
    connection_options = list(proxies) + [None]

    channel_ids = []
    group_mapping = {}

    # --- Pass 1: JSON API (/oz/containers/linear) ---
    # This endpoint returns the COMPLETE linear lineup (all channels), so it is
    # strongly preferred over the HTML scrape, which only exposes a subset of
    # channels rendered on the /live page. Try every proxy, then direct.
    for proxy in connection_options:
        logger.info(f"Tubi: API attempt via {proxy or 'direct'}")
        ids = fetch_channel_ids_via_api(proxy)
        if ids:
            channel_ids = ids
            # HTML scrape is optional here; it only supplies group titles.
            html_data = fetch_channel_list(proxy)
            if html_data:
                group_mapping = create_group_mapping(html_data)
            break

    # --- Pass 2: HTML window.__data scrape (partial list, last resort) ---
    if not channel_ids:
        for proxy in connection_options:
            logger.info(f"Tubi: HTML attempt via {proxy or 'direct'}")
            html_data = fetch_channel_list(proxy)
            if html_data:
                ids = extract_ids_from_html_data(html_data)
                if ids:
                    channel_ids = ids
                    group_mapping = create_group_mapping(html_data)
                    break
                # Diagnostic: show top-level keys so future shape changes are visible.
                try:
                    keys = list(html_data.keys())[:25] if isinstance(html_data, dict) else type(html_data).__name__
                    logger.warning(f"Tubi: window.__data had no extractable IDs. Top-level keys: {keys}")
                except Exception:
                    pass

    if not channel_ids:
        logger.error("Tubi: could not retrieve any channel IDs. Skipping.")
        return

    logger.info(f"Tubi: got {len(channel_ids)} channel IDs")

    epg_data = fetch_epg_data(channel_ids)
    if not epg_data:
        logger.error("Tubi: EPG endpoint returned no data. Skipping.")
        return

    m3u_playlist = create_m3u_playlist(epg_data, group_mapping)
    epg_tree = create_epg_xml(epg_data)
    write_m3u_file("tubi_all.m3u", m3u_playlist)
    epg_tree.write(os.path.join(OUTPUT_DIR, "tubi_epg.xml"), encoding='utf-8', xml_declaration=True)
    logger.info(f"Tubi: wrote {len(epg_data)} channels.")

# --- Execution ---

if __name__ == "__main__":
    cleanup_output_dir()
    generate_pluto_m3u()
    generate_plex_m3u()
    generate_samsungtvplus_m3u()
    generate_tubi_m3u()
    generate_roku_m3u()