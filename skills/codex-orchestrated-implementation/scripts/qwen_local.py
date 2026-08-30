#!/usr/bin/env python3
"""Constrained OpenAI-compatible adapter for the optional Qwen assistant."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

HEX64 = re.compile(r"^[0-9a-f]{64}$")
PAIR = frozenset({"luna_worker", "claude_worker"})
QWEN_RECEIPT_REQUIRED = frozenset({
    "schema_version", "receipt_type", "invocation_id", "task_id", "milestone_id", "role",
    "role_instance_id", "session_id", "host_id", "context_id", "endpoint", "model",
    "capability_probe", "request_digest", "output_digest", "input_tokens", "output_tokens",
    "read_only", "untrusted", "write_mode", "outcome",
})
QWEN_RECEIPT_ALLOWED = QWEN_RECEIPT_REQUIRED | frozenset({"failure", "warnings"})


class QwenError(ValueError):
    """Stable, fail-closed Qwen rejection."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise QwenError("E_QWEN_RECEIPT_SCHEMA", f"{label} must be a lowercase SHA-256 digest")
    return value


def _qwen_policy(resolved_or_policy: Any) -> Mapping[str, Any]:
    policy = getattr(resolved_or_policy, "policy", resolved_or_policy)
    if not isinstance(policy, Mapping) or policy.get("version") != 2:
        raise QwenError("E_QWEN_CONFIG_VERSION", "Qwen is available only under a validated v2 policy")
    qwen = policy.get("qwen_local")
    if not isinstance(qwen, Mapping):
        raise QwenError("E_QWEN_CONFIG", "qwen_local policy is missing")
    return qwen


def _preflight(qwen: Mapping[str, Any], *, prompt: str, tools: bool, skills: bool, inherited_credentials: bool) -> None:
    if qwen.get("enabled") is not True:
        raise QwenError("E_QWEN_DISABLED", "Qwen local assistant is disabled")
    if not isinstance(prompt, str) or not prompt:
        raise QwenError("E_QWEN_INPUT", "prompt must be non-empty text")
    if tools or skills or inherited_credentials:
        raise QwenError("E_QWEN_CAPABILITY_REJECTED", "Qwen cannot receive tools, skills, or inherited credentials")
    if qwen.get("tools") is not False or qwen.get("skills") is not False or qwen.get("inherit_credentials") is not False:
        raise QwenError("E_QWEN_CAPABILITY_REJECTED", "Qwen capability policy is not read-only")
    if qwen.get("write_mode") not in {"disabled", "draft_patch"}:
        raise QwenError("E_QWEN_WRITE_REJECTED", "Qwen write mode is not permitted")
    if len(prompt.encode("utf-8")) > int(qwen["max_input_tokens"]) * 8:
        raise QwenError("E_QWEN_INPUT_BUDGET", "prompt exceeds the configured input budget")


def pre_dispatch(resolved_or_policy: Any, *, prompt: str, write_requested: bool = False, write_gate: Mapping[str, Any] | None = None, tools: bool = False, skills: bool = False, inherited_credentials: bool = False) -> None:
    """Apply stable rejection rules before any network request or tool dispatch."""

    qwen = _qwen_policy(resolved_or_policy)
    _preflight(qwen, prompt=prompt, tools=tools, skills=skills, inherited_credentials=inherited_credentials)
    if write_requested:
        if qwen.get("write_mode") != "draft_patch" or write_gate is None:
            raise QwenError("E_QWEN_WRITE_REJECTED", "Qwen writes require an explicitly validated isolated draft-patch gate")
        validate_write_gate(qwen, write_gate)


def _request(qwen: Mapping[str, Any], path: str, method: str = "GET", payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    endpoint = str(qwen["endpoint"]).rstrip("/")
    url = endpoint + path
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if qwen["auth_mode"] == "explicit_env":
        token = os.environ.get(qwen["auth_token_env"])
        if not token:
            raise QwenError("E_QWEN_AUTH_UNAVAILABLE", "the explicitly configured Qwen token is unavailable")
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=qwen["timeout_seconds"]) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise QwenError("E_QWEN_PROBE_FAILED", f"local Qwen endpoint unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise QwenError("E_QWEN_PROBE_FAILED", "Qwen endpoint returned a non-object response")
    return value


def probe_capabilities(resolved_or_policy: Any) -> dict[str, Any]:
    """Probe the OpenAI-compatible /models endpoint using only explicit policy."""

    qwen = _qwen_policy(resolved_or_policy)
    if qwen.get("enabled") is not True:
        raise QwenError("E_QWEN_DISABLED", "Qwen local assistant is disabled")
    if qwen["endpoint"] not in qwen["allowed_endpoints"] or qwen["model"] not in qwen["allowed_models"]:
        raise QwenError("E_QWEN_ALLOWLIST", "Qwen endpoint or model is outside its allowlist")
    last: QwenError | None = None
    for attempt in range(qwen["max_retries"] + 1):
        try:
            response = _request(qwen, "/models")
            model_ids = {item.get("id") for item in response.get("data", []) if isinstance(item, dict)}
            if qwen["model"] not in model_ids:
                raise QwenError("E_QWEN_MODEL_UNAVAILABLE", "allowlisted Qwen model was not advertised")
            return {"endpoint": qwen["endpoint"], "model": qwen["model"], "protocol": "openai-compatible", "tools": False, "skills": False, "read_only": True, "attempts": attempt + 1}
        except QwenError as exc:
            last = exc
            if exc.code in {"E_QWEN_MODEL_UNAVAILABLE", "E_QWEN_ALLOWLIST"}:
                raise
            if attempt < qwen["max_retries"]:
                time.sleep(min(0.25 * (attempt + 1), 1.0))
    raise last or QwenError("E_QWEN_PROBE_FAILED", "Qwen capability probe failed")


def validate_write_gate(qwen: Mapping[str, Any], gate: Mapping[str, Any]) -> None:
    """Validate the only possible mechanical-write route for Qwen output."""

    required = {"mode", "terra_decision", "integration_tests_passed", "adopted_by", "opposite_pair_reviewed", "reviewed_by", "isolated_worktree", "direct_worktree_mutation"}
    if not isinstance(gate, Mapping) or not required.issubset(gate):
        raise QwenError("E_QWEN_WRITE_GATE", "draft-patch gate is incomplete")
    if qwen.get("write_mode") != "draft_patch" or gate["mode"] != "draft_patch":
        raise QwenError("E_QWEN_WRITE_GATE", "draft-patch mode is not enabled")
    if gate["terra_decision"] != "accept":
        raise QwenError("E_QWEN_WRITE_GATE", "Terra must accept the Qwen draft before adoption")
    if gate["integration_tests_passed"] is not True:
        raise QwenError("E_QWEN_WRITE_GATE", "integration-test gate has not passed")
    if gate["adopted_by"] not in PAIR or gate["reviewed_by"] not in PAIR or gate["reviewed_by"] == gate["adopted_by"]:
        raise QwenError("E_QWEN_WRITE_GATE", "adoption and opposite-pair review must name distinct pair workers")
    if gate["opposite_pair_reviewed"] is not True:
        raise QwenError("E_QWEN_WRITE_GATE", "the opposite pair worker has not reviewed the adoption")
    if not isinstance(gate["isolated_worktree"], str) or not Path(gate["isolated_worktree"]).is_absolute():
        raise QwenError("E_QWEN_WRITE_GATE", "Qwen draft must use an absolute isolated worktree")
    if gate["direct_worktree_mutation"] is not False:
        raise QwenError("E_QWEN_WRITE_GATE", "Qwen may not mutate the active worktree directly")


def validate_qwen_receipt(receipt: Any) -> Mapping[str, Any]:
    if not isinstance(receipt, Mapping):
        raise QwenError("E_QWEN_RECEIPT_SCHEMA", "Qwen receipt must be an object")
    missing = QWEN_RECEIPT_REQUIRED - set(receipt)
    unknown = set(receipt) - QWEN_RECEIPT_ALLOWED
    if missing or unknown:
        raise QwenError("E_QWEN_RECEIPT_SCHEMA", f"missing={sorted(missing)} unknown={sorted(unknown)}")
    if receipt["schema_version"] != 2 or receipt["receipt_type"] != "qwen_local_assistant":
        raise QwenError("E_QWEN_RECEIPT_SCHEMA", "Qwen receipt must use schema v2 and its receipt type")
    if receipt["role"] != "qwen_local_assistant" or receipt["read_only"] is not True or receipt["untrusted"] is not True:
        raise QwenError("E_QWEN_RECEIPT_POLICY", "Qwen receipts must identify an untrusted read-only assistant")
    for field in ("invocation_id", "task_id", "milestone_id", "role_instance_id", "session_id", "host_id", "context_id", "endpoint", "model", "outcome"):
        if not isinstance(receipt[field], str) or not receipt[field]:
            raise QwenError("E_QWEN_RECEIPT_SCHEMA", f"{field} must be a non-empty string")
    for field in ("request_digest", "output_digest"):
        _digest(receipt[field], field)
    if type(receipt["input_tokens"]) is not int or type(receipt["output_tokens"]) is not int or receipt["input_tokens"] < 0 or receipt["output_tokens"] < 0:
        raise QwenError("E_QWEN_RECEIPT_SCHEMA", "token counts must be non-negative integers")
    return receipt


def make_receipt(*, invocation_id: str, task_id: str, milestone_id: str, binding: Mapping[str, str], endpoint: str, model: str, prompt: str, output: str, input_tokens: int, output_tokens: int, outcome: str = "success", write_mode: str = "disabled") -> dict[str, Any]:
    receipt = {
        "schema_version": 2, "receipt_type": "qwen_local_assistant", "invocation_id": invocation_id,
        "task_id": task_id, "milestone_id": milestone_id, "role": "qwen_local_assistant",
        **dict(binding), "endpoint": endpoint, "model": model,
        "capability_probe": "confirmed", "request_digest": hashlib.sha256(prompt.encode()).hexdigest(),
        "output_digest": hashlib.sha256(output.encode()).hexdigest(), "input_tokens": input_tokens,
        "output_tokens": output_tokens, "read_only": True, "untrusted": True, "write_mode": write_mode,
        "outcome": outcome,
    }
    validate_qwen_receipt(receipt)
    return receipt


class QwenLocalAdapter:
    """Small OpenAI-compatible client with policy checks at every dispatch."""

    def __init__(self, resolved_or_policy: Any):
        self.policy = resolved_or_policy
        self.qwen = _qwen_policy(resolved_or_policy)

    def probe(self) -> dict[str, Any]:
        return probe_capabilities(self.policy)

    def pre_dispatch(self, prompt: str, **kwargs: Any) -> None:
        pre_dispatch(self.policy, prompt=prompt, **kwargs)

    def complete(self, prompt: str, *, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.pre_dispatch(prompt)
        if capability is None:
            capability = self.probe()
        if capability.get("model") != self.qwen["model"] or capability.get("endpoint") != self.qwen["endpoint"]:
            raise QwenError("E_QWEN_PROBE_MISMATCH", "capability probe does not match the configured endpoint/model")
        response = _request(self.qwen, "/chat/completions", method="POST", payload={
            "model": self.qwen["model"], "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.qwen["max_output_tokens"], "temperature": 0,
        })
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise QwenError("E_QWEN_RESPONSE", "Qwen completion response has no usable choice")
        message = choices[0].get("message")
        output = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(output, str):
            raise QwenError("E_QWEN_RESPONSE", "Qwen completion content is not text")
        return {"output": output, "untrusted": True, "model": self.qwen["model"], "endpoint": self.qwen["endpoint"]}
