# m_flow/memory/procedural/validators/procedural_validator.py
"""
Procedure validator

Validate whether Procedure Bundle conforms to data contract.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from m_flow.shared.tracing import TraceManager

from .procedural_contract import (
    ProcedureContract,
    ValidationIssue,
    IssueSeverity,
)


@dataclass
class ProcedureBundle:
    """
    Procedure Bundle structure (for validation).

    Contains Procedure and its associated Packs and Points.
    """

    # Procedure basic information
    procedure_id: Optional[str] = None
    procedure_key: Optional[str] = None
    signature: Optional[str] = None
    name: str = ""
    summary: str = ""
    search_text: str = ""
    version: int = 1
    status: str = "active"
    confidence: str = "high"

    # Packs
    steps_pack: Optional[Dict[str, Any]] = None
    context_pack: Optional[Dict[str, Any]] = None

    # Quality flags
    quality_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "procedure_id": self.procedure_id,
            "procedure_key": self.procedure_key,
            "signature": self.signature,
            "name": self.name,
            "summary": self.summary,
            "search_text": self.search_text,
            "version": self.version,
            "status": self.status,
            "confidence": self.confidence,
            "steps_pack": self.steps_pack,
            "context_pack": self.context_pack,
            "quality_flags": self.quality_flags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProcedureBundle":
        """Create from dictionary"""
        return cls(
            procedure_id=data.get("procedure_id"),
            procedure_key=data.get("procedure_key"),
            signature=data.get("signature"),
            name=data.get("name", ""),
            summary=data.get("summary", ""),
            search_text=data.get("search_text", ""),
            version=data.get("version", 1),
            status=data.get("status", "active"),
            confidence=data.get("confidence", "high"),
            steps_pack=data.get("steps_pack"),
            context_pack=data.get("context_pack"),
            quality_flags=data.get("quality_flags", []),
        )


def validate_procedure_bundle(
    bundle: ProcedureBundle,
    strict: bool = False,
) -> Tuple[bool, List[ValidationIssue]]:
    """
    Validate whether Procedure Bundle conforms to data contract.

    Args:
        bundle: Bundle to validate
        strict: Whether strict mode (WARNING also considered failure)

    Returns:
        (ok, issues): Whether passed, list of issues
    """
    issues: List[ValidationIssue] = []
    bundle_dict = bundle.to_dict()

    # Run all rules
    all_rules = ProcedureContract.get_all_rules()

    for code, rule in all_rules.items():
        try:
            check_fn = rule["check"]
            passed = check_fn(bundle_dict)

            if not passed:
                issues.append(
                    ValidationIssue(
                        code=code,
                        severity=rule["severity"],
                        message=rule["message"],
                        suggestion=rule.get("suggestion", ""),
                    )
                )
        except Exception as e:
            # Rule execution error, record but continue
            issues.append(
                ValidationIssue(
                    code=f"{code}_CHECK_ERROR",
                    severity=IssueSeverity.INFO,
                    message=f"Rule check error: {e}",
                )
            )

    # Determine if passed
    if strict:
        ok = len(issues) == 0
    else:
        # Only ERROR level counts as failure
        ok = not any(i.severity == IssueSeverity.ERROR for i in issues)

    # Tracing
    TraceManager.event(
        "procedural.validate",
        {
            "bundle_id": bundle.procedure_id or bundle.procedure_key,
            "ok": ok,
            "issues_count": len(issues),
            "error_count": sum(1 for i in issues if i.severity == IssueSeverity.ERROR),
            "warning_count": sum(1 for i in issues if i.severity == IssueSeverity.WARNING),
            "issue_codes": [i.code for i in issues[:10]],
        },
    )

    return ok, issues


def validate_procedure_from_datapoint(
    procedure_dp: Any,
) -> Tuple[bool, List[ValidationIssue]]:
    """
    Validate from Procedure MemoryNode.

    Convert MemoryNode to Bundle then validate.
    """
    # Extract bundle information
    bundle = ProcedureBundle(
        procedure_id=str(getattr(procedure_dp, "id", "")),
        procedure_key=getattr(procedure_dp, "signature", None),
        signature=getattr(procedure_dp, "signature", None),
        name=getattr(procedure_dp, "name", ""),
        summary=getattr(procedure_dp, "summary", ""),
        search_text=getattr(procedure_dp, "search_text", ""),
        version=getattr(procedure_dp, "version", 1),
        status=getattr(procedure_dp, "status", "active"),
        confidence=getattr(procedure_dp, "confidence", "high"),
    )

    # Extract packs
    if hasattr(procedure_dp, "has_steps") and procedure_dp.has_steps:
        for _edge, steps_pack in procedure_dp.has_steps:
            bundle.steps_pack = {
                "id": str(getattr(steps_pack, "id", "")),
                "name": getattr(steps_pack, "name", ""),
                "search_text": getattr(steps_pack, "search_text", ""),
                "anchor_text": getattr(steps_pack, "anchor_text", ""),
                "description": getattr(steps_pack, "description", ""),
                "step_points": [],
            }
            if hasattr(steps_pack, "has_point") and steps_pack.has_point:
                for _, point in steps_pack.has_point:
                    bundle.steps_pack["step_points"].append(
                        {
                            "name": getattr(point, "name", ""),
                            "search_text": getattr(point, "search_text", ""),
                            "order_index": getattr(point, "order_index", None),
                            "step_number": getattr(point, "step_number", None),
                        }
                    )
            break

    if hasattr(procedure_dp, "has_context") and procedure_dp.has_context:
        for _edge, ctx_pack in procedure_dp.has_context:
            bundle.context_pack = {
                "id": str(getattr(ctx_pack, "id", "")),
                "name": getattr(ctx_pack, "name", ""),
                "search_text": getattr(ctx_pack, "search_text", ""),
                "anchor_text": getattr(ctx_pack, "anchor_text", ""),
                "description": getattr(ctx_pack, "description", ""),
                "when_text": getattr(ctx_pack, "when_text", ""),
                "why_text": getattr(ctx_pack, "why_text", ""),
                "boundary_text": getattr(ctx_pack, "boundary_text", ""),
                "context_points": [],
            }
            if hasattr(ctx_pack, "has_point") and ctx_pack.has_point:
                for _, point in ctx_pack.has_point:
                    bundle.context_pack["context_points"].append(
                        {
                            "name": getattr(point, "name", ""),
                            "search_text": getattr(point, "search_text", ""),
                            "cue_type": getattr(point, "cue_type", None),
                        }
                    )
            break

    return validate_procedure_bundle(bundle)
