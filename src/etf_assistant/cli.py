from __future__ import annotations

import argparse
import getpass
import json
import sys
from decimal import Decimal
from pathlib import Path

from .backup import export_backup, merge_backup, restore_backup, validate_backup
from .config import default_database_path
from .db import Database
from .domain import EffectiveMode, ExecutionMode, Plan, Strategy, StrategyLevel
from .strategy import default_strategy
from .providers.credentials import LocalCredentialStore
from .runtime import run_daily_check
from .scheduler import install_scheduler, remove_scheduler


def _database(value: str | None) -> Database:
    return Database(Path(value) if value else default_database_path())


def _strategy_from_json(plan: Plan, path: Path) -> Strategy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    levels = tuple(
        StrategyLevel(
            threshold=Decimal(str(item["threshold"])),
            multiplier=Decimal(str(item["multiplier"])),
            execution_mode=ExecutionMode(item.get("execution_mode", "once")),
            phases=int(item.get("phases", 1)),
            interval_weeks=int(item.get("interval_weeks", 1)),
            enabled=bool(item.get("enabled", True)),
            amount_cap=Decimal(str(item["amount_cap"])) if item.get("amount_cap") else None,
            max_executions=(
                int(item.get("max_executions", 4))
                if item.get("execution_mode") == "weekly_while_deep" else None
            ),
            cycle_amount_cap=(
                Decimal(str(item["cycle_amount_cap"]))
                if item.get("cycle_amount_cap") else None
            ),
            cycle_multiplier_cap=(
                Decimal(str(item.get("cycle_multiplier_cap", 4)))
                if item.get("execution_mode") == "weekly_while_deep" else None
            ),
            note=str(item.get("note", "")),
        )
        for item in payload["levels"]
    )
    return Strategy(
        plan_id=plan.id,
        version=1,
        levels=levels,
        effective_mode=EffectiveMode.IMMEDIATE,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="etf-assistant")
    parser.add_argument("--db", help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize the local database")

    add = commands.add_parser("add-plan", help="create a plan and strategy")
    add.add_argument("--name", required=True)
    add.add_argument("--purchase-code", required=True)
    add.add_argument("--purchase-name", default="")
    add.add_argument("--signal-code", required=True)
    add.add_argument("--signal-name", default="")
    add.add_argument("--base-amount", required=True, type=Decimal)
    add.add_argument("--weekday", type=int, default=3, choices=range(5))
    add.add_argument("--strategy-json", type=Path)

    commands.add_parser("list-plans", help="list configured plans")
    export = commands.add_parser("export", help="create a complete migration package")
    export.add_argument("path", type=Path)
    validate = commands.add_parser("validate-backup", help="validate a migration package")
    validate.add_argument("path", type=Path)
    restore = commands.add_parser("restore", help="replace the database from a migration package")
    restore.add_argument("path", type=Path)
    merge = commands.add_parser("merge", help="merge a migration package into the database")
    merge.add_argument("path", type=Path)
    merge.add_argument("--conflict", choices=("local", "import"), default="local")
    check = commands.add_parser("daily-check", help="run the market check once")
    check.add_argument("--force", action="store_true", help="allow a manual check outside 14:45-15:00")
    secret = commands.add_parser("set-secret", help="save a secret in the local app data file")
    secret.add_argument("key", choices=("smtp.password",))
    secret.add_argument("value", nargs="?", help="omit to enter without echoing or shell history")
    commands.add_parser("install-scheduler", help="install the 14:50 operating-system task")
    commands.add_parser("remove-scheduler", help="remove the operating-system task")
    setting = commands.add_parser("set-setting", help="save a non-sensitive application setting")
    setting.add_argument("key")
    setting.add_argument("value")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = _database(args.db)
    if args.command == "init":
        database.initialize()
        print(database.path)
    elif args.command == "add-plan":
        database.initialize()
        plan = Plan(
            name=args.name,
            purchase_code=args.purchase_code,
            purchase_name=args.purchase_name,
            signal_code=args.signal_code,
            signal_name=args.signal_name,
            base_amount=args.base_amount,
            invest_weekday=args.weekday,
        )
        strategy = (
            _strategy_from_json(plan, args.strategy_json)
            if args.strategy_json
            else default_strategy(plan.id)
        )
        database.add_plan_with_strategy(plan, strategy)
        print(plan.id)
    elif args.command == "list-plans":
        database.initialize()
        for plan in database.list_plans():
            strategy = database.get_active_strategy(plan.id)
            levels = ", ".join(
                f"{level.threshold}%×{level.multiplier}" for level in strategy.levels if level.enabled
            )
            print(f"{plan.id}  {plan.name}  {plan.purchase_code}->{plan.signal_code}  {levels}")
    elif args.command == "export":
        print(export_backup(database, args.path))
    elif args.command == "validate-backup":
        print(json.dumps(validate_backup(args.path), ensure_ascii=False, indent=2))
    elif args.command == "restore":
        rollback = restore_backup(args.path, database)
        print(f"restored; rollback={rollback}")
    elif args.command == "merge":
        merge_backup(args.path, database, conflict=args.conflict)
        print("merged")
    elif args.command == "daily-check":
        result = run_daily_check(database, force=args.force)
        print(json.dumps({
            "checked_plans": result.checked_plans,
            "created_events": [str(value) for value in result.created_events],
            "errors": list(result.errors),
        }, ensure_ascii=False, indent=2))
    elif args.command == "set-secret":
        value = args.value or getpass.getpass(f"Enter {args.key}: ")
        LocalCredentialStore().set(args.key, value)
        print(f"saved {args.key} in the local app data file")
    elif args.command == "install-scheduler":
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--db", str(database.path), "daily-check"]
        else:
            command = [
                sys.executable, "-m", "etf_assistant.cli", "--db", str(database.path), "daily-check"
            ]
        print(install_scheduler(command))
    elif args.command == "remove-scheduler":
        remove_scheduler()
        print("scheduler removed")
    elif args.command == "set-setting":
        database.initialize()
        database.set_setting(args.key, args.value)
        print(f"saved {args.key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
