import urllib.request
import json
import os
import subprocess

out_dir = '/Users/pulkitpareek18/Desktop/AERIS/datasets/raw/presence_movement'
os.makedirs(out_dir, exist_ok=True)

url = 'https://zenodo.org/api/records/3677366'
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    
files_to_download = ['annotations.csv', 'README.txt', '260-6.csi.json.gz']

for f in data['files']:
    if f['key'] in files_to_download:
        out_path = os.path.join(out_dir, f['key'])
        if not os.path.exists(out_path):
            print(f"Downloading {f['key']}...")
            urllib.request.urlretrieve(f['links']['self'], out_path)
            print(f"Downloaded {f['key']}")
        else:
            print(f"{f['key']} already exists")

print("Processing dataset...")
cmd = "PYTHONPATH=src python3 -m aeris_ml.cli prepare --dataset presence_movement --input ../datasets/raw/presence_movement --output ../datasets/processed/presence_movement"
subprocess.run(cmd, shell=True, cwd='/Users/pulkitpareek18/Desktop/AERIS/ml', check=True)
print("Dataset processed successfully!")
