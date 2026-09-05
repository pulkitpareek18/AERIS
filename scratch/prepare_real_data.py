import gzip
import json
import csv
import numpy as np
import sys
import os
sys.path.insert(0, '/Users/pulkitpareek18/Desktop/AERIS/ml/src')
from aeris_ml.preprocessing import extract_cir

base_dir = '/Users/pulkitpareek18/Desktop/AERIS/datasets/raw/presence_movement'
csi_file = os.path.join(base_dir, '260-6.csi.json.gz')
annot_file = os.path.join(base_dir, 'annotations.csv')

# Load annotations
# Format: id,room,label,begin_time,end_time,oid,device_id
annotations = []
if os.path.exists(annot_file):
    with open(annot_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['room'] == '260':
                annotations.append({
                    'label': row['label'],
                    'begin': float(row['begin_time']),
                    'end': float(row['end_time'])
                })

print(f"Loaded {len(annotations)} annotations for room 260")

def get_label_for_timestamp(t):
    for ann in annotations:
        if ann['begin'] <= t <= ann['end']:
            if ann['label'] in ['Mobile', 'Stationary', 'Enter', 'Exit', 'Approach']:
                return 1.0 # 1 person
            elif ann['label'] in ['Gone', 'Departure']:
                return 0.0 # 0 people
    return 0.0 # default to 0

# Parse a small subset of CSI for training (e.g., 2000 packets)
X_list = []
Y_list = []

print("Parsing CSI and extracting CIR...")
try:
    with gzip.open(csi_file, "rt", encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            if i >= 2000:
                break
            if not line.strip():
                continue
            item = json.loads(line)
            t = float(item["t"])
            
            subcarriers = []
            for subcarrier in item["csi"]:
                links = []
                for value in subcarrier:
                    links.append(complex(float(value["r"]), float(value["i"])))
                subcarriers.append(links)
                
            arr = np.asarray(subcarriers, dtype=np.complex64).T # [3, 30]
            
            # Extract CIR for this single packet
            # Input to extract_cir needs to be [time, link, subcarrier]
            arr_expanded = np.expand_dims(arr, axis=0) # [1, 3, 30]
            pdp = extract_cir(arr_expanded, apply_window=True) # [1, 3, 30]
            
            X_list.append(pdp[0]) # [3, 30]
            Y_list.append(get_label_for_timestamp(t))
            
            if i % 500 == 0:
                print(f"Processed {i} packets...")
                
except EOFError:
    pass

X = np.array(X_list, dtype=np.float32)
Y = np.array(Y_list, dtype=np.float32)

print(f"Dataset shape X: {X.shape}, Y: {Y.shape}")

out_path = '/Users/pulkitpareek18/Desktop/AERIS/datasets/processed/presence_movement_train.npz'
os.makedirs(os.path.dirname(out_path), exist_ok=True)
np.savez_compressed(out_path, X=X, Y=Y)
print(f"Saved real dataset to {out_path}")
