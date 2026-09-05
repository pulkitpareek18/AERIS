# Raspberry Pi Deployment Notes

This milestone does not change the Raspberry Pi recorder installation.

Preserved recorder paths and behavior:

- `install.sh` still installs to `$HOME/aeris-recorder`.
- The launcher still starts `aeris_recorder_server.py --serve --host 0.0.0.0 --port 8765`.
- Chromium still opens `http://127.0.0.1:8765` in kiosk mode unless `--windowed` is used.
- Recorder data remains under `$HOME/aeris-data`.
- The ML package is optional and lives beside the recorder under `ml/`.

Future deployment work should add a small inference service or process that reads
a compressed model artifact from a safe deployment directory. Do not bundle raw
datasets, checkpoints under active training, participant databases or API keys
onto the Pi image.
