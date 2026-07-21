from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .db import Database
from .domain import Plan
from .strategy import strategy_from_template


@dataclass(frozen=True, slots=True)
class PlanPreset:
    purchase_name: str
    purchase_code: str
    signal_name: str
    signal_code: str
    base_amount: Decimal
    strategy_template: str


PRD_PLAN_PRESETS = (
    PlanPreset("易方达中证 A500ETF 联接 A", "022459", "A500ETF 易方达", "159361", Decimal("600"), "core"),
    PlanPreset("国泰半导体材料设备 ETF 联接 A", "019632", "半导体设备 ETF 国泰", "159516", Decimal("250"), "sector"),
    PlanPreset("易方达创新药 ETF 联接 A", "019666", "创新药 ETF 易方达", "516080", Decimal("200"), "sector"),
    PlanPreset("招商畜牧养殖 ETF 联接 C", "014415", "畜牧养殖 ETF 招商", "516670", Decimal("300"), "sector"),
)


def install_prd_plan_presets(database: Database) -> int:
    """Install the PRD example plans once without replacing user data."""
    if database.get_setting("system.prd_plan_presets_installed", "false") == "true":
        return 0
    existing = {(plan.purchase_code, plan.signal_code) for plan in database.list_plans()}
    installed = 0
    for preset in PRD_PLAN_PRESETS:
        key = (preset.purchase_code, preset.signal_code)
        if key in existing:
            continue
        plan = Plan(
            name=preset.signal_name,
            purchase_code=preset.purchase_code,
            purchase_name=preset.purchase_name,
            signal_code=preset.signal_code,
            signal_name=preset.signal_name,
            base_amount=preset.base_amount,
        )
        database.add_plan_with_strategy(
            plan, strategy_from_template(plan.id, preset.strategy_template)
        )
        installed += 1
    database.set_setting("system.prd_plan_presets_installed", "true")
    return installed
