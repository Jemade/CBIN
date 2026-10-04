"""Observed software inventory, separate from operational adapter registration."""

import json
from importlib.resources import files


def catalogue():
    return json.loads(files("cbin.connectors").joinpath("catalogue.json").read_text())


def software_by_id(software_id):
    return next((row for row in catalogue()["items"] if row["id"] == software_id), None)
