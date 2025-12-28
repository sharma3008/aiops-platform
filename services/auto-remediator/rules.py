from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import yaml


_SEVERITY_ORDER = {"info": 0, "warn": 1, "warning": 1, "error": 2, "critical": 3}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    incident_type: str
    min_severity: str
    pattern: str
    action_type: str
    target_service: str
    parameters: Dict[str, Any]
    cooldown_sec: int


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Rules file not found: {path}")
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

        rule_id = str(r.get("rule_id", "")).strip()
        incident_type = str(r.get("incident_type", "")).strip()
        min_severity = str(r.get("min_severity", defaults.get("severity", "warn"))).strip()
        pattern = str(r.get("pattern", "*")).strip()
        action_type = str(r.get("action_type", "")).strip()
        target_service = str(r.get("target_service", "{service}")).strip()
        parameters = r.get("parameters") or {}
        cooldown_sec = int(r.get("cooldown_sec", defaults.get("cooldown_sec", 180)))

        if not rule_id or not incident_type or not action_type:
            continue

        parsed.append(
            Rule(
                rule_id=rule_id,
                incident_type=incident_type,
                min_severity=min_severity,
                pattern=pattern,
                action_type=action_type,
                target_service=target_service,
                parameters=dict(parameters),
                cooldown_sec=cooldown_sec,
            )
        )

    return parsed, defaults, policy, aliases


def _severity_ok(incident_sev: str, min_sev: str) -> bool:
    i = _SEVERITY_ORDER.get(str(incident_sev).lower(), 0)
    m = _SEVERITY_ORDER.get(str(min_sev).lower(), 0)
    return i >= m


def _pattern_ok(service: str, pattern: str) -> bool:
    return fnmatch.fnmatch(service, pattern)


def _canon_service(s: Any) -> str:
    return str(s).strip().lower() if s is not None else ""


def canonicalize_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    inc = dict(incident or {})

    raw_service = _canon_service(inc.get("service"))
    raw_sev = _canon_service(inc.get("severity"))
    if raw_sev == "warning":
        raw_sev = "warn"

    cfg_path = os.getenv("REMEDIATION_RULES_PATH", "/app/rules/remediation_rules.yml")
    cfg = _load_yaml(cfg_path)
    _, _, _, aliases = _parse_rules(cfg)

    canonical_service = aliases.get(raw_service, raw_service) if raw_service else raw_service
    if raw_service and canonical_service and raw_service != canonical_service:
        inc["_service_alias_raw"] = raw_service
        inc["_service_alias_canonical"] = canonical_service

    if canonical_service:
        inc["service"] = canonical_service
    if raw_sev:
        inc["severity"] = raw_sev

    # Normalize other possible fields if they exist
    for key in ("target_service", "target", "service_name"):
        if key in inc and isinstance(inc.get(key), str):
            t_raw = _canon_service(inc.get(key))
            t_can = aliases.get(t_raw, t_raw) if t_raw else t_raw
            inc[key] = t_can

    return inc


def _policy_allows(target_service: str, action_type: str, policy: Dict[str, Any]) -> Tuple[bool, str]:
    allowlist = [str(x).strip().lower() for x in (policy.get("allowlist_services") or []) if str(x).strip()]
    denylist = [str(x).strip().lower() for x in (policy.get("denylist_services") or []) if str(x).strip()]
    allowed_actions = [str(x).strip() for x in (policy.get("allowlist_action_types") or []) if str(x).strip()]

    ts = _canon_service(target_service)

    if ts in denylist:
        return False, "denylist_service"
    if allowlist and ts not in allowlist:
        return False, "not_in_allowlist"
    if allowed_actions and str(action_type).strip() not in allowed_actions:
        return False, "action_type_not_allowed"
    return True, "allowed"


def evaluate_rules_with_diagnostics(incident: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cfg_path = os.getenv("REMEDIATION_RULES_PATH", "/app/rules/remediation_rules.yml")
    cfg = _load_yaml(cfg_path)
    rules, defaults, policy, _aliases = _parse_rules(cfg)

    service = str(incident.get("service", "unknown"))
    sev = str(incident.get("severity", defaults.get("severity", "warn")))
    incident_type = str(incident.get("incident_type", "")).strip()

    actions: List[Dict[str, Any]] = []
    matched_candidates: List[Dict[str, Any]] = []
    policy_rejects: List[Dict[str, Any]] = []

    for rule in rules:
        if rule.incident_type and rule.incident_type != incident_type:
            continue
        if not _severity_ok(sev, rule.min_severity):
            continue
        if rule.pattern and not _pattern_ok(service, rule.pattern):
            continue

        # IMPORTANT: render target BEFORE policy checks/diagnostics
        raw_target = rule.target_service or "{service}"
        try:
            target = raw_target.format(service=service)
        except Exception:
            target = service

        matched_candidates.append(
            {"rule_id": rule.rule_id, "action_type": rule.action_type, "target_service": target}
        )

        ok, reason = _policy_allows(target, rule.action_type, policy)
        if not ok:
            policy_rejects.append(
                {"rule_id": rule.rule_id, "action_type": rule.action_type, "target_service": target, "reason": reason}
            )
            continue

        actions.append(
            {
                "rule_id": rule.rule_id,
                "action_type": rule.action_type,
                "target_service": target,
                "parameters": dict(rule.parameters or {}),
                "cooldown_sec": int(rule.cooldown_sec),
            }
        )

    diag = {
        "matched_candidates": matched_candidates,
        "policy_rejects": policy_rejects,
    }
    return actions, diag
