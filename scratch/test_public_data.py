import gzip
import json
import numpy as np
import matplotlib.pyplot as plt
import sys
sys.path.insert(0, '/Users/pulkitpareek18/Desktop/AERIS/ml/src')
from aeris_ml.preprocessing import extract_cir

path = '/Users/pulkitpareek18/Desktop/AERIS/datasets/raw/presence_movement/260-6.csi.json.gz'

samples = []
timestamps = []

try:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            if i > 100: # Just take 100 packets to test the math
                break
            if not line.strip():
                continue
            item = json.loads(line)
            timestamps.append(float(item["t"]))
            subcarriers = []
            for subcarrier in item["csi"]:
                links = []
                for value in subcarrier:
                    links.append(complex(float(value["r"]), float(value["i"])))
                subcarriers.append(links)
            # subcarriers is [30, 3] usually. We want [link, subcarrier] -> [3, 30]
            arr = np.asarray(subcarriers, dtype=np.complex64).T
            samples.append(arr)
except EOFError:
    pass
except Exception as e:
    print("Stopped reading due to:", e)

if not samples:
    print("No valid samples found!")
    sys.exit(1)

csi = np.asarray(samples) # [time, link, subcarrier]
print(f"Loaded {csi.shape[0]} packets of shape {csi.shape[1:]}")

# Run extract_cir
pdp = extract_cir(csi, apply_window=True)
print("CIR Extracted. Shape:", pdp.shape)

plt.figure(figsize=(10, 6))
# Plot the first link, first packet
plt.plot(pdp[0, 0, :])
plt.title("Real Public Data: Power Delay Profile (Intel 5300 - 30 subcarriers)")
plt.xlabel("Delay Bin")
plt.ylabel("Signal Strength (Echo Power)")
plt.grid(True)
out_path = '/Users/pulkitpareek18/.gemini/antigravity/brain/339802af-69ce-4c31-86bc-82d5e7282aa9/real_data_cir.png'
plt.savefig(out_path)
print(f"Saved plot to {out_path}")
