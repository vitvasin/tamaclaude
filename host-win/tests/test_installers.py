"""hook + statusline installer — เขียน settings.json โดยไม่แตะของเดิม · ใช้ path ชั่วคราวล้วน"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import hook_installer, settings_file, statusline_installer  # noqa: E402

HOOK_CMD = '"C:\\py\\python.exe" -m tamaclaude --hook'


def _settings(tmp_path) -> Path:
    return tmp_path / "settings.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# MARK: - hook installer


def test_hook_install_adds_all_events(tmp_path):
    s = _settings(tmp_path)
    hook_installer.install(HOOK_CMD, s)
    hooks = _read(s)["hooks"]
    for event in hook_installer.EVENTS:
        assert event in hooks
        cmd = hooks[event][0]["hooks"][0]["command"]
        assert cmd == HOOK_CMD


def test_hook_install_preserves_foreign_keys(tmp_path):
    s = _settings(tmp_path)
    s.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}), encoding="utf-8")
    hook_installer.install(HOOK_CMD, s)
    root = _read(s)
    assert root["model"] == "opus"
    # ของผู้ใช้เดิมใน Stop ยังอยู่ + ของเราถูกเพิ่ม
    stop_cmds = [h["command"] for e in root["hooks"]["Stop"] for h in e["hooks"]]
    assert "echo hi" in stop_cmds
    assert HOOK_CMD in stop_cmds


def test_hook_install_twice_updates_not_duplicates(tmp_path):
    s = _settings(tmp_path)
    hook_installer.install(HOOK_CMD, s)
    new_cmd = '"C:\\other\\python.exe" -m tamaclaude --hook'
    hook_installer.install(new_cmd, s)
    stop = _read(s)["hooks"]["Stop"]
    ours = [h for e in stop for h in e["hooks"] if "--hook" in h["command"]]
    assert len(ours) == 1
    assert ours[0]["command"] == new_cmd


def test_hook_uninstall_removes_ours_keeps_theirs(tmp_path):
    s = _settings(tmp_path)
    s.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}), encoding="utf-8")
    hook_installer.install(HOOK_CMD, s)
    hook_installer.uninstall(s)
    stop = _read(s)["hooks"]["Stop"]
    cmds = [h["command"] for e in stop for h in e["hooks"]]
    assert cmds == ["echo hi"]


# MARK: - statusline installer


def test_statusline_install_sets_command(tmp_path):
    s = _settings(tmp_path)
    script = tmp_path / "statusline.ps1"
    inv = ["C:\\py\\python.exe", "-m", "tamaclaude"]
    statusline_installer.install(inv, s, script)
    root = _read(s)
    assert root["statusLine"]["type"] == "command"
    assert str(script) in root["statusLine"]["command"]
    assert root["statusLine"]["refreshInterval"] == 10
    assert script.exists()
    body = script.read_text(encoding="utf-8")
    assert "--usage-cache" in body


def test_statusline_delegates_previous(tmp_path):
    s = _settings(tmp_path)
    script = tmp_path / "statusline.ps1"
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-old-statusline"}}), encoding="utf-8")
    statusline_installer.install(["py", "-m", "tamaclaude"], s, script)
    # คำสั่งเดิมถูกเก็บใน $PREV ของสคริปต์
    assert statusline_installer.delegated_command_in_script(script) == "my-old-statusline"


def test_statusline_reinstall_keeps_original_previous(tmp_path):
    s = _settings(tmp_path)
    script = tmp_path / "statusline.ps1"
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-old"}}), encoding="utf-8")
    statusline_installer.install(["py", "-m", "tamaclaude"], s, script)
    # ติดตั้งซ้ำ: statusLine ชี้มาที่เราแล้ว ต้องกู้ my-old จากสคริปต์ ไม่ใช่เขียน of ourselves
    statusline_installer.install(["py", "-m", "tamaclaude"], s, script)
    assert statusline_installer.delegated_command_in_script(script) == "my-old"


def test_statusline_uninstall_restores_previous(tmp_path):
    s = _settings(tmp_path)
    script = tmp_path / "statusline.ps1"
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-old"}}), encoding="utf-8")
    statusline_installer.install(["py", "-m", "tamaclaude"], s, script)
    statusline_installer.uninstall(s, script)
    assert _read(s)["statusLine"]["command"] == "my-old"
    assert not script.exists()


def test_statusline_uninstall_no_previous_removes_key(tmp_path):
    s = _settings(tmp_path)
    script = tmp_path / "statusline.ps1"
    statusline_installer.install(["py", "-m", "tamaclaude"], s, script)
    statusline_installer.uninstall(s, script)
    assert "statusLine" not in _read(s)


def test_settings_error_on_non_object(tmp_path):
    s = _settings(tmp_path)
    s.write_text("[1,2,3]", encoding="utf-8")
    try:
        settings_file.load(s)
        assert False, "expected SettingsError"
    except settings_file.SettingsError:
        pass
