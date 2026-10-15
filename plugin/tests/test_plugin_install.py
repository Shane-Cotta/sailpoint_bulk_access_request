"""Plugin installer tests: dry-run payloads, idempotency and safe uninstall. No tenant needed.

Run from bulk-access-request/:  python -m pytest plugin/tests -q
"""

from __future__ import annotations

import json
import re
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
from bulkaccess import config, definitions  # noqa: E402

ME = {"id": "me-123", "name": "admin.user"}
LAUNCHER = {"id": "ln-1", "name": "ACME Bulk Access Request"}
ACCESS = {"id": "la-1", "name": "ACME Bulk Access Request - Launcher Access"}
ROLE_NAME = "ACME Bulk Access Request - Submit Access"


def _role(cfg_path=EXAMPLE, launcher_access=ACCESS) -> dict:
    """The Submit Access role as installed and up to date (so the installer leaves it alone)."""
    return {**definitions.submit_role(config.load(cfg_path), ME["id"], launcher_access), "id": "role-1"}


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
        self.roles = [_role()]
        self.holders: dict[str, list[dict]] = {}       # item ID -> identities holding it (search)
        self.anyone_by_anyone = True
        self.calls: list[tuple[str, str, object]] = []

    def me(self):
        return ME

    def call(self, method, path, body=None, **_):
        self.calls.append((method, path, body))
        if method == "POST" and path.startswith("/search/v1"):
            item = re.search(r'id:"([^"]+)"', body["query"]["query"]).group(1)
            return self.holders.get(item, [])
        if method == "POST" and path == "/roles/v1":
            return {**body, "id": "role-new"}
        if method != "GET":
            return None
        if path.startswith("/roles/v1?filters=name"):
            return self.roles
        if path.startswith("/workflows/v1?"):
            return self.workflows
        if path.startswith("/launchers/v1?"):
            return {"items": self.launchers}
        if path == "/access-request-config/v1":
            return {"requestOnBehalfOfConfig": {"allowRequestOnBehalfOfAnyoneByAnyone": self.anyone_by_anyone},
                    "entitlementRequestConfig": {"grantRequestApprovalSchemes": "sourceOwner"}}
        if path.startswith("/requestable-objects/v1?"):
            return [{"id": "ap-open", "type": "ACCESS_PROFILE", "name": "ACME Open"},
                    {"id": "ap-owner", "type": "ACCESS_PROFILE", "name": "ACME Owned"},
                    {"id": "role-1", "type": "ROLE", "name": ROLE_NAME}] if "offset=0" in path else []
        if path.startswith("/entitlements/v1?"):
            return [{"id": "ent-1", "name": "ACME Group"}] if "offset=0" in path else []
        if path.startswith("/entitlements/v1/"):
            return {"accessRequestConfig": {"approvalSchemes": []}}
        if path.startswith("/access-profiles/v1?filters=id"):
            return [{"id": "ap-open", "accessRequestConfig": {"approvalSchemes": []}},
                    {"id": "la-1", "accessRequestConfig": {"approvalSchemes": []}},
                    {"id": "ap-owner", "accessRequestConfig": {"approvalSchemes": [{"approverType": "OWNER"}]}}]
        if path.startswith("/access-profiles/v1?filters=name"):
            return self.access_profiles
        return []

    @property
    def writes(self):
        """Every call that changes something (a search is a POST, but only reads)."""
        return [c for c in self.calls if c[0] != "GET" and not c[1].startswith("/search/")]


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
    assert (runtime["submit"], runtime["launcherId"], runtime["submitAccessId"]) == ("direct", None, "role-1")
    # ap-open's own schemes are empty; the entitlement has a default; the tool's own role is never offered
    assert runtime["noApproval"] == ["ap-open"]
    assert f"only holders of the role '{ROLE_NAME}' may submit" in out
    assert f"Submit Access role unchanged: role-1  {ROLE_NAME} (contains '{ACCESS['name']}')" in out
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
    assert (written["submit"], written["launcherId"], written["submitAccessId"]) == ("launcher", "ln-1", "role-1")
    assert ROLE_NAME in json.loads((tmp_path / lib.MANIFEST).read_text())["manifest"]["description"]["en"]


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


def test_the_submit_access_role_is_created_and_launcher_access_holders_get_it(tenant, tmp_path, capsys):
    tenant.roles = []
    tenant.holders = {"la-1": [{"id": "id-a", "name": "alice"}, {"id": "id-b", "name": "bob"}]}
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    (_, _, role), (_, _, request) = tenant.writes
    assert role == definitions.submit_role(config.load(EXAMPLE), ME["id"], ACCESS)
    assert (role["requestable"], role["accessProfiles"][0]["id"], role["entitlements"]) == (True, "la-1", [])
    assert request["requestedFor"] == ["id-a", "id-b"]
    assert request["requestedItems"][0] | {"comment": ""} == {"type": "ROLE", "id": "role-new", "comment": ""}
    assert "Requested the role for 2 holder(s) of 'ACME Bulk Access Request - Launcher Access': alice, bob" in out
    assert json.loads((tmp_path / lib.RUNTIME_CONFIG).read_text())["submitAccessId"] == "role-new"


def test_the_role_is_patched_only_where_it_differs_and_holders_are_not_asked_twice(tenant, tmp_path, capsys):
    tenant.roles = [{**_role(), "description": "old", "accessRequestConfig": {"approvalSchemes": []}}]
    tenant.holders = {"la-1": [{"id": "id-a", "name": "alice"}], "role-1": [{"id": "id-a", "name": "alice"}]}
    assert install.main(["--config", str(EXAMPLE), "--workdir", str(tmp_path)]) == 0
    [(method, path, ops)] = tenant.writes
    assert (method, path, [op["path"] for op in ops]) == ("PATCH", "/roles/v1/role-1",
                                                           ["/description", "/accessRequestConfig"])
    assert "Submit Access role updated (description, accessRequestConfig)" in capsys.readouterr().out


def test_without_the_launcher_the_role_is_empty_but_launcher_access_holders_still_get_it(tenant, tmp_path, capsys):
    data = json.loads(EXAMPLE.read_text())
    plugin_only = tmp_path / "plugin-only.json"
    plugin_only.write_text(json.dumps({**data, "deployments": {"launcher": False, "plugin": True}}))
    tenant.roles = [_role()]                                     # installed with the Launcher: the profile comes out
    tenant.holders = {"la-1": [{"id": "id-a", "name": "alice"}]}
    assert install.main(["--config", str(plugin_only), "--workdir", str(tmp_path), "--grant", "me"]) == 0
    patch, migrate, grant = tenant.writes
    assert {"op": "replace", "path": "/accessProfiles", "value": []} in patch[2]
    assert (migrate[2]["requestedFor"], grant[2]["requestedFor"]) == (["id-a"], ["me-123"])


def test_a_dry_run_changes_nothing_and_says_what_it_would_do(tenant, tmp_path, capsys):
    tenant.roles = []
    tenant.holders = {"la-1": [{"id": "id-a", "name": "alice"}]}
    assert install.main(["--config", str(EXAMPLE), "--dry-run", "--grant", "me", "--workdir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert tenant.writes == []
    assert "Submit Access role would be created" in out and "would request the role for 1 holder(s)" in out
    assert _sections(out)[str(lib.RUNTIME_CONFIG)]["submitAccessId"] is None


def test_committed_runtime_config_is_the_neutral_example():
    committed = json.loads((PLUGIN / lib.RUNTIME_CONFIG).read_text())
    assert committed == lib.runtime_config(config.load(EXAMPLE))   # regenerate it if the example changes
    assert committed["launcherId"] is None and committed["submitAccessId"] is None and committed["noApproval"] == []


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
    assert lib.runtime_config(cfg)["submitAccessName"] == ROLE_NAME
    direct = config.from_dict({**json.loads(EXAMPLE.read_text()), "plugin": {"submit": "direct"}})
    assert lib.runtime_config(direct, "ln-9")["launcherId"] is None          # direct mode: no Launcher needed
    assert lib.manifest(cfg)["manifest"]["alias"] == "acme-bulk"
    assert lib.manifest(cfg)["manifest"]["name"] == {"en": "ACME Bulk Access"}


def test_uninstall_only_touches_our_objects():
    cfg = config.load(EXAMPLE)
    ours = {"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)"}
    plugin = {"id": "p-1", "alias": "acme-bulk-access", "name": "ACME Bulk Access Request"}
    assert uninstall.plan(cfg, ours, plugin, {"id": "role-1", "name": ROLE_NAME}) == [
        ("role", "role-1", ROLE_NAME),
        ("workflow", "wf-1", "ACME Bulk Access Request (Plugin)"),
        ("plugin", "p-1", "ACME Bulk Access Request (alias acme-bulk-access)"),
    ]
    with pytest.raises(lib.TenantError, match="Refusing"):
        uninstall.plan(cfg, {"id": "x", "name": "ACME Bulk Access Request"}, None)   # the Launcher's workflow
    with pytest.raises(lib.TenantError, match="Refusing"):
        uninstall.plan(cfg, None, {"id": "p", "alias": "acme-bulk-access", "name": "Someone else's plugin"})
    with pytest.raises(lib.TenantError, match="Refusing"):
        uninstall.plan(cfg, None, None, {"id": "r", "name": "ACME Something Else"})


def test_uninstall_asks_before_deleting(tenant, monkeypatch, capsys):
    tenant.workflows = [{"id": "wf-1", "name": "ACME Bulk Access Request (Plugin)"}]   # left by an older version
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert uninstall.main(["--config", str(EXAMPLE)]) == 1
    assert tenant.writes == []
    assert uninstall.main(["--config", str(EXAMPLE), "--yes"]) == 0
    assert [(m, p) for m, p, _ in tenant.writes] == [("DELETE", "/roles/v1/role-1"), ("DELETE", "/workflows/v1/wf-1")]


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
