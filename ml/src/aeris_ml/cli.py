"""Command line interface for AERIS ML dataset ingestion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aeris_ml.datasets.aeris_pcap import AerisPcapDataset
from aeris_ml.datasets.aril import ArilDataset
from aeris_ml.datasets.csi_bench import CsiBenchDataset
from aeris_ml.datasets.csi_traces import CsiTracesDataset
from aeris_ml.datasets.operanet import OperanetDataset
from aeris_ml.datasets.presence_movement import PresenceMovementDataset
from aeris_ml.datasets.registry import get_default_registry
from aeris_ml.datasets.sensefi import SenseFiProcessedDataset


def _loader(dataset_id: str, input_path: str | Path, **options):
    if dataset_id in {"aeris", "aeris_native", "aeris_recorder"}:
        return AerisPcapDataset(input_path, **options)
    if dataset_id == "presence_movement":
        return PresenceMovementDataset(input_path, **options)
    if dataset_id == "csi_traces":
        return CsiTracesDataset(input_path, **options)
    if dataset_id == "csi_bench":
        return CsiBenchDataset(input_path, **options)
    if dataset_id == "operanet":
        return OperanetDataset(input_path, **options)
    if dataset_id == "aril":
        return ArilDataset(input_path, **options)
    if dataset_id.startswith("sensefi_"):
        return SenseFiProcessedDataset(input_path, dataset_id=dataset_id, **options)
    raise ValueError(
        f"No loader is registered for '{dataset_id}'. Use `datasets list` to inspect loader status."
    )


def _json(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def cmd_datasets_list(_args: argparse.Namespace) -> int:
    registry = get_default_registry()
    print(f"{'dataset_id':28} {'status':16} official_name")
    print("-" * 88)
    for item in registry.list():
        print(f"{item.dataset_id:28} {item.loader_implementation_status:16} {item.official_name}")
    return 0


def cmd_datasets_info(args: argparse.Namespace) -> int:
    registry = get_default_registry()
    try:
        item = registry.get(args.dataset_id)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(_json(item.to_dict()))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        loader = _loader(args.dataset, args.input, max_packets=args.max_packets)
        reports = []
        count = 0
        for record in loader.iter_recordings():
            if args.max_recordings is not None and count >= args.max_recordings:
                break
            report = loader.validate_recording(record)
            reports.append(
                {
                    "session_id": record.session_id,
                    "dataset_id": record.dataset_id,
                    "label": record.label.value,
                    "shape": list(record.csi.shape),
                    **report.to_dict(),
                }
            )
            count += 1
        print(_json({"recordings_validated": count, "reports": reports}))
        return 0 if all(report["ok"] for report in reports) else 1
    except Exception as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 2


def cmd_prepare(args: argparse.Namespace) -> int:
    try:
        loader = _loader(args.dataset, args.input, max_packets=args.max_packets)
        summary = loader.convert(
            args.output,
            target_rate_hz=args.target_rate_hz,
            window_s=args.window_s,
            step_s=args.step_s,
            normalize=not args.no_normalize,
            max_recordings=args.max_recordings,
        )
        print(_json(summary.to_dict()))
        return 0
    except Exception as exc:
        print(f"Prepare failed: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aeris-ml",
        description="AERIS Wi-Fi CSI dataset registry, validation, and preprocessing CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    datasets = sub.add_parser("datasets", help="Inspect the dataset registry")
    datasets_sub = datasets.add_subparsers(dest="datasets_command", required=True)
    datasets_list = datasets_sub.add_parser("list", help="List registered datasets")
    datasets_list.set_defaults(func=cmd_datasets_list)
    datasets_info = datasets_sub.add_parser("info", help="Show one dataset registry entry")
    datasets_info.add_argument("dataset_id")
    datasets_info.set_defaults(func=cmd_datasets_info)

    validate = sub.add_parser("validate", help="Validate recordings without writing processed files")
    validate.add_argument("--dataset", required=True, help="Dataset ID, for example aeris or presence_movement")
    validate.add_argument("--input", required=True, help="Dataset root or AERIS session directory")
    validate.add_argument("--max-recordings", type=int, default=None, help="Stop after N recordings")
    validate.add_argument("--max-packets", type=int, default=None, help="Only for large JSON-lines test runs")
    validate.set_defaults(func=cmd_validate)

    prepare = sub.add_parser("prepare", help="Convert a dataset into canonical NPZ+JSON windows")
    prepare.add_argument("--dataset", required=True, help="Dataset ID, for example aeris or presence_movement")
    prepare.add_argument("--input", required=True, help="Dataset root or AERIS session directory")
    prepare.add_argument("--output", required=True, help="Processed output directory")
    prepare.add_argument("--target-rate-hz", type=float, default=50.0)
    prepare.add_argument("--window-s", type=float, default=2.5)
    prepare.add_argument("--step-s", type=float, default=0.5)
    prepare.add_argument("--max-recordings", type=int, default=None, help="Stop after N recordings")
    prepare.add_argument("--max-packets", type=int, default=None, help="Only for large JSON-lines test runs")
    prepare.add_argument("--no-normalize", action="store_true", help="Skip median/IQR normalization")
    prepare.set_defaults(func=cmd_prepare)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
