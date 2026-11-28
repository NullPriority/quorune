"""Native attack and spell-target occurrences with the printed Treasure grant."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_announcements import (
    SOURCE_ATTACK_OR_SPELL_TARGET_CAPABILITIES, TARGET_ANNOUNCEMENT_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database


SOURCE = "Goldspan Dragon"
PROFILE = "commander_review"


class SourceAttackOrSpellTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixture_names = (
            "source-attack-or-spell-target-cards", "opponent-spell-or-permanent-return-cards",
            "ability-source-counter-cards", "target-announcements", "announced-x-source-sacrifice-cards",
        )
        path = Path(cls.temporary.name) / "attack-or-spell-target.sqlite3"
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixture_names], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Attack or spell target", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def treasures(self, game, controller="A"):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.controller == controller and card.printed_name == "Treasure Token"]

    def activate(self, game, seat, card, *, ability_id=None, **selection):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        matches = [row for row in actions if row["id"].startswith(f"activate:{card.ref}:")
                   and (ability_id is None or row["id"] == f"activate:{card.ref}:{ability_id}")]
        self.assertEqual(1, len(matches), matches)
        result = game.act("pilot:" + seat, {"action_id": matches[0]["id"], "pay": "auto", **selection})
        self.assertTrue(result.ok, result.summary)

    def mana_abilities(self, game, card):
        current = game.state.cards[card.object_id]
        return [ability for ability in game.engine._activated_abilities(current) if ability.mana_ability]

    def test_exact_original_and_independent_subscription_authority(self):
        game = self.session(35900)
        source = self.add(game.engine, SOURCE)
        self.assert_closed(game, source)
        record = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = (node for node in ir.faces[0].nodes if node.event == "creature.attacks")
        self.assertEqual(record.oracle_text.splitlines()[1], trigger.text)
        self.assertEqual(["creature.attacks", "object.became_target"],
                         [child["event"] for child in trigger.event_condition["event_subscriptions"]])
        self.assertEqual("create_token", trigger.effects[0]["op"])
        self.assertEqual("Treasure Token", trigger.effects[0]["name"])
        self.assertEqual(1, trigger.effects[0]["quantity"])
        self.assertTrue(set(SOURCE_ATTACK_OR_SPELL_TARGET_CAPABILITIES) <= set(trigger.capability_dependencies))
        program, = (candidate for candidate in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if candidate.event == "creature.attacks")
        condition = deepcopy(program.event_condition)
        condition["event_subscriptions"].pop(0)
        with self.assertRaisesRegex(ValueError, "at least two subscriptions"):
            replace(program, event_condition=condition)
        for mutation in ("allow_ability", "target_other", "token_quantity"):
            condition = deepcopy(program.event_condition)
            effects = deepcopy(program.effects)
            subscriptions = condition["event_subscriptions"]
            if mutation == "allow_ability":
                subscriptions[1]["condition"]["all"][1]["value"].append("activated_ability")
            elif mutation == "target_other":
                subscriptions[1]["condition"]["all"][0]["value"] = "$source.controller"
            else:
                effects[0]["quantity"] = True
            with self.subTest(mutation=mutation):
                self.assertFalse(bind_semantic_program_runtime(replace(program, event_condition=condition, effects=effects),
                    capability_registry=self.registry, profile=PROFILE)["strict"])
        remaining = tuple(cap for cap in program.capability_dependencies if cap != TARGET_ANNOUNCEMENT_CAPABILITY)
        closure = self.registry.closure(remaining, profile=PROFILE).to_dict()
        denied = replace(program, capability_dependencies=remaining, capability_closure=closure,
                         provenance={**program.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        proof = bind_semantic_program_runtime(denied, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + TARGET_ANNOUNCEMENT_CAPABILITY, proof["blockers"])

    def test_exact_public_source_and_token_grammar_fail_closed(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        for original, replacement in (
            ("this creature attacks", "another creature attacks"),
            ("target of a spell", "target of a spell or ability"),
            ("create a Treasure token", "create a token with undefined characteristics"),
        ):
            edited = replace(record, oracle_text=record.oracle_text.replace(original, replacement))
            with self.subTest(oracle=edited.oracle_text):
                ir = compile_oracle_card(edited, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.fixed_public_multi_event_bindings._source_attack_or_spell_target_spec", return_value=None):
            ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status)

    def test_actual_attack_haste_treasure_and_two_mana_grant_replay(self):
        game = self.session(35901)
        source = self.add(game.engine, SOURCE, zone="hand")
        own_hound = self.add(game.engine, "Goldhound", ref="own-goldhound")
        other_hound = self.add(game.engine, "Goldhound", seat="B", ref="foreign-goldhound")
        ordinary_land = self.add(game.engine, "Treasure Vault")
        self.prepare(game, mana={"C": 3, "R": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertTrue({"flying", "haste"} <= set(game.engine._combat_keywords(current)))
        own_hound_mana = self.mana_abilities(game, own_hound)
        other_hound_mana = self.mana_abilities(game, other_hound)
        self.assertEqual(1, len(other_hound_mana))
        self.assertEqual(2, len(own_hound_mana))
        self.assertEqual(1, len(self.mana_abilities(game, ordinary_land)))
        self.attack(game, [(current, "B")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual([], self.treasures(game))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        treasure, = self.treasures(game)
        abilities = self.mana_abilities(game, treasure)
        self.assertEqual(2, len(abilities), abilities)
        granted, = (ability for ability in abilities if "ability:granted:" in ability.ability_id)
        self.activate(game, "A", treasure, ability_id=granted.ability_id, mana_output={"U": 2})
        self.assertEqual(2, game.state.players["A"].mana_pool["U"])
        self.assertFalse(game.state.stack)
        self.assertFalse(self.treasures(game))
        self.replay(game, load=True)

    def test_actual_spell_target_counter_independence_and_ability_exclusion_replay(self):
        game = self.session(35902)
        source = self.add(game.engine, SOURCE)
        other = self.add(game.engine, "Grizzly Bears", seat="D", ref="not-goldspan")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B")
        pinger.acquired_control_turn_count = -1
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        unrelated = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="bounce-wrong-creature")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        hidden = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="hidden-goldspan-control")
        game.state.players["B"].mana_pool["U"] = 2
        game.state.players["C"].mana_pool["U"] = 2
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "B", pinger, targets=[source.ref])
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.cast(game, "B", unrelated, [other.ref])
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.priority(game, "B")
        action = self.action(game, "B", bounce)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "B", bounce, [source.ref])
        origin, trigger = game.state.stack[-2:]
        self.assertEqual(("triggered_ability", "A"), (trigger.kind, trigger.controller))
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.cast(game, "C", counter, [origin.ref])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertNotIn(origin.ref, {item.ref for item in game.state.stack})
        self.assertEqual(trigger.ref, game.state.stack[-1].ref)
        self.resolve_top(game)
        self.assertEqual(1, len(self.treasures(game)))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_own_spell_target_and_source_departure_keeps_intrinsic_mana(self):
        game = self.session(35903)
        source = self.add(game.engine, SOURCE)
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", bounce, [source.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        treasure, = self.treasures(game)
        self.assertEqual(2, len(self.mana_abilities(game, treasure)))
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        intrinsic, = self.mana_abilities(game, treasure)
        self.activate(game, "A", treasure, ability_id=intrinsic.ability_id, mana_choice="W")
        self.assertEqual(1, game.state.players["A"].mana_pool["W"])
        self.assertEqual([], self.treasures(game))
        self.replay(game, load=True)

    def test_actual_spell_copy_retargets_to_source_and_creates_one_treasure(self):
        game = self.session(35904)
        source = self.add(game.engine, SOURCE)
        other = self.add(game.engine, "Grizzly Bears", seat="D")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        copier = self.add(game.engine, "Twincast", seat="C", zone="hand")
        game.state.players["B"].mana_pool["U"] = 1
        game.state.players["C"].mana_pool["U"] = 2
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "B", bounce, [other.ref])
        original = game.state.stack[-1]
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast(game, "C", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:C", {"action_id": "choose", "targets": [source.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        spell_copy, trigger = game.state.stack[-2:]
        self.assertEqual(("spell_copy", [source.ref]), (spell_copy.kind, spell_copy.targets))
        self.assertEqual(("triggered_ability", "A"), (trigger.kind, trigger.controller))
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual(1, len(self.treasures(game)))
        self.assertEqual(spell_copy.ref, game.state.stack[-1].ref)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        self.assertEqual(1, len(self.treasures(game)))
        self.replay(game, load=True)
