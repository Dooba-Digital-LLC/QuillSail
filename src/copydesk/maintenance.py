"""An operator-controlled gate for safe publication migrations and validation."""

from pathlib import Path


def marker(config):
    return Path(config.data_dir) / "update-maintenance.json"


def active(config):
    return marker(config).exists()
