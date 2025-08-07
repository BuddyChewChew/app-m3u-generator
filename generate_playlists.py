import requests
import gzip
import json
import os
import logging
from io import BytesIO

# --- Configuration ---
OUTPUT_DIR = "playlists"
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
REQUEST_TIMEOUT = 30 # seconds

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---
def fetch_url(url, is_json=True, is_gzipped=False, headers=None, stream=False):
    """Fetches data from a URL, handles gzip, and parses JSON if needed."""
    logging.info(f"Fetching URL: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        if stream: # Return the raw response object for streaming content (like Tubi's M3U)
             logging.info("Returning streaming response.")
             return response

        content = response.content
        if is_gzipped:
            logging.info("Decompressing gzipped content.")
            try:
                # Use BytesIO to treat the byte string as a file-like object
                with gzip.GzipFile(fileobj=BytesIO(content), mode='rb') as f:
                    content = f.read()
                content = content.decode('utf-8') # Decode bytes to string
            except gzip.BadGzipFile:
                logging.warning("Content was not gzipped, trying as plain text.")
                content = content.decode('utf-8') # Assume it was plain text
            except Exception as e:
                 logging.error(f"Error decompressing gzip: {e}")
                 raise # Re-raise the exception

        else:
             content = content.decode('utf-8') # Decode bytes to string for non-gzipped

        if is_json:
            logging.info("Parsing JSON data.")
            return json.loads(content)
        else:
            logging.info("Returning raw text content.")
            return content # Return raw text if not JSON

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred for {url}: {e}")
        return None

def write_m3u_file(filename, content):
    """Writes content to a file in the output directory."""
    if not os.path.exists(OUTPUT_DIR):
        logging.info(f"Creating output directory: {OUTPUT_DIR}")
        os.makedirs(OUTPUT_DIR)

    filepath = os.path.join(OUTPUT_DIR, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"Successfully wrote playlist to {filepath}")
    except IOError as e:
        logging.error(f"Error writing file {filepath}: {e}")

def format_extinf(channel_id, tvg_id, tvg_chno, tvg_name, tvg_logo, group_title, display_name):
    """Formats the #EXTINF line."""
    # Ensure tvg_chno is empty if None or invalid
    chno_str = str(tvg_chno) if tvg_chno is not None and str(tvg_chno).isdigit() else ""
    
    # Basic sanitization for names/titles within the M3U format
    sanitized_tvg_name = tvg_name.replace('"', "'")
    sanitized_group_title = group_title.replace('"', "'")
    sanitized_display_name = display_name.replace(',', '') # Commas break the EXTINF line itself

    return (f'#EXTINF:-1 '
            f'channel-id="{channel_id}" '
            f'tvg-id="{tvg_id}" '
            f'tvg-chno="{chno_str}" '
            f'tvg-name="{sanitized_tvg_name}" '
            f'tvg-logo="{tvg_logo}" '
            f'group-title="{sanitized_group_title}",'
            f'{sanitized_display_name}\n')

# --- Service Functions ---

def generate_pluto_m3u(regions=['us', 'ca', 'gb', 'au', 'all'], sort='name'):
    """Generates M3U playlists for PlutoTV."""
    PLUTO_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/.channels.json.gz'
    STREAM_URL_TEMPLATE = 'https://jmp2.uk/plu-{id}.m3u8'
    EPG_URL_TEMPLATE = 'https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/{region}.xml.gz'

    data = fetch_url(PLUTO_URL, is_json=True, is_gzipped=True)
    if not data or 'regions' not in data:
        logging.error("Failed to fetch or parse PlutoTV data.")
        return

    region_name_map = {
        "ar": "Argentina", "br": "Brazil", "ca": "Canada", "cl": "Chile", "co": "Colombia",
        "cr": "Costa Rica", "de": "Germany", "dk": "Denmark", "do": "Dominican Republic",
        "ec": "Ecuador", "es": "Spain", "fr": "France", "gb": "United Kingdom", "gt": "Guatemala",
        "it": "Italy", "mx": "Mexico", "no": "Norway", "pe": "Peru", "se": "Sweden",
        "us": "United States", "latam": "Latin America" # Add others as needed from data
    }

    for region in regions:
        logging.info(f"--- Generating PlutoTV playlist for region: {region} ---")
        epg_url = EPG_URL_TEMPLATE.replace('{region}', region)
        output_lines = [f'#EXTM3U url-tvg="{epg_url}"\n']
        channels_to_process = {}
        is_all_region = region.lower() == 'all'

        if is_all_region:
            for region_key, region_data in data.get('regions', {}).items():
                region_full_name = region_name_map.get(region_key, region_key.upper())
                for channel_key, channel_info in region_data.get('channels', {}).items():
                    unique_channel_id = f"{channel_key}-{region_key}"
                    # Add region info for grouping in 'all' list
                    channels_to_process[unique_channel_id] = {
                        **channel_info,
                        'region_code': region_key,
                        'group_title_override': region_full_name,
                        'original_id': channel_key
                    }
        else:
            region_data = data.get('regions', {}).get(region)
            if not region_data:
                logging.warning(f"Region '{region}' not found in PlutoTV data. Skipping.")
                continue
            for channel_key, channel_info in region_data.get('channels', {}).items():
                 channels_to_process[channel_key] = {
                     **channel_info,
                     'region_code': region,
                     'original_id': channel_key
                 }

        # Sort channels
        try:
             if sort == 'chno':
                 sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: int(channels_to_process[k].get('chno', 99999)))
             else: # Default to name sort
                 sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: channels_to_process[k].get('name', '').lower())
        except Exception as e:
             logging.warning(f"Sorting failed for PlutoTV {region}, using default order. Error: {e}")
             sorted_channel_ids = list(channels_to_process.keys())

        # Build M3U entries
        for channel_id in sorted_channel_ids:
            channel = channels_to_process[channel_id]
            chno = channel.get('chno')
            name = channel.get('name', 'Unknown Channel')
            logo = channel.get('logo', '')
            group = channel.get('group_title_override') if is_all_region else channel.get('group', 'Uncategorized')
            original_id = channel.get('original_id', channel_id.split('-')[0]) # Fallback for safety
            tvg_id = original_id # Use the base ID for EPG matching across regions

            extinf = format_extinf(channel_id, tvg_id, chno, name, logo, group, name)
            stream_url = STREAM_URL_TEMPLATE.replace('{id}', original_id)
            output_lines.append(extinf)
            output_lines.append(stream_url + '\n')

        write_m3u_file(f"plutotv_{region}.m3u", "".join(output_lines))

def generate_plex_m3u(regions=['us', 'ca', 'gb', 'au', 'all'], sort='name'):
    """Generates M3U playlists for Plex."""
    PLEX_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Plex/.channels.json.gz'
    CHANNELS_JSON_URL = 'https://raw.githubusercontent.com/Mikoshi-nyudo/plex-channels-list/refs/heads/main/plex/channels.json' # For genre mapping
    STREAM_URL_TEMPLATE = 'https://jmp2.uk/plex-{id}.m3u8'
    EPG_URL_TEMPLATE = 'https://github.com/matthuisman/i.mjh.nz/raw/master/Plex/{region}.xml.gz'
    PLEX_HEADERS = {'User-Agent': USER_AGENT}

    data = fetch_url(PLEX_URL, is_json=True, is_gzipped=True, headers=PLEX_HEADERS)
    plex_channels_list = fetch_url(CHANNELS_JSON_URL, is_json=True, headers=PLEX_HEADERS) # For genres

    if not data or 'channels' not in data:
        logging.error("Failed to fetch or parse Plex data.")
        return
    if not plex_channels_list:
        logging.warning("Failed to fetch Plex genre list, groups might be inaccurate.")
        plex_channels_list = [] # Use empty list if fetch fails

    # Create a lookup dictionary for faster genre retrieval
    genre_lookup = {ch.get('Title', '').lower(): ch.get('Genre', 'Uncategorized') for ch in plex_channels_list}

    region_name_map = {
        "us": "United States", "mx": "Mexico", "es": "Spain", "ca": "Canada",
        "au": "Australia", "nz": "New Zealand", "br": "Brazil", "gb": "United Kingdom",
        "de": "Germany", "ch": "Switzerland", "it": "Italy", "fr": "France",
        "at": "Austria", "ie": "Ireland", "za": "South Africa" # Add others as needed
    }

    for region in regions:
        logging.info(f"--- Generating Plex playlist for region: {region} ---")
        epg_url = EPG_URL_TEMPLATE.replace('{region}', region)
        output_lines = [f'#EXTM3U url-tvg="{epg_url}"\n']
        channels_to_process = {}
        is_all_region = region.lower() == 'all'

        all_plex_channels = data.get('channels', {})

        if is_all_region:
            for channel_key, channel_info in all_plex_channels.items():
                channel_regions = channel_info.get('regions', [])
                for reg_code in channel_regions:
                    region_full_name = region_name_map.get(reg_code, reg_code.upper())
                    unique_channel_id = f"{channel_key}-{reg_code}"
                    channels_to_process[unique_channel_id] = {
                        **channel_info,
                        'region_code': reg_code,
                        'group_title_override': region_full_name,
                        'original_id': channel_key
                    }
        else:
            if region not in region_name_map and region not in data.get('regions', {}): # Check both map and data
                logging.warning(f"Region '{region}' not found or mapped in Plex data. Skipping.")
                continue

            for channel_key, channel_info in all_plex_channels.items():
                if region in channel_info.get('regions', []):
                    # Get genre from the fetched list
                    channel_name_lower = channel_info.get('name', '').lower()
                    genre = genre_lookup.get(channel_name_lower, 'Uncategorized')
                    channels_to_process[channel_key] = {
                        **channel_info,
                         'group': genre, # Assign genre as group for single region
                         'original_id': channel_key,
                         'region_code': region
                    }

        # Sort channels
        try:
            if sort == 'chno':
                # Plex 'chno' seems unreliable or absent, prioritize name sort or use a high default
                sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: (int(channels_to_process[k].get('chno', 99999)), channels_to_process[k].get('name', '').lower()))
            else: # Default to name sort
                sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: channels_to_process[k].get('name', '').lower())
        except Exception as e:
            logging.warning(f"Sorting failed for Plex {region}, using default order. Error: {e}")
            sorted_channel_ids = list(channels_to_process.keys())

        # Build M3U entries
        for channel_id in sorted_channel_ids:
            channel = channels_to_process[channel_id]
            chno = channel.get('chno')
            name = channel.get('name', 'Unknown Channel')
            logo = channel.get('logo', '')
            group = channel.get('group_title_override') if is_all_region else channel.get('group', 'Uncategorized')
            original_id = channel.get('original_id', channel_id.split('-')[0]) # Use the base ID for EPG matching
            tvg_id = original_id # Use the base ID for EPG matching

            extinf = format_extinf(channel_id, tvg_id, chno, name, logo, group, name)
            stream_url = STREAM_URL_TEMPLATE.replace('{id}', original_id)
            output_lines.append(extinf)
            output_lines.append(stream_url + '\n')

        write_m3u_file(f"plex_{region}.m3u", "".join(output_lines))


def generate_samsungtvplus_m3u(regions=['us', 'ca', 'gb', 'au', 'de', 'all'], sort='name'):
    """Generates M3U playlists for SamsungTVPlus."""
    SAMSUNG_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/SamsungTVPlus/.channels.json.gz'
    STREAM_URL_TEMPLATE = 'https://jmp2.uk/sam-{id}.m3u8'
    EPG_URL_TEMPLATE = 'https://github.com/matthuisman/i.mjh.nz/raw/master/SamsungTVPlus/{region}.xml.gz'

    data = fetch_url(SAMSUNG_URL, is_json=True, is_gzipped=True)
    if not data or 'regions' not in data:
        logging.error("Failed to fetch or parse SamsungTVPlus data.")
        return

    for region in regions:
        logging.info(f"--- Generating SamsungTVPlus playlist for region: {region} ---")
        epg_url = EPG_URL_TEMPLATE.replace('{region}', region)
        output_lines = [f'#EXTM3U url-tvg="{epg_url}"\n']
        channels_to_process = {}
        is_all_region = region.lower() == 'all'

        if is_all_region:
            for region_key, region_data in data.get('regions', {}).items():
                region_full_name = region_data.get('name', region_key.upper())
                for channel_key, channel_info in region_data.get('channels', {}).items():
                    unique_channel_id = f"{channel_key}-{region_key}"
                    channels_to_process[unique_channel_id] = {
                        **channel_info,
                        'region_code': region_key,
                        'group_title_override': region_full_name,
                        'original_id': channel_key
                    }
        else:
            region_data = data.get('regions', {}).get(region)
            if not region_data:
                logging.warning(f"Region '{region}' not found in SamsungTVPlus data. Skipping.")
                continue
            for channel_key, channel_info in region_data.get('channels', {}).items():
                 channels_to_process[channel_key] = {
                     **channel_info,
                     'original_id': channel_key,
                     'region_code': region
                 }

        # Sort channels
        try:
            if sort == 'chno':
                sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: int(channels_to_process[k].get('chno', 99999)))
            else: # Default to name sort
                sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: channels_to_process[k].get('name', '').lower())
        except Exception as e:
            logging.warning(f"Sorting failed for SamsungTVPlus {region}, using default order. Error: {e}")
            sorted_channel_ids = list(channels_to_process.keys())


        # Build M3U entries
        for channel_id in sorted_channel_ids:
            channel = channels_to_process[channel_id]
            chno = channel.get('chno')
            name = channel.get('name', 'Unknown Channel')
            logo = channel.get('logo', '')
            group = channel.get('group_title_override') if is_all_region else channel.get('group', 'Uncategorized')
            original_id = channel.get('original_id', channel_id.split('-')[0]) # Use the base ID for EPG matching
            tvg_id = original_id # Use the base ID for EPG matching

            extinf = format_extinf(channel_id, tvg_id, chno, name, logo, group, name)
            stream_url = STREAM_URL_TEMPLATE.replace('{id}', original_id)
            output_lines.append(extinf)
            output_lines.append(stream_url + '\n')

        write_m3u_file(f"samsungtvplus_{region}.m3u", "".join(output_lines))


def generate_stirr_m3u(sort='name'):
    """Generates M3U playlist for Stirr."""
    STIRR_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Stirr/.channels.json.gz'
    STREAM_URL_TEMPLATE = 'https://jmp2.uk/str-{id}.m3u8'
    EPG_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/master/Stirr/all.xml.gz' # Note: master branch, not refs/heads/master for EPG usually

    logging.info("--- Generating Stirr playlist ---")
    data = fetch_url(STIRR_URL, is_json=True, is_gzipped=True)
    if not data or 'channels' not in data:
        logging.error("Failed to fetch or parse Stirr data.")
        return

    output_lines = [f'#EXTM3U url-tvg="{EPG_URL}"\n']
    channels_to_process = data.get('channels', {})

    # Sort channels
    try:
        if sort == 'chno':
             sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: int(channels_to_process[k].get('chno', 99999)))
        else: # Default to name sort
             sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: channels_to_process[k].get('name', '').lower())
    except Exception as e:
        logging.warning(f"Sorting failed for Stirr, using default order. Error: {e}")
        sorted_channel_ids = list(channels_to_process.keys())

    # Build M3U entries
    for channel_id in sorted_channel_ids:
        channel = channels_to_process[channel_id]
        chno = channel.get('chno')
        name = channel.get('name', 'Unknown Channel')
        logo = channel.get('logo', '')
        groups_list = channel.get('groups', [])
        group_title = ', '.join(groups_list) if groups_list else 'Uncategorized'
        tvg_id = channel_id # Stirr IDs seem unique enough

        extinf = format_extinf(channel_id, tvg_id, chno, name, logo, group_title, name)
        stream_url = STREAM_URL_TEMPLATE.replace('{id}', channel_id)
        output_lines.append(extinf)
        output_lines.append(stream_url + '\n')

    write_m3u_file("stirr_all.m3u", "".join(output_lines))

def generate_tubi_m3u():
    """Generates M3U playlist for Tubi by fetching pre-made list."""
    TUBI_PLAYLIST_URL = 'https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_playlist.m3u'
    EPG_URL = 'https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_epg.xml'
    TUBI_HEADERS = {'User-Agent': USER_AGENT}

    logging.info("--- Generating Tubi playlist ---")
    # Fetch Tubi's M3U content directly as text (stream=True in helper not strictly needed here, but good practice)
    # response = fetch_url(TUBI_PLAYLIST_URL, is_json=False, is_gzipped=False, headers=TUBI_HEADERS, stream=True)
    # Using stream=False is simpler if the file isn't huge
    playlist_content = fetch_url(TUBI_PLAYLIST_URL, is_json=False, is_gzipped=False, headers=TUBI_HEADERS)

    if not playlist_content:
        logging.error("Failed to fetch Tubi playlist content.")
        return

    # Ensure the fetched content doesn't start with its own M3U header
    lines = playlist_content.strip().splitlines()
    if lines and lines[0].strip().upper() == '#EXTM3U':
        logging.info("Removing existing #EXTM3U header from fetched Tubi content.")
        playlist_data = "\n".join(lines[1:])
    else:
        playlist_data = "\n".join(lines)


    output_content = f'#EXTM3U url-tvg="{EPG_URL}"\n'
    output_content += playlist_data

    # Add a newline at the end if it's missing
    if not output_content.endswith('\n'):
        output_content += '\n'

    write_m3u_file("tubi_all.m3u", output_content)

def generate_roku_m3u(sort='chno'):
    """Generates M3U playlist for Roku."""
    ROKU_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/Roku/.channels.json.gz'
    STREAM_URL_TEMPLATE = 'https://jmp2.uk/rok-{id}.m3u8'
    EPG_URL = 'https://github.com/matthuisman/i.mjh.nz/raw/master/Roku/all.xml.gz'

    logging.info("--- Generating Roku playlist ---")
    data = fetch_url(ROKU_URL, is_json=True, is_gzipped=True)
    if not data or 'channels' not in data:
        logging.error("Failed to fetch or parse Roku data.")
        return

    output_lines = [f'#EXTM3U url-tvg="{EPG_URL}"\n']
    channels_to_process = data.get('channels', {})

    # Sort channels
    try:
        if sort == 'chno':
            sorted_channel_ids = sorted(channels_to_process.keys(), 
                                     key=lambda k: int(channels_to_process[k].get('chno', 99999)))
        else:  # Default to name sort
            sorted_channel_ids = sorted(channels_to_process.keys(), 
                                     key=lambda k: channels_to_process[k].get('name', '').lower())
    except Exception as e:
        logging.warning(f"Sorting failed for Roku, using default order. Error: {e}")
        sorted_channel_ids = list(channels_to_process.keys())

    # Build M3U entries
    for channel_id in sorted_channel_ids:
        channel = channels_to_process[channel_id]
        chno = channel.get('chno')
        name = channel.get('name', 'Unknown Channel')
        logo = channel.get('logo', '')
        groups_list = channel.get('groups', [])
        group_title = groups_list[0] if groups_list else 'Uncategorized'
        tvg_id = channel_id  # Roku IDs seem unique enough

        extinf = format_extinf(channel_id, tvg_id, chno, name, logo, group_title, name)
        stream_url = STREAM_URL_TEMPLATE.replace('{id}', channel_id)
        output_lines.append(extinf)
        output_lines.append(stream_url + '\n')

    write_m3u_file("roku_all.m3u", "".join(output_lines))

# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Starting playlist generation process...")
    
    # List of services to generate playlists for
    services = [
        'pluto',
        'plex',
        'samsungtvplus',
        'stirr',
        'tubi',
        'roku'  # Added Roku service
    ]
    
    # Default regions for services that support them
    regions = ['us', 'ca', 'gb', 'au', 'all']
    
    # Generate playlists for each service
    for service in services:
        try:
            if service == 'pluto':
                generate_pluto_m3u(regions=regions)
            elif service == 'plex':
                generate_plex_m3u(regions=regions)
            elif service == 'samsungtvplus':
                generate_samsungtvplus_m3u(regions=regions)
            elif service == 'stirr':
                generate_stirr_m3u()
            elif service == 'tubi':
                generate_tubi_m3u()
            elif service == 'roku':
                generate_roku_m3u()  # Added Roku service call
        except Exception as e:
            logging.error(f"Error generating {service} playlist: {e}")
            continue
            
    logging.info("Playlist generation process completed.")
