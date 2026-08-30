from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from validate_config import ConfigError, load_config, main  # noqa: E402


VALID = '''\
version = 1

[orchestrator]
provider = "codex"
model = "gpt-5.6-terra"
reasoning_effort = "high"

[[pair]]
name = "worker_a"
provider = "claude_code"
model = "opus"
enabled = true

[[pair]]
name = "worker_b"
provider = "codex"
model = "gpt-5.6-luna"
enabled = true

[workflow]
profile = "standard"
pairing_mode = "alternating"
first_implementer = "worker_a"
'''


def write(tmp_path: Path, text: str = VALID) -> Path:
    path = tmp_path / ".codex-orchestration.toml"
    path.write_text(text, encoding="utf-8")
    return path


def assert_code(tmp_path: Path, text: str, code: str) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, text))
    assert caught.value.code == code


def test_valid_policy_resolves_digest(tmp_path: Path) -> None:
    resolved = load_config(write(tmp_path))
    assert resolved.policy["workflow"]["pairing_mode"] == "alternating"
    assert len(resolved.policy["pair"]) == 2
    assert len(resolved.policy_digest) == 64


def test_rejects_malformed_toml(tmp_path: Path) -> None:
    assert_code(tmp_path, "version = [", "E_CONFIG_MALFORMED")


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    assert_code(tmp_path, VALID + "\nunknown = true\n", "E_CONFIG_UNKNOWN_KEY")


def test_rejects_role_declarations(tmp_path: Path) -> None:
    assert_code(tmp_path, VALID.replace('enabled = true', 'enabled = true\nrole = "reviewer"', 1), "E_ROLE_NOT_ALLOWED")


def test_rejects_duplicate_member_names(tmp_path: Path) -> None:
    assert_code(tmp_path, VALID.replace('name = "worker_b"', 'name = "worker_a"'), "E_PAIR_DUPLICATE")


def test_rejects_wrong_pair_cardinality_or_disabled_member(tmp_path: Path) -> None:
    first_pair = VALID.find("[[pair]]")
    second_pair = VALID.find("[[pair]]", first_pair + 1)
    workflow = VALID.find("[workflow]")
    one = VALID[:second_pair] + VALID[workflow:]
    assert_code(tmp_path, one, "E_PAIR_CARDINALITY")
    before, after = VALID.rsplit("enabled = true", 1)
    assert_code(tmp_path, before + "enabled = false" + after, "E_PAIR_CARDINALITY")


def test_rejects_unsupported_pairing_mode(tmp_path: Path) -> None:
    assert_code(tmp_path, VALID.replace('pairing_mode = "alternating"', 'pairing_mode = "parallel"'), "E_PAIRING_MODE")


def test_rejects_invalid_first_implementer(tmp_path: Path) -> None:
    assert_code(tmp_path, VALID.replace('first_implementer = "worker_a"', 'first_implementer = "worker_c"'), "E_FIRST_IMPLEMENTER")


def test_rejects_invalid_provider_model_combinations(tmp_path: Path) -> None:
    assert_code(tmp_path, VALID.replace('provider = "claude_code"\nmodel = "opus"', 'provider = "codex"\nmodel = "opus"'), "E_PROVIDER_MODEL")
    assert_code(tmp_path, VALID.replace('provider = "claude_code"', 'provider = "unknown"'), "E_PROVIDER_MODEL")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('reasoning_effort = "high"', 'reasoning_effort = ["high"]'),
        ('profile = "standard"', 'profile = ["standard"]'),
        ('pairing_mode = "alternating"', 'pairing_mode = ["alternating"]'),
    ],
)
def test_rejects_unhashable_workflow_value_shapes(tmp_path: Path, old: str, new: str) -> None:
    assert_code(tmp_path, VALID.replace(old, new), "E_CONFIG_SCHEMA")


def test_accepts_new_codex_id_shape_pending_runtime_confirmation(tmp_path: Path) -> None:
    resolved = load_config(write(tmp_path, VALID.replace('model = "gpt-5.6-luna"', 'model = "gpt-9.9-future"')))
    assert resolved.policy["pair"][1]["model"] == "gpt-9.9-future"


def test_cli_prints_resolved_policy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(write(tmp_path))]) == 0
    output = capsys.readouterr().out
    assert '"source": "project_config"' in output
    assert '"policy_digest"' in output
