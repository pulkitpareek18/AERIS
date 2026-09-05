# AERIS Dataset Pipeline

## Responsibilities

The Raspberry Pi recorder captures privacy-preserving AERIS sessions:

- `capture.pcap`
- `metadata.json`
- `labels.csv`
- `quality.json`
- local-only SQLite participant/location metadata

The ML pipeline runs beside the recorder. It validates and converts AERIS and
public CSI datasets into canonical NPZ+JSON records. It does not modify recorder
installation paths, Chromium launch behavior, desktop launchers or Raspberry Pi
configuration.

## Machine Roles

- Mac or workstation: dataset download, preprocessing, public pretraining,
  supervised experiments and model compression.
- Raspberry Pi: CSI recording now; later lightweight inference after model
  export and validation.

## Directory Conventions

```text
datasets/
├── raw/
│   ├── presence_movement/
│   ├── csi_traces/
│   └── ...
└── processed/
    ├── aeris/
    ├── presence_movement/
    └── ...
```

Both directories are ignored by Git. Keep downloaded archives and converted
outputs out of the repository history.

## Registering A New Dataset

1. Add one entry to `ml/configs/datasets.yaml`.
2. Use official pages, repositories or papers for metadata.
3. Set unknown values to `verification_required`.
4. Add a loader only after the source format is verified from documentation or a
   small official sample.
5. Preserve original labels and map only safe labels into the common hierarchy.
6. Add synthetic tests. Do not require full dataset downloads.

## Preparing AERIS Data

```bash
cd ml
python -m aeris_ml.cli validate --dataset aeris --input /home/aeris/aeris-data/sessions
python -m aeris_ml.cli prepare --dataset aeris --input /home/aeris/aeris-data/sessions --output datasets/processed/aeris
```

The AERIS loader reads the existing session format and uses the documented
Nexmon CSI UDP payload layout to extract complex CSI into `[time, link,
subcarrier]`. It also calls the recorder's existing `parse_pcap` where possible
to keep quality metrics aligned with the recorder.

## Privacy

Participant names remain in the local recorder database for operator convenience.
They must not be copied into processed training records. Participant code,
height, body type, gender and clothing can be stored as evaluation metadata, but
they must not become model prediction targets.

## Planned Training Sequence

1. Self-supervised public pretraining across heterogeneous CSI datasets.
2. Multi-task supervised learning on safe activity/motion labels.
3. AERIS fine-tuning on recorder captures.
4. Site calibration for each deployment environment.
5. Model compression and Raspberry Pi deployment.

## Public Loader Notes

The Zenodo presence/movement loader is implemented because the official README
documents line-delimited CSI JSON and `annotations.csv` labels. CSI Traces,
CSI-Bench, OPERAnet and ARIL are scaffolded until official local samples or
format documents are available. This is intentional: the pipeline should fail
clearly instead of silently parsing guessed formats.
