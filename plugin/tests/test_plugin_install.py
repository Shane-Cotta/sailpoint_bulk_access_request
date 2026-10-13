"""Plugin installer tests: dry-run payloads, idempotency and safe uninstall. No tenant needed.

Run from bulk-access-request/:  python -m pytest plugin/tests -q
"""

from __future__ import annotations

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
LAUNCHER = {"id": "ln-1", "name": "ACME Bulk Access Request"}
ACCESS = {"id": "la-1", "name": "ACME Bulk Access Request - Launcher Access"}


def _config(tmp_path: Path, **plugin) -> str:
    """The example config with `plugin` keys overridden (e.g. submit="launcher"), as a file."""
    data = json.loads(EXAMPLE.read_text())
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**data, "plugin": {**data["plugin"], **plugin}}))
    return str(path)


class FakeTenant:
    """Records every call; answers the GETs the scripts make."""

    base_url = "https://acme.api.identitynow.com"
    tenant_name = "acme"

    def __init__(self):
        self.workflows: list[dict] = []
        self.launchers = [LAUNCHER]
        self.access_profiles = [ACCESS]
        self.anyone_by_anyone = True
        self.calls: list[tuple[str, str, object]] = []

    def call(self, method, path, body=None, **_):
        self.calls.append((method, path, body))
        if method != "GET":
            return None
        if path.startswith("/workflows/v1?"):
            return self.workflows
        if path.startswith("/launchers/v1?"):
            return {"items": self.launchers}
        if path == "/access-request-config/v1":
            return {"requestOnBehalfOfConfig": {"allowRequestOnBehalfOfAnyoneByAnyone": self.anyone_by_anyone},
                    "entitlementRequestConfig": {"grantRequestApprovalSchemes": "sourceOwner"}}
        if path.startswith("/requestable-objects/v1?"):
            return [{"id": "ap-open", "type": "ACCESS_PROFILE", "name": "ACME Open"},
                    {"id": "ap-owner", "type": "ACCESS_PROFILE", "name": "ACME Owned"}] if "offset=0" in path else []
        if path.startswith("/entitlements/v1?"):
            return [{"id": "ent-1", "name": "ACME Group"}] if "offset=0" in path else []
        if path.startswith("/entitlements/v1/"):
            return {"accessRequestConfig": {"approvalSchemes": []}}
        if path.startswith("/access-profiles/v1?filters=id"):
            return [{"id": "ap-open", "accessRequestConfig": {"approvalSchemes": []}},
                    {"id": "ap-owner", "accessRequestConfig": {"approvalSchemes": [{"approverType": "OWNER"}]}}]
        if path.startswith("/access-profiles/v1?filters=name"):
            return self.access_profiles
        return []

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


def test_dry_run_in_direct_mode_writes_nothing_and_creates_nothing(tenant, capsys, tmp_path):
    assert install.main(["--config", str(EXAMPLE), "--dry-run", "--workdir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing was changed." in out and "the plugin files the access requests itself" in out
    assert tenant.writes == [] and not (tmp_path / "public").exists()
    runtime = _sections(out)[str(lib.RUNTIME_CONFIG)]
    assert (runtime["submit"], runtime["launcherId"], runtime["launcherAccessId"]) == ("direct", None, "la-1")
    assert runtime["noApproval"] == ["ap-open"]              # its own schemes are empty; the entitlement has a default
    assert "only holders of 'ACME Bulk Access Request - Launcher Access' may submit" in out
    assert not [h for h in _sections(out) if h.startswith("Workflow")]


def test_direct_mode_falls_back_to_the_launcher_when_users_cant_request_for_others(tenant, capsys, tmp_path):
    tenant.anyone_by_anyone = False
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    assert "doesn't let users request access for others" in capsys.readouterr().out
    written = json.loads((tmp_path / lib.RUNTIME_CONFIG).read_text())
    assert (written["submit"], written["launcherId"]) == ("launcher", "ln-1")
    # Without the Launcher there is nothing to fall back on.
    data = json.loads(EXAMPLE.read_text())
    plugin_only = tmp_path / "plugin-only.json"
    plugin_only.write_text(json.dumps({**data, "deployments": {"launcher": False, "plugin": True}}))
    with pytest.raises(lib.TenantError, match="doesn't let users request access for others"):
        install.main(["--config", str(plugin_only), "--workdir", str(tmp_path)])


def test_install_in_launcher_mode_looks_the_launcher_up(tenant, tmp_path, capsys):
    cfg_path = _config(tmp_path, submit="launcher")
    assert install.main(["--config", cfg_path, "--workdir", str(tmp_path)]) == 0
    assert tenant.writes == []                                   # nothing created in the tenant
    written = json.loads((tmp_path / lib.RUNTIME_CONFIG).read_text())
    assert (written["submit"], written["launcherId"], written["launcherAccessId"]) == ("launcher", "ln-1", "la-1")
    assert "Launcher Access" in json.loads((tmp_path / lib.MANIFEST).read_text())["manifest"]["description"]["en"]


def test_an_old_plugin_workflow_is_left_alone_with_a_note(tenant, tmp_path, capsys):
    tenant.workflows = [{"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)", "enabled": False}]
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    assert tenant.writes == []
    assert "is no longer used" in capsys.readouterr().out


def test_install_in_launcher_mode_needs_the_launcher(tenant, tmp_path):
    tenant.launchers = []
    with pytest.raises(lib.TenantError, match="Install the Launcher deployment first"):
        install.main(["--config", _config(tmp_path, submit="launcher"), "--workdir", str(tmp_path)])
    assert not (tmp_path / "public").exists()


def test_without_the_launcher_access_profile_anyone_who_sees_the_plugin_may_submit(tenant, tmp_path, capsys):
    tenant.access_profiles = []
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    assert "anyone who can open the plugin may submit" in capsys.readouterr().out
    assert json.loads((tmp_path / lib.RUNTIME_CONFIG).read_text())["launcherAccessId"] is None


def test_committed_runtime_config_is_the_neutral_example():
    committed = json.loads((PLUGIN / lib.RUNTIME_CONFIG).read_text())
    assert committed == lib.runtime_config(config.load(EXAMPLE))   # regenerate it if the example changes
    assert committed["launcherId"] is None and committed["launcherAccessId"] is None and committed["noApproval"] == []


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


def test_runtime_config_temporary_access_follows_the_submit_route():
    data = json.loads(EXAMPLE.read_text())
    end_date_only = {"enabled": True, "allow": ["endDate"], "units": ["DAYS"], "maxDays": None}
    # Direct requests carry an exact removal date: every configured mode works.
    cfg = config.from_dict({**data, "temporaryAccess": end_date_only})
    assert lib.runtime_config(cfg)["temporary"] == end_date_only
    # Through the Launcher, temporary access travels in the form's duration fields: with no durations
    # offered there are none, so the plugin offers permanent access only.
    cfg = config.from_dict({**data, "temporaryAccess": end_date_only, "plugin": {**data["plugin"], "submit": "launcher"}})
    assert lib.runtime_config(cfg)["temporary"]["enabled"] is False


def test_runtime_config_carries_the_approvals_settings():
    runtime = lib.runtime_config(config.load(EXAMPLE))
    assert runtime["approvals"] == {"enabled": True, "concurrency": 4, "useBulkEndpoint": "auto", "maxRows": 5000,
                                    "showOther": False, "denyCommentRequired": True}
    cfg = config.from_dict({**json.loads(EXAMPLE.read_text()),
                            "approvals": {"enabled": False, "concurrency": 2, "useBulkEndpoint": "never",
                                          "maxRows": 1000, "showOther": True, "denyCommentRequired": False}})
    assert lib.runtime_config(cfg)["approvals"] == {"enabled": False, "concurrency": 2, "useBulkEndpoint": "never",
                                                    "maxRows": 1000, "showOther": True, "denyCommentRequired": False}
    # The page reads `enabled` as the derived value: no plugin deployment, no Approvals tab.
    cfg = config.from_dict({**json.loads(EXAMPLE.read_text()), "deployments": {"launcher": True, "plugin": False}})
    assert lib.runtime_config(cfg)["approvals"]["enabled"] is False
    json.dumps(runtime)


def test_committed_manifest_matches_the_example_config():
    assert json.loads((PLUGIN / lib.MANIFEST).read_text()) == lib.manifest(config.load(EXAMPLE))


def test_another_tenant_gets_its_own_names_everywhere():
    cfg = config.from_dict({**json.loads(EXAMPLE.read_text()), "prefix": "ACME",
                            "plugin": {"alias": "acme-bulk", "displayName": "ACME Bulk Access", "submit": "launcher"}})
    assert lib.runtime_config(cfg, "ln-9")["launcherId"] == "ln-9"
    assert lib.runtime_config(cfg)["launcherAccessName"] == "ACME Bulk Access Request - Launcher Access"
    direct = config.from_dict({**json.loads(EXAMPLE.read_text()), "plugin": {"submit": "direct"}})
    assert lib.runtime_config(direct, "ln-9")["launcherId"] is None          # direct mode: no Launcher needed
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
    tenant.workflows = [{"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)"}]   # left by an older version
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert uninstall.main(["--config", str(EXAMPLE)]) == 1
    assert tenant.writes == []
    assert uninstall.main(["--config", str(EXAMPLE), "--yes"]) == 0
    assert [(m, p) for m, p, _ in tenant.writes] == [("DELETE", "/workflows/v1/wf-1")]


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
