"""Pinned Commander format authority for the companion role."""

from datetime import date
from functools import cache
import json
from pathlib import Path
from uuid import UUID


COMPANION_ROLE_CAPABILITY = "setup.commander.companion_role_ban"
COMPANION_ROLE_EVENT = "setup.commander.companion_role"
COMPANION_ROLE_TEMPLATE = "commander-companion-role-ban-v1"
COMPANION_ROLE_PROFILES = frozenset(("commander_duel", "commander_review"))
SINGLETON_COMPANION_CLAUSE = "companion — each nonland card in your starting deck has a different name."


@cache
def companion_ban_oracle_ids():
    data = json.loads(Path(__file__).with_name("commander-companion-bans.json").read_bytes())
    if (set(data) != {"schema_version", "format", "banned_role", "effective", "authority", "oracle_ids"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["format"] != "commander" or data["banned_role"] != "companion"
            or not data["authority"].startswith("https://magic.wizards.com/en/news/announcements/")):
        raise ValueError("Invalid pinned Commander companion-role authority")
    date.fromisoformat(data["effective"])
    ids = data["oracle_ids"]
    if not isinstance(ids, list) or not ids or any(type(oid) is not str or str(UUID(oid)) != oid for oid in ids):
        raise ValueError("Invalid Commander companion-ban Oracle identities")
    if len(set(ids)) != len(ids):
        raise ValueError("Commander companion-ban Oracle identities must be unique")
    return frozenset(ids)


def commander_companion_is_banned(oracle_id):
    return oracle_id in companion_ban_oracle_ids()


def validate_commander_companion_roles(card_db, deck):
    for entry in deck.entries:
        if entry.board == "companion":
            card = card_db.lookup(entry.name)
            if commander_companion_is_banned(card.oracle_id):
                raise ValueError(f"Commander legality: {card.name} is banned as a companion")


def companion_role_runtime_dependencies(program):
    if (program.event != COMPANION_ROLE_EVENT and COMPANION_ROLE_CAPABILITY not in program.capability_dependencies
            and program.provenance.get("template_id") != COMPANION_ROLE_TEMPLATE):
        return ()
    if ((program.event, program.active_zone) != (COMPANION_ROLE_EVENT, "all")
            or program.provenance.get("template_id") != COMPANION_ROLE_TEMPLATE
            or not commander_companion_is_banned(program.oracle_id)
            or program.effects or program.handlers or program.destination is not None
            or program.target_schema is not None or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Commander companion-role closure requires the pinned nonexecutable setup declaration")
    return (COMPANION_ROLE_CAPABILITY,)
