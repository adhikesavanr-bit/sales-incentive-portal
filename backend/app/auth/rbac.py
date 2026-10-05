"""Role-based access control.

The rule this module exists to enforce: **scope is derived from the identity in
the token, never from a parameter in the request.** A caller may ask for
`employee_id=NHP002`; this module decides whether that id is inside the
caller's own subtree, and the answer is computed from the hierarchy in
BigQuery, not from anything the caller sent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.models.schemas import Role


class Permission(str, Enum):
    VIEW_OWN = "VIEW_OWN"
    VIEW_TEAM = "VIEW_TEAM"
    VIEW_REGION = "VIEW_REGION"
    VIEW_ZONE = "VIEW_ZONE"
    VIEW_BUSINESS = "VIEW_BUSINESS"
    UPLOAD_SALES = "UPLOAD_SALES"
    MANAGE_EMPLOYEES = "MANAGE_EMPLOYEES"
    MANAGE_HIERARCHY = "MANAGE_HIERARCHY"
    MANAGE_RULES = "MANAGE_RULES"
    VIEW_TARGETS = "VIEW_TARGETS"
    PROPOSE_TARGETS = "PROPOSE_TARGETS"
    APPROVE_TARGETS = "APPROVE_TARGETS"
    RECALCULATE = "RECALCULATE"
    LOCK_MONTH = "LOCK_MONTH"
    REOPEN_MONTH = "REOPEN_MONTH"
    EXPORT_SCOPED = "EXPORT_SCOPED"
    VIEW_AUDIT = "VIEW_AUDIT"
    # See a month's results before it is approved: the people who calculate
    # and review it. Everyone else sees a month only once it is APPROVED.
    VIEW_UNPUBLISHED = "VIEW_UNPUBLISHED"


_BDE = {Permission.VIEW_OWN, Permission.VIEW_TARGETS, Permission.EXPORT_SCOPED}
_SUB = _BDE | {Permission.VIEW_TEAM}
# Targets are read-only for the sales line, managers included: only the admin
# tiers below set them.
_RM = _SUB | {Permission.VIEW_REGION}
_ZM = _RM | {Permission.VIEW_ZONE}
_BH = _ZM | {Permission.VIEW_BUSINESS}
_EDIT_TARGETS = {Permission.PROPOSE_TARGETS, Permission.APPROVE_TARGETS}
# Manages people and targets within their own subtree. Deliberately NOT given
# VIEW_BUSINESS: a team lead who can edit their team's targets should not
# thereby see every other manager's payroll.
_TEAM_ADMIN = _RM | _EDIT_TARGETS | {
    Permission.MANAGE_EMPLOYEES,
    Permission.MANAGE_HIERARCHY,
}

_FIN = _BH | _EDIT_TARGETS | {
    Permission.UPLOAD_SALES,
    Permission.MANAGE_EMPLOYEES,
    Permission.MANAGE_HIERARCHY,
    Permission.MANAGE_RULES,
    Permission.RECALCULATE,
    Permission.LOCK_MONTH,
    Permission.VIEW_AUDIT,
    Permission.VIEW_UNPUBLISHED,
}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.BDE: _BDE,
    Role.TEAM_ADMIN: _TEAM_ADMIN,
    Role.SUB_MANAGER: _SUB,
    Role.REGIONAL_MANAGER: _RM,
    Role.ZONAL_MANAGER: _ZM,
    Role.BUSINESS_HEAD: _BH,
    Role.FINANCE_ADMIN: _FIN,
    Role.SUPER_ADMIN: _FIN | {Permission.REOPEN_MONTH},
}


# Which incentive policies (slab scopes) each role may read. A person sees the
# policy for their own level and every level beneath it, never above: a BDE
# sees only the BDE policy, an RM sees RM and below, a ZM sees ZM and below.
# The first-year MBBS unit slab applies at the BDE level. None means all.
_BDE_POLICIES = {"BDE_MONTHLY", "FIRST_YEAR_MBBS_UNITS"}
_SUB_POLICIES = _BDE_POLICIES | {"SUBMANAGER_MONTHLY"}
_RM_POLICIES = _SUB_POLICIES | {"TEAM_OWNER_QUARTERLY"}
_ZM_POLICIES = _RM_POLICIES | {"ZM_NINE_MONTH"}

ROLE_POLICY_SCOPES: dict[Role, set[str] | None] = {
    Role.BDE: _BDE_POLICIES,
    Role.SUB_MANAGER: _SUB_POLICIES,
    Role.REGIONAL_MANAGER: _RM_POLICIES,
    Role.TEAM_ADMIN: _RM_POLICIES,
    Role.ZONAL_MANAGER: _ZM_POLICIES,
    Role.BUSINESS_HEAD: None,
    Role.FINANCE_ADMIN: None,
    Role.SUPER_ADMIN: None,
}


def can_see_policy(role: Role, scope: str) -> bool:
    allowed = ROLE_POLICY_SCOPES.get(role, _BDE_POLICIES)
    return allowed is None or scope in allowed


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


@dataclass
class Principal:
    """The authenticated caller. Built from the JWT plus the employee master."""

    email: str
    employee_id: str
    full_name: str
    role: Role
    region: str | None = None
    zone: str | None = None
    vertical: str | None = None
    permissions: set[Permission] = field(default_factory=set)
    # Set when a super admin is viewing the app as this person: the email of
    # the admin actually at the keyboard. Such a principal is read-only.
    impersonator: str | None = None

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


def visible_employee_sql(principal: Principal) -> tuple[str, dict]:
    """The subtree this principal may see, as a SQL predicate + parameters.

    Returned as a predicate rather than a list so a Business Head's few thousand
    reports never round-trip through the application.
    """
    p = principal
    if p.can(Permission.VIEW_BUSINESS):
        if p.role in (Role.FINANCE_ADMIN, Role.SUPER_ADMIN):
            return "TRUE", {}
        return "h.vertical = @scope_vertical", {"scope_vertical": p.vertical}
    if p.can(Permission.VIEW_ZONE):
        return (
            "(h.zm_id = @scope_id OR h.employee_id = @scope_id)",
            {"scope_id": p.employee_id},
        )
    if p.can(Permission.VIEW_REGION):
        return (
            "(h.rm_id = @scope_id OR h.employee_id = @scope_id)",
            {"scope_id": p.employee_id},
        )
    if p.can(Permission.VIEW_TEAM):
        return (
            "(h.submanager_id = @scope_id OR h.employee_id = @scope_id)",
            {"scope_id": p.employee_id},
        )
    return "h.employee_id = @scope_id", {"scope_id": p.employee_id}
