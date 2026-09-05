from __future__ import annotations

from aeris_ml.cli import main
from aeris_ml.datasets.registry import get_default_registry


def test_registry_contains_required_datasets() -> None:
    registry = get_default_registry()
    ids = set(registry.ids())
    assert "aeris" in ids
    assert "presence_movement" in ids
    assert "csi_traces" in ids
    assert "csi_bench" in ids
    assert "sensefi_ut_har" in ids
    assert "sensefi_ntu_fi_har" in ids
    assert "sensefi_ntu_fi_humanid" in ids
    assert "sensefi_widar" in ids
    assert "operanet" in ids
    assert "aril" in ids
    assert "broadcom_nexmon_examples" in ids


def test_cli_dataset_commands(capsys) -> None:
    assert main(["datasets", "list"]) == 0
    out = capsys.readouterr().out
    assert "presence_movement" in out

    assert main(["datasets", "info", "aeris"]) == 0
    out = capsys.readouterr().out
    assert '"dataset_id": "aeris"' in out
