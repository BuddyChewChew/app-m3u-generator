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
                group = REGION_MAP.get(region.lower(), region.upper()) if region != 'all' else 'Plex'
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
    output_lines = ['#EXTM3U url-tvg="https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml.gz"\n']
    for c_id, ch in data['channels'].items():
        output_lines.extend([format_extinf(c_id, c_id, ch.get('chno'), ch['name'], ch['logo'], "Roku", ch['name']), f"https://jmp2.uk/rok-{c_id}.m3u8\n"])
    write_m3u_file("roku_all.m3u", "".join(output_lines))

# --- Tubi Scraping Logic ---

def get_proxies(country_code):
    url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country={country_code}&ssl=all&anonymity=elite"
    response = requests.get(url)
    if response.status_code == 200:
        proxy_list = response.text.splitlines()
        return [f"socks4://{proxy}" for proxy in proxy_list]
    else:
        return []

def fetch_channel_list(proxy, retries=3):
    url = "https://tubitv.com/live"
    for attempt in range(retries):
        try:
            if proxy:
                response = requests.get(url, proxies={"http": proxy, "https": proxy}, verify=False, timeout=20)
            else:
                response = requests.get(url, verify=False, timeout=20)
            response.encoding = 'utf-8'
            if response.status_code != 200: continue

            html_content = response.content.decode('utf-8', errors='replace')
            soup = BeautifulSoup(html_content, "html.parser")
            script_tags = soup.find_all("script")
            target_script = None
            for script in script_tags:
                if script.string and script.string.strip().startswith("window.__data"):
                    target_script = script.string
                    break
            if not target_script: continue

            start_index = target_script.find("{")
            end_index = target_script.rfind("}") + 1
            json_string = target_script[start_index:end_index]
            json_string = json_string.replace('undefined', 'null')
            json_string = re.sub(r'new Date\("([^"]*)"\)', r'"\1"', json_string)
            return json.loads(json_string)
        except: continue
    return []

def create_group_mapping(json_data):
    group_mapping = {}
    content_ids_by_container = json_data.get('epg', {}).get('contentIdsByContainer', {})
    for container_list in content_ids_by_container.values():
        for category in container_list:
            group_name = category.get('name', 'Other')
            for content_id in category.get('contents', []):
                group_mapping[str(content_id)] = group_name
    return group_mapping

def fetch_epg_data(channel_list):
    epg_data = []
    group_size = 150
    grouped_ids = [channel_list[i:i + group_size] for i in range(0, len(channel_list), group_size)]
    for group in grouped_ids:
        url = "https://tubitv.com/oz/epg/programming"
        params = {"content_id": ','.join(map(str, group))}
        response = requests.get(url, params=params)
        if response.status_code == 200:
            epg_data.extend(response.json().get('rows', []))
    return epg_data

def clean_stream_url(url):
    parsed_url = urlparse(url)
    return urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, '', '', ''))

def create_m3u_playlist(epg_data, group_mapping):
    sorted_epg_data = sorted(epg_data, key=lambda x: x.get('title', '').lower())
    playlist = f"#EXTM3U url-tvg=\"tubi_epg.xml\"\n"
    seen_urls = set()
    for elem in sorted_epg_data:
        channel_name = elem.get('title', 'Unknown Channel').encode('utf-8', errors='ignore').decode('utf-8')
        stream_url = unquote(elem['video_resources'][0]['manifest']['url']) if elem.get('video_resources') else ''
        clean_url = clean_stream_url(stream_url)
        tvg_id = str(elem.get('content_id', ''))
        logo_url = elem.get('images', {}).get('thumbnail', [None])[0]
        group_title = group_mapping.get(tvg_id, 'Other').encode('utf-8', errors='ignore').decode('utf-8')
        if clean_url and clean_url not in seen_urls:
            playlist += f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo_url}" group-title="{group_title}",{channel_name}\n{clean_url}\n'
            seen_urls.add(clean_url)
    return playlist

def create_epg_xml(epg_data):
    root = ET.Element("tv")
    for station in epg_data:
        channel = ET.SubElement(root, "channel", id=str(station.get("content_id")))
        ET.SubElement(channel, "display-name").text = station.get("title", "Unknown Title")
        ET.SubElement(channel, "icon", src=station.get("images", {}).get("thumbnail", [None])[0])
        for program in station.get('programs', []):
            programme = ET.SubElement(root, "programme", channel=str(station.get("content_id")))
            start = program.get("start_time", "")
            stop = program.get("end_time", "")
            try:
                dt_start = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
                dt_stop = datetime.strptime(stop, "%Y-%m-%dT%H:%M:%SZ")
                programme.set("start", dt_start.strftime("%Y%m%d%H%M%S +0000"))
                programme.set("stop", dt_stop.strftime("%Y%m%d%H%M%S +0000"))
            except:
                programme.set("start", start)
                programme.set("stop", stop)
            ET.SubElement(programme, "title").text = program.get("title", "")
            if program.get("description"):
                ET.SubElement(programme, "desc").text = program.get("description", "")
    return ET.ElementTree(root)

def generate_tubi_m3u():
    proxies = get_proxies("US")
    json_data = None
    if proxies:
        for proxy in proxies:
            json_data = fetch_channel_list(proxy)
            if json_data: break
    if not json_data: json_data = fetch_channel_list(None)
    if not json_data: return

    channel_list = []
    content_ids_by_container = json_data.get('epg', {}).get('contentIdsByContainer', {})
    for container_list in content_ids_by_container.values():
        for category in container_list:
            channel_list.extend(category.get('contents', []))

    epg_data = fetch_epg_data(channel_list)
    if epg_data:
        group_mapping = create_group_mapping(json_data)
        m3u_playlist = create_m3u_playlist(epg_data, group_mapping)
        epg_tree = create_epg_xml(epg_data)
        write_m3u_file("tubi_all.m3u", m3u_playlist)
        epg_tree.write(os.path.join(OUTPUT_DIR, "tubi_epg.xml"), encoding='utf-8', xml_declaration=True)

# --- Execution ---

if __name__ == "__main__":
    cleanup_output_dir()
    generate_pluto_m3u()
    generate_plex_m3u()
    generate_samsungtvplus_m3u()
    generate_tubi_m3u()
    generate_roku_m3u()

