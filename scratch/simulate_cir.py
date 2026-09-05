import numpy as np
import matplotlib.pyplot as plt
import sys
import os

sys.path.insert(0, '/Users/pulkitpareek18/Desktop/AERIS/ml/src')
from aeris_ml.preprocessing import extract_cir

# Simulation parameters
num_subcarriers = 64
subcarrier_spacing = 312.5e3  # 312.5 kHz for WiFi 20MHz
frequencies = np.arange(num_subcarriers) * subcarrier_spacing

num_time_steps = 100
t = np.linspace(0, 2, num_time_steps) # 2 seconds of data

# Simulate CSI: H(f, t) = sum_p a_p * exp(-j * 2 * pi * f * tau_p(t))
# Path 1: Static Wall (Strong reflection)
a_wall = 1.0
tau_wall = 50e-9  # 50 ns delay (approx 15 meters round trip)

# Path 2: Moving Person (Weak reflection, oscillating delay)
a_person = 0.5
tau_person = 100e-9 + 20e-9 * np.sin(2 * np.pi * 1.0 * t) # Moving between 80ns and 120ns

csi = np.zeros((num_time_steps, 1, num_subcarriers), dtype=complex)

for i in range(num_time_steps):
    # Sum of paths for each subcarrier
    h_wall = a_wall * np.exp(-1j * 2 * np.pi * frequencies * tau_wall)
    h_person = a_person * np.exp(-1j * 2 * np.pi * frequencies * tau_person[i])
    csi[i, 0, :] = h_wall + h_person
    
# Add some noise
csi += 0.05 * (np.random.randn(*csi.shape) + 1j * np.random.randn(*csi.shape))

# Run our Phase 1 math
pdp = extract_cir(csi, apply_window=True)

# Plotting
plt.figure(figsize=(10, 6))
# Plot the first time step to show the peaks
plt.plot(pdp[0, 0, :])
plt.title("Power Delay Profile (CIR) - Snapshot")
plt.xlabel("Delay Bin (Distance)")
plt.ylabel("Signal Strength (Echo Power)")
plt.grid(True)

output_path = '/Users/pulkitpareek18/Desktop/AERIS/scratch/cir_simulation.png'
plt.savefig(output_path)
print(f"Plot saved to {output_path}")
