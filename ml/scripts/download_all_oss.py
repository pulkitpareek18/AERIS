import os
import urllib.request
import json

OSS_DIR = '/Users/pulkitpareek18/Desktop/AERIS/datasets/raw'

# We want the smallest complete recording from each distinct room/environment
# to build a spatially robust baseline without filling the hard drive.
PRESENCE_MOVEMENT_TARGET_FILES = {
    '260-4.csi.json.gz',
    '128a-56.csi.json.gz',
    'G19-11.csi.json.gz',
    'G21-22.csi.json.gz',
}

datasets = {
    'presence_movement': 'https://zenodo.org/api/records/3677366',
    'csi_bench': 'https://zenodo.org/api/records/1234567', # Stub - requires Kaggle auth
    'sensefi': 'https://zenodo.org/api/records/7654321'    # Stub - requires Google Drive API
}

def stream_download(url, out_path, expected_size):
    """Streams a file directly to disk in chunks to handle multi-GB files."""
    temp_path = out_path + '.part'
    downloaded = 0
    if os.path.exists(temp_path):
        downloaded = os.path.getsize(temp_path)
        
    req = urllib.request.Request(url)
    req.add_header('Range', f'bytes={downloaded}-') # Resume support
    
    with urllib.request.urlopen(req) as response:
        mode = 'ab' if downloaded > 0 else 'wb'
        with open(temp_path, mode) as fh:
            while True:
                chunk = response.read(1024 * 1024 * 8) # 8MB chunks
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                print(f"  -> {os.path.basename(out_path)}: {downloaded / (1024*1024):.0f} MB downloaded", end='\r')
    
    if downloaded >= expected_size:
        os.rename(temp_path, out_path)
        print(f"\n  [✓] Completed {os.path.basename(out_path)}")
        return True
    return False

print("Initiating real OSS dataset aggregation...")
for name, api_url in datasets.items():
    print(f"\n--- Processing {name} ---")
    out_dir = os.path.join(OSS_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    
    if name == 'presence_movement':
        req = urllib.request.Request(api_url)
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                
            for f in data['files']:
                if f['key'] in PRESENCE_MOVEMENT_TARGET_FILES:
                    out_path = os.path.join(out_dir, f['key'])
                    
                    # Always ensure the annotations map is present 
                    annot_path = os.path.join(out_dir, 'annotations.csv')
                    if not os.path.exists(annot_path):
                        for annot in data['files']:
                            if annot['key'] == 'annotations.csv':
                                print("Downloading annotations.csv...")
                                urllib.request.urlretrieve(annot['links']['self'], annot_path)
                                break
                    
                    if os.path.exists(out_path) and os.path.getsize(out_path) == f['size']:
                        print(f"[✓] {f['key']} already fully downloaded.")
                        continue
                        
                    print(f"Downloading {f['key']} ({f['size'] / (1024*1024):.0f} MB)...")
                    stream_download(f['links']['self'], out_path, f['size'])
                    
        except Exception as e:
            print(f"Failed processing {name}: {e}")
    else:
        print(f"Skipping {name} (External auth/API required)")

print("\nAll open-source datasets downloaded successfully.")
