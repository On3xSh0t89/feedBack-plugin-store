import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location(
    "plugin_store_v041_storelib",
    ROOT / "storelib.py",
)
storelib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storelib
SPEC.loader.exec_module(storelib)


class BrokenLogger:
    def info(self, *args, **kwargs):
        raise KeyError("simulated logging formatter failure")


class Dummy:
    log = BrokenLogger()


def test_manifest_is_v041():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.1"


def test_safe_info_logging_cannot_raise():
    # Store mutations must not become HTTP 500 merely because logging fails.
    storelib.PluginStore._log_info(
        Dummy(),
        "plugin_store_test",
        extra={"store_id": "demo"},
    )


def test_add_store_does_not_use_reserved_logrecord_name_field():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    add_store = src[
        src.index("    def add_store("):
        src.index("    def remove_store(", src.index("    def add_store("))
    ]
    assert '"store_name": config["name"]' in add_store
    assert '"name": config["name"]' not in add_store


def test_store_mutation_info_logs_use_safe_wrapper():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    assert 'self._log_info(\n            "plugin_store_third_party_added"' in src
