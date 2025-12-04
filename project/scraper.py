import requests
import os
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

FORMAT = 'gen9ou'
SAVE_FOLDER = 'gen9ou_replays'
os.makedirs(SAVE_FOLDER, exist_ok=True)

API_URL = f'https://replay.pokemonshowdown.com/search.json?format={FORMAT}'
MAX_RESULTS = 51
MAX_WORKERS = 8  # Adjust for your machine/network

def get_oldest_uploadtime(folder):
    upload_times = []
    for fname in os.listdir(folder):
        if fname.endswith('.json'):
            try:
                with open(os.path.join(folder, fname), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and 'uploadtime' in data:
                    upload_times.append(data['uploadtime'])
            except Exception:
                continue
    if upload_times:
        return min(upload_times)
    else:
        return None

def download_and_save_replay(replay):
    slug = replay['id']
    fpath = os.path.join(SAVE_FOLDER, f'{slug}.json')
    if os.path.exists(fpath):
        return 0
    json_url = f'https://replay.pokemonshowdown.com/{slug}.json'
    try:
        replay_json = requests.get(json_url, timeout=10)
        if replay_json.status_code == 200:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(replay_json.text)
            # Polite delay is split across threads, so keep it low here
            time.sleep(0.05)
            print(f'Saved: {slug}')
            return 1
        else:
            print(f'Failed: {slug}')
            return 0
    except Exception as e:
        print(f'Error: {slug} — {e}')
        return 0

before = get_oldest_uploadtime(SAVE_FOLDER)
print(before)
total_saved = 0

while True:
    url = API_URL if before is None else f'{API_URL}&before={before}'
    print(f'Requesting: {url}')
    response = requests.get(url)
    if response.status_code != 200:
        print('Failed to retrieve:', response.status_code)
        break
    batch = response.json()
    replays = batch
    # Download JSONs in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(download_and_save_replay, replay) for replay in replays]
        for future in as_completed(futures):
            total_saved += future.result()
    if len(replays) < MAX_RESULTS:
        break
    before = replays[-1]['uploadtime']

print(f'Saved {total_saved} replays into {SAVE_FOLDER}/')
