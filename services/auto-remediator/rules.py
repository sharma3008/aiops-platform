"""
Auto-remediator rules engine (Phase 4).

YAML-driven to support:
- deterministic, reviewable remediation behavior
- cooldowns and basic policy guards
- service alias normalization

Default YAML path: /app/rules/remediation_rules.yml
Override with env var: REMEDIATION_RULES_PATH
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import yaml

_SEVERITY_ORDER = {"info": 0, "warn": 1, "warning": 1, "error": 2, "critical": 3}
_LOG = logging.getLogger("auto_remediator.rules")


def _sev_rank(sev: Any) -> int:
    if not isinstance(sev, str):
        return _SEVERITY_ORDER["warn"]
    return _SEVERITY_ORDER.get(sev.lower(), _SEVERITY_ORDER["warn"])


@dataclass(frozen=True)
class Rule:
    rule_id: str
    enabled: bool
    match_incident_type: str
    match_service: str
    min_severity: str
    action_type: str
    target_service_tpl: str
    parameters: Dict[str, Any]
    severity: str
    cooldown_sec: int


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        # Leave unknown placeholders intact rather than crashing
        return "{" + key + "}"


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _parse_rules(cfg: Dict[str, Any]) -> Tuple[List[Rule], Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    defaults = cfg.get("defaults") or {}
    policy = cfg.get("policy") or {}

    aliases_raw = cfg.get("service_aliases") or {}
    aliases: Dict[str, str] = {}
    if isinstance(aliases_raw, dict):
        for k, v in aliases_raw.items():
            k_s = str(k).strip().lower()
            v_s = str(v).strip().lower()
            if k_s and v_s:
                aliases[k_s] = v_s

    rules_raw = cfg.get("rules") or []
    parsed: List[Rule] = []

    for r in rules_raw:
        if not isinstance(r, dict):
            continue
        rule_id = str(r.get("rule_id") or "").strip()
        if not rule_id:
            continue

        enabled = bool(r.get("enabled", defaults.get("enabled", True)))
        match = r.get("match") or {}
        action = r.get("action") or {}

        parsed.append(
            Rule(
                rule_id=rule_id,
                enabled=enabled,
                match_incident_type=str(match.get("incident_type") or "").strip(),
                match_service=str(match.get("service") or "*").strip(),
                min_severity=str(match.get("min_severity") or defaults.get("severity") or "warn"),
                action_type=str(action.get("action_type") or "").strip(),
                target_service_tpl=str(action.get("target_service") or "{service}"),
                parameters=action.get("parameters") or {},
                severity=str(r.get("severity") or defaults.get("severity") or "warn"),
                cooldown_sec=int(r.get("cooldown_sec") or defaults.get("cooldown_sec") or 180),
            )
        )

    return parsed, defaults, policy, aliases


def _normalize_list(values: Any, *, lower: bool = True) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for v in values:
        s = str(v).strip()
        if not s:
            continue
        out.append(s.lower() if lower else s)
    return out


def _normalize_action_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for v in values:
        s = str(v).strip()
        if not s:
            continue
        out.append(s.upper())
    return out


def _policy_allows_reason(policy: Dict[str, Any], action_type: str, target_service: str) -> Optional[str]:
    """Return None if allowed; otherwise reject reason string."""
    deny = _normalize_list(policy.get("denylist_services"), lower=True)
    allow = _normalize_list(policy.get("allowlist_services"), lower=True)
    allow_actions = _normalize_action_list(policy.get("allowlist_action_types"))

    ts = str(target_service).strip().lower() if target_service else "unknown"
    at = str(action_type).strip().upper() if action_type else ""

    if deny and ts in deny:
        return "denylist_service"
    if allow and ts not in allow:
        return "not_in_allowlist"
    if allow_actions and at not in allow_actions:
        return "action_type_not_allowed"
    return None


class _LazyConfig:
    def __init__(self) -> None:
        self._loaded_path: Optional[str] = None
        self.rules: List[Rule] = []
        self.policy: Dict[str, Any] = {}
        self.defaults: Dict[str, Any] = {}
        self.service_aliases: Dict[str, str] = {}

    def load(self, path: str) -> None:
        cfg = _load_yaml(path)
        self.rules, self.defaults, self.policy, self.service_aliases = _parse_rules(cfg)
        self._loaded_path = path


_CFG = _LazyConfig()


def get_rules_path() -> str:
    return os.getenv("REMEDIATION_RULES_PATH", "/app/rules/remediation_rules.yml")


def _ensure_loaded() -> None:
    path = get_rules_path()
    if _CFG._loaded_path != path:
        _CFG.load(path)
        _LOG.info("Loaded remediation rules: path=%s rules=%d", path, len(_CFG.rules))


def canonicalize_service(service: Any) -> str:
    _ensure_loaded()
    raw = str(service or "").strip()
    if not raw:
        return "unknown"
    key = raw.lower()
    return _CFG.service_aliases.get(key, key)


def canonicalize_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow copy + canonicalize incident['service'].

    Adds:
      _service_alias_raw, _service_alias_canonical when alias mapping occurs.
    """
    raw_service = incident.get("service")
    canon = canonicalize_service(raw_service)

    # If unchanged (including same string), return as-is.
    if str(raw_service or "").strip().lower() == canon:
        out = dict(incident)
        out["service"] = canon  # enforce lowercase canonical even if case differed
        return out

    out = dict(incident)
    out["_service_alias_raw"] = raw_service
    out["_service_alias_canonical"] = canon
    out["service"] = canon
    return out


def evaluate_rules_with_diagnostics(incident: dict) -> Tuple[List[dict], Dict[str, Any]]:
    _ensure_loaded()

    incident_type = str(incident.get("incident_type") or incident.get("type") or "").strip()
    raw_service = incident.get("service") or "unknown"
    service = canonicalize_service(raw_service)
    sev = incident.get("severity") or "warn"
    sev_rank = _sev_rank(sev)

    actions: List[Dict[str, Any]] = []
    policy_rejects: List[Dict[str, Any]] = []

    ctx = _SafeDict(dict(incident))
    ctx["service"] = service
    ctx.setdefault("incident_type", incident_type)

    matched_candidates = 0

    for rule in _CFG.rules:
        if not rule.enabled:
            continue
        if rule.match_incident_type and rule.match_incident_type != incident_type:
            continue
        if rule.match_service and not fnmatch.fnmatch(service, rule.match_service):
            continue
        if sev_rank < _sev_rank(rule.min_severity):
            continue
        if not rule.action_type:
            continue

        matched_candidates += 1

        target_raw = rule.target_service_tpl.format_map(ctx) if rule.target_service_tpl else service
        target_service = canonicalize_service(target_raw) if target_raw else service

        reject_reason = _policy_allows_reason(_CFG.policy, rule.action_type, target_service)
        if reject_reason is not None:
            policy_rejects.append(
                {
                    "rule_id": rule.rule_id,
                    "action_type": str(rule.action_type).upper(),
                    "target_service": target_service,
                    "reason": reject_reason,
                }
            )
            continue

        actions.append(
            {
                "rule_id": rule.rule_id,
                "action_type": str(rule.action_type).upper(),
                "target_service": target_service,
                "parameters": rule.parameters or {},
                "severity": rule.severity or "warn",
                "cooldown_sec": rule.cooldown_sec,
            }
        )

    diag: Dict[str, Any] = {
        "service_raw": str(raw_service),
        "service": service,
        "incident_type": incident_type,
        "matched_candidates": matched_candidates,
        "policy_rejects": policy_rejects,
    }
    return actions, diag


def evaluate_rules(incident: dict) -> list[dict]:
    actions, _ = evaluate_rules_with_diagnostics(incident)
    return actions
