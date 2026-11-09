"""The release zip that HACS installs: since Home Assistant 2026.11 HACS is built in as Marketplace,
and the download number it shows is the download count of that release asset (review 2026-11-08)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run_steps() -> str:
    job = _workflow()["jobs"]["zip"]
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_hacs_installs_the_release_zip():
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["zip_release"] is True
    assert hacs["filename"] == "thriftherm.zip"
    assert hacs["name"] == "Thriftherm" and hacs["homeassistant"]


def test_the_zip_is_built_only_for_a_published_release_of_the_public_repository():
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert triggers == {"release": {"types": ["published"]}}
    job = workflow["jobs"]["zip"]
    assert job["if"] == "github.repository == 'alpha-uniform/thriftherm'"
    assert job["permissions"] == {"contents": "write"}


def test_the_zip_holds_the_integration_at_its_root_without_bytecode():
    steps = _run_steps()
    assert "cd custom_components/thriftherm" in steps
    # '*/__pycache__/*' alone let the top-level __pycache__/ folder entry in (seen 2026-11-08)
    assert "zip -r ../../thriftherm.zip . -x '*__pycache__*' '*.pyc'" in steps
    assert "gh release upload" in steps and "thriftherm.zip" in steps


def test_a_release_whose_tag_differs_from_the_manifest_version_fails():
    steps = _run_steps()
    assert "manifest.json" in steps
    assert '"${GITHUB_REF_NAME#v}"' in steps and "exit 1" in steps
    # the check comes before the upload, so a wrong tag never gets a zip
    assert steps.index("exit 1") < steps.index("gh release upload")


def test_every_action_the_release_uses_is_pinned_to_a_full_version():
    for step in _workflow()["jobs"]["zip"]["steps"]:
        if "uses" in step:
            _, _, version = step["uses"].partition("@")
            assert version.startswith("v") and version.count(".") == 2, step["uses"]
