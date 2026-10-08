"""Plugin installer tests: dry-run payloads, idempotency and safe uninstall. No tenant needed.

Run from bulk-access-request/:  python -m pytest plugin/tests -q
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parent
EXAMPLE = ROOT / "config" / "bulk-access.example.json"
sys.path[:0] = [str(PLUGIN), str(ROOT / "core")]

import install  # noqa: E402
import pluginlib as lib  # noqa: E402
import uninstall  # noqa: E402
from bulkaccess import config  # noqa: E402

ME = {"id": "me-123", "name": "admin.user"}


def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"x.{seg}.y"


class FakeTenant:
    """Records every call; answers the few GETs the scripts make."""

    base_url = "https://acme.api.identitynow.com"
    tenant_name = "acme"

    def __init__(self, workflows: list[dict] | None = None):
        self.workflows = workflows or []
        self.calls: list[tuple[str, str, object]] = []

    def token(self) -> str:
        return _jwt({"identity_id": ME["id"]})

    def call(self, method, path, body=None, **_):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/v2025/identities/"):
            return ME
        if method == "GET" and path.startswith("/v2025/workflows?"):
            return self.workflows
        if method in ("POST", "PUT") and "/workflows" in path:
            return {**body, "id": "wf-new" if method == "POST" else path.rsplit("/", 1)[-1]}
        return None

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def tenant(monkeypatch):
    fake = FakeTenant()
    monkeypatch.setattr(lib.Tenant, "from_env", classmethod(lambda cls, env_file=None: fake))
    return fake


def _sections(out: str) -> dict[str, dict]:
    """Split dry-run output into {heading: parsed JSON}."""
    decoder, parts = json.JSONDecoder(), {}
    for chunk in out.split("\n== ")[1:]:
        heading, _, rest = chunk.partition(" ==\n")
        if rest.startswith("{"):
            parts[heading] = decoder.raw_decode(rest)[0]
    return parts


def test_dry_run_prints_every_body_and_changes_nothing(tenant, capsys, tmp_path):
    assert install.main(["--config", str(EXAMPLE), "--dry-run", "--workdir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing was changed." in out
    assert tenant.writes == []                                   # only GETs
    assert not (tmp_path / "public").exists()                    # no files written
    parts = _sections(out)
    wf = parts["Workflow (POST /v2025/workflows)"]
    assert wf["name"] == "ACME Bulk Access Request (Plugin)"
    assert wf["enabled"] is False and wf["trigger"]["type"] == "EXTERNAL"
    assert wf["owner"] == {"type": "IDENTITY", "id": ME["id"], "name": ME["name"]}
    runtime = parts[str(lib.RUNTIME_CONFIG)]
    assert runtime["workflowName"] == wf["name"] and runtime["workflowId"] is None
    assert parts[str(lib.MANIFEST)]["manifest"]["alias"] == "acme-bulk-access"


def test_plugin_workflow_reads_the_trigger_input_the_page_sends():
    cfg = config.load(EXAMPLE)
    steps = lib.plugin_workflow(cfg, "o", None)["definition"]["steps"]
    assert steps["Get Requester"]["attributes"]["id.$"] == "$.trigger.requesterId"
    assert steps["Bulk Approval"]["attributes"]["singleApproverIdentityId.$"] == "$.trigger.approverId"
    assert steps["Bulk Approval"]["attributes"]["name"] == "Bulk access {{$.trigger.inc}}{{$.trigger.partLabel}}"
    assert "Request Access" not in steps                         # dry-run mode requests nothing
    for step in steps.values():
        if step.get("type") == "failure":                        # SailPoint validator needs these (e300)
            assert step["failureName"] and step["description"]


def test_live_mode_requests_per_person_with_the_inc_in_every_comment():
    cfg = config.from_dict({**json.loads(EXAMPLE.read_text()), "mode": "live"})
    loop = lib.plugin_workflow(cfg, "o", None)["definition"]["steps"]["Request Access"]["attributes"]
    assert loop["input.$"] == "$.trigger.people"
    # Inside a loop only $.loop.* resolves, so the loop carries the whole state as its context.
    assert loop["context.$"] == "$"
    manage = loop["steps"]["Manage Access"]["attributes"]
    assert manage["requestedItems.$"] == "$.loop.context.trigger.items"
    assert manage["comments"].startswith("{{$.loop.context.trigger.inc}} | Bulk access request by "
                                         "{{$.loop.context.getRequester.attributes.displayName}}")
    assert "{{$.trigger" not in manage["comments"]


def test_install_creates_then_updates_by_name(tenant, tmp_path, capsys):
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    assert [(m, p) for m, p, _ in tenant.writes] == [("POST", "/v2025/workflows")]
    written = json.loads((tmp_path / lib.RUNTIME_CONFIG).read_text())
    assert written["workflowId"] == "wf-new"
    assert json.loads((tmp_path / lib.MANIFEST).read_text())["manifest"]["slots"] == [{"slotId": "full-page"}]

    tenant.calls.clear()
    tenant.workflows = [{"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)", "enabled": True}]
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    writes = [(m, p) for m, p, _ in tenant.writes]
    assert writes == [("PATCH", "/v2025/workflows/wf-1"), ("PUT", "/v2025/workflows/wf-1")]   # disable, then update
    assert tenant.writes[-1][2]["enabled"] is False
    assert json.loads((tmp_path / lib.RUNTIME_CONFIG).read_text())["workflowId"] == "wf-1"


def test_committed_runtime_config_is_the_neutral_example():
    committed = json.loads((PLUGIN / lib.RUNTIME_CONFIG).read_text())
    assert committed == lib.runtime_config(config.load(EXAMPLE))   # regenerate it if the example changes
    assert committed["workflowId"] is None


def test_runtime_config_carries_the_limits_and_temporary_access_settings():
    runtime = lib.runtime_config(config.load(EXAMPLE))
    assert runtime["peopleMax"] is None                          # no people limit by default
    assert runtime["partSize"] == config.LOOP_MAX == 250
    assert runtime["temporary"] == {"enabled": True, "allow": ["duration", "endDate"],
                                    "units": ["HOURS", "DAYS", "WEEKS", "MONTHS"], "maxDays": None}
    cfg = config.from_dict({**json.loads(EXAMPLE.read_text()),
                            "people": {"max": 600, "partSize": 100},
                            "temporaryAccess": {"enabled": False, "allow": ["duration"], "units": ["DAYS"], "maxDays": 90}})
    runtime = lib.runtime_config(cfg)
    assert (runtime["peopleMax"], runtime["partSize"]) == (600, 100)
    assert runtime["temporary"] == {"enabled": False, "allow": ["duration"], "units": ["DAYS"], "maxDays": 90}
    json.dumps(runtime)                                          # plain JSON (no tuples)


def test_committed_manifest_matches_the_example_config():
    assert json.loads((PLUGIN / lib.MANIFEST).read_text()) == lib.manifest(config.load(EXAMPLE))


def test_another_tenant_gets_its_own_names_everywhere():
    cfg = config.from_dict({**json.loads(EXAMPLE.read_text()), "prefix": "ACME",
                            "plugin": {"alias": "acme-bulk", "displayName": "ACME Bulk Access"}})
    assert lib.runtime_config(cfg)["workflowName"] == "ACME Bulk Access Request (Plugin)"
    assert lib.manifest(cfg)["manifest"]["alias"] == "acme-bulk"
    assert lib.manifest(cfg)["manifest"]["name"] == {"en": "ACME Bulk Access"}


def test_uninstall_only_touches_our_objects():
    cfg = config.load(EXAMPLE)
    ours = {"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)"}
    plugin = {"id": "p-1", "alias": "acme-bulk-access", "name": "ACME Bulk Access Request"}
    assert uninstall.plan(cfg, ours, plugin) == [
        ("workflow", "wf-1", "ACME Bulk Access Request (Plugin)"),
        ("plugin", "p-1", "ACME Bulk Access Request (alias acme-bulk-access)"),
    ]
    with pytest.raises(lib.TenantError, match="Refusing"):
        uninstall.plan(cfg, {"id": "x", "name": "ACME Bulk Access Request"}, None)   # the Launcher's workflow
    with pytest.raises(lib.TenantError, match="Refusing"):
        uninstall.plan(cfg, None, {"id": "p", "alias": "acme-bulk-access", "name": "Someone else's plugin"})


def test_uninstall_asks_before_deleting(tenant, monkeypatch, capsys):
    tenant.workflows = [{"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)"}]
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert uninstall.main(["--config", str(EXAMPLE)]) == 1
    assert tenant.writes == []
    assert uninstall.main(["--config", str(EXAMPLE), "--yes"]) == 0
    assert [(m, p) for m, p, _ in tenant.writes] == [("DELETE", "/v2025/workflows/wf-1")]


def test_deploy_keeps_the_plugin_private_on_every_update(tenant, tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: None)              # npm run build
    monkeypatch.setattr(lib, "sail", lambda cfg, args, workdir, **k: calls.append(args) or type("P", (), {"stdout": ""})())
    monkeypatch.setattr(lib, "find_plugin", lambda cfg, workdir: {"id": "p-1", "alias": cfg.plugin_alias})
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path), "--deploy"]) == 0
    # push-manifest replaces the whole manifest, visibility included
    assert calls == [["ui-plugins", "push-manifest", "--private"], ["ui-plugins", "upload"]]
    calls.clear()
    monkeypatch.setattr(lib, "find_plugin", lambda cfg, workdir: None)
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path), "--deploy", "--public"]) == 0
    assert calls[0] == ["ui-plugins", "create"]


def test_deploy_visibility_defaults_to_the_config(tenant, tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(lib, "sail", lambda cfg, args, workdir, **k: calls.append(args) or type("P", (), {"stdout": ""})())
    monkeypatch.setattr(lib, "find_plugin", lambda cfg, workdir: {"id": "p-1", "alias": cfg.plugin_alias})
    public_cfg = tmp_path / "public.json"
    data = json.loads(EXAMPLE.read_text())
    public_cfg.write_text(json.dumps({**data, "plugin": {**data["plugin"], "public": True}}))
    assert install.main(["--config", str(public_cfg), "--workdir", str(tmp_path), "--deploy"]) == 0
    assert calls[0] == ["ui-plugins", "push-manifest"]                       # plugin.public: true
    calls.clear()
    assert install.main(["--config", str(public_cfg), "--workdir", str(tmp_path), "--deploy", "--private"]) == 0
    assert calls[0] == ["ui-plugins", "push-manifest", "--private"]          # the flag wins
    with pytest.raises(SystemExit):
        install.main(["--config", str(public_cfg), "--public", "--private"])
