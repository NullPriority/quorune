"""A legal target's owner chooses its library position during the original resolution."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import OWNER_LIBRARY_POSITION_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.die_roll_occurrence import plan_fixed_die_roll
from quorune.rules.random_selection import roll_fixed_die
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class OwnerLibraryPositionChoiceTests(InheritedNativeFixture, unittest.TestCase):
    spell = "Trial Ordered Return"
    followup = "Trial Ordered Insight"
    body = "Trial Refuge Creature"
    bounce = "Trial Trail Retreat"
    basic = "Generic Bound Plains"
    spell_mana = {"C": 1, "U": 1}

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "owner-library-position.sqlite3"
        fixture = ROOT / "tests/fixtures"
        build_fixture_database([
            fixture / "bound-effect-program-cards.json",
            fixture / "source-counter-reflexive-cards.json",
            fixture / "source-optional-creature-unblockable-cards.json",
            fixture / "owner-library-position-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Owner library placement", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [name])

    def scene(self, seed, *, spell_name=None):
        game = self.session(seed)
        spell = self.add(game.engine, spell_name or self.spell, zone="hand", ref="CASTING-PLAYER-SPELL")
        target = self.add(game.engine, self.body, seat="C", ref="OWNED-BY-C")
        bounce = self.add(game.engine, self.bounce, seat="B", zone="hand", ref="HIDDEN-RESPONSE")
        game.engine.change_control(target.object_id, "B")
        for card in (spell, bounce):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=self.spell_mana)
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "A", spell, [target.ref])
        return game, spell, target, bounce

    def test_target_owner_alone_places_the_other_players_permanent_and_replays(self):
        for index, position in enumerate(("top", "bottom")):
            with self.subTest(position=position):
                game, spell, target, bounce = self.scene(4003101 + index)
                before_library = tuple(game.state.players["C"].zones["library"])
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
                action, = game.packet("pilot:C", full=True)["decision"]["legal_actions"]
                self.assertEqual(["top", "bottom"], action["choice_schema"]["legal_values"])
                self.assertNotIn(bounce.ref, json.dumps(game.packet("pilot:C", full=True)))
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                before = authoritative_state_hash(game.state)
                command = {"action_id": "choose", "choice": position}
                for principal in ("pilot:A", "pilot:B", "pilot:D"):
                    self.assertFalse(game.act(principal, command).ok)
                self.assertFalse(game.act("pilot:C", {**command, "choice": "hand"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                response = game.act("pilot:C", command)
                self.assertTrue(response.ok, response.summary)
                self.assertIsNone(self.resolve(game))
                after = tuple(game.state.players["C"].zones["library"])
                expected = (*before_library, target.object_id) if position == "top" else (target.object_id, *before_library)
                self.assertEqual(expected, after)
                self.assertEqual("library", game.state.cards[target.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_illegal_target_creates_no_owner_choice(self):
        game, spell, target, bounce = self.scene(4003103)
        before_library = tuple(game.state.players["C"].zones["library"])
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual(before_library, tuple(game.state.players["C"].zones["library"]))
        self.replay(game, load=True)

    def test_followup_library_choice_still_belongs_to_the_original_caster(self):
        game, _, target, _ = self.scene(4003104, spell_name=self.followup)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        placed = game.act("pilot:C", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(placed.ok, placed.summary)
        self.assertEqual(target.object_id, game.state.players["C"].zones["library"][0])
        self.assertEqual("library", game.state.cards[target.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        card_id = game.state.players["A"].zones["library"][-1]
        top = game.state.cards[card_id].ref
        command = {"action_id": "choose", "cards": {"top": [], "graveyard": [top]}}
        self.assertFalse(game.act("pilot:C", command).ok)
        game = self.replay(game, load=True)
        continued = game.act("pilot:A", command)
        self.assertTrue(continued.ok, continued.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[card_id].zone)
        self.replay(game, load=True)

    def test_admission_requires_the_target_owner_and_both_fixed_library_positions(self):
        record = self.db.lookup(self.spell)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(record, self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(OWNER_LIBRARY_POSITION_CAPABILITY, node.capability_dependencies)
        self.assertEqual({"kind": "target_owner_information", "schema_version": 1, "index": 0}, node.effects[0]["player"])
        for text in (record.oracle_text.replace("owner", "controller"),
                     record.oracle_text.replace("their library", "your library"),
                     record.oracle_text + " Rewrite the game."):
            self.assertNotEqual("exact", compile_card(replace(record, oracle_text=text), self.registry).status)
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in data["capabilities"] if row["id"] == OWNER_LIBRARY_POSITION_CAPABILITY)
        cap.update(status="blocked", blockers=["Withdraw the original owner's position choice."])
        review = CapabilityRegistry(data)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=data, declaration_source=declarations,
                           discovered_test_ids=set(discover_tests(ROOT).values()))
        review.mark_evidence_verified(validate_capability_evidence_index(index, registry=review))
        self.assertNotEqual("exact", compile_card(record, review).status)
        with patch("quorune.compiler.resolution_effect_templates.owner_library_position_template", return_value=None):
            self.assertNotEqual("exact", compile_card(record, self.registry).status)


class UnderdarkRiftNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / "underdark-rift.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-counter-reflexive-cards.json",
            fixtures / "source-optional-creature-unblockable-cards.json",
            fixtures / "owner-library-position-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Underdark Rift", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def scene(self, seed, *, target="Sol Ring", library_limit=None, active="A", mana=5):
        game = self.session(seed)
        source = self.add(game.engine, "Underdark Rift", ref="RIFT")
        response = self.add(game.engine, "Trial Trail Retreat", seat="B", zone="hand", ref="RIFT-PRIVATE")
        if target == "commander":
            object_id, = game.state.players["C"].zones["command"]
            victim = game.engine.move_card(object_id, "battlefield", controller="B", log=False)
        else:
            victim = self.add(game.engine, target, seat="C", ref="RIFT-TARGET")
            if target == "Karn Liberated":
                victim.counters["loyalty"] = 6
            game.engine.change_control(victim.object_id, "B")
        if library_limit is not None:
            for object_id in tuple(game.state.players["C"].zones["library"])[library_limit:]:
                game.engine.move_card(object_id, "graveyard", log=False)
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active=active, mana={"C": mana})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        return game, source, victim, response

    @staticmethod
    def rolls(game):
        return [event for event in game.state.events if event.code == "die.roll"]

    @staticmethod
    def ability(game, source, *, mana=False):
        ability, = (ability for ability in game.engine._activated_abilities(source)
                    if bool(ability.mana_ability) == mana)
        return f"activate:{source.ref}:{ability.ability_id}"

    def activate(self, game, source, target):
        action_id = self.ability(game, source)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, [offer["id"] for offer in offers])
        result = game.act("pilot:A", {"action_id": action_id, "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertEqual([], self.rolls(game))
        item, = game.state.stack
        return item

    def assert_roll(self, game, item):
        event, = self.rolls(game)
        expected = roll_fixed_die(10, seed=game.state.config.seed,
                                  instruction_key=f"{item.ref}|0", seat="A")
        self.assertEqual({"player": "A", "sides": 10, "result": expected, "stack": item.ref},
                         event.details)
        self.assertTrue(set("ABCD") <= set(event.visibility))
        return expected

    def assert_insert(self, game, target, original, result):
        index = max(0, len(original) - result)
        self.assertEqual((*original[:index], target.object_id, *original[index:]),
                         tuple(game.state.players["C"].zones["library"]))
        self.assertEqual("library", game.state.cards[target.object_id].zone)

    def test_rift_pays_and_places_each_legal_type_in_its_owners_library(self):
        scenarios = (("Sol Ring", None), ("Trial Refuge Creature", None),
                     ("Karn Liberated", 1), ("Sol Ring", 0))
        for index, (target, library_limit) in enumerate(scenarios):
            with self.subTest(target=target, library_limit=library_limit):
                game, source, victim, _ = self.scene(367912 + index, target=target, library_limit=library_limit)
                original = tuple(game.state.players["C"].zones["library"])
                item = self.activate(game, source, victim)
                self.assertIsNone(self.resolve(game))
                face = self.assert_roll(game, item)
                self.assert_insert(game, victim, original, face)
                self.replay(game, load=True)
        game, source, _, _ = self.scene(367916, mana=0)
        result = game.act("pilot:A", {"action_id": self.ability(game, source, mana=True)})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual([], self.rolls(game))
        self.replay(game, load=True)

    def test_rift_replays_a_commander_replacement_and_preserves_the_original_roll(self):
        for decline in (False, True):
            with self.subTest(decline=decline):
                game, source, victim, response = self.scene(367917, target="commander")
                original = tuple(game.state.players["C"].zones["library"])
                item = self.activate(game, source, victim)
                pending = self.resolve(game)
                self.assertEqual(("replacement.order", ["C"]), (pending.kind, pending.actors))
                face = self.assert_roll(game, item)
                packet = game.packet("pilot:C", full=True)
                self.assertNotIn(response.ref, json.dumps(packet))
                rows = packet["decision"]["ctx"]["options"]
                selected = next(row for row in rows if bool(row.get("decline")) is decline)
                command = {"action_id": "choose", "replacement": selected["id"]}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                result = game.act("pilot:C", command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(face, self.assert_roll(game, item))
                if decline:
                    self.assert_insert(game, victim, original, face)
                else:
                    self.assertEqual("command", game.state.cards[victim.object_id].zone)
                    self.assertEqual(original, tuple(game.state.players["C"].zones["library"]))
                self.replay(game, load=True)

    def test_rift_rejects_wrong_price_timing_or_target_and_skips_an_illegal_resolution(self):
        for kwargs in ({"mana": 4}, {"active": "B"}, {"target": "Generic Bound Plains"}):
            with self.subTest(kwargs=kwargs):
                game, source, victim, _ = self.scene(367918, **kwargs)
                before = authoritative_state_hash(game.state)
                result = game.act("pilot:A", {"action_id": self.ability(game, source),
                                            "targets": [victim.ref], "pay": "auto"})
                self.assertFalse(result.ok, result.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
        game, source, victim, response = self.scene(367919, target="Trial Refuge Creature")
        self.activate(game, source, victim)
        self.cast(game, "B", response, [victim.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.rolls(game))
        self.assertEqual("hand", game.state.cards[victim.object_id].zone)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_rift_exact_authority_rejects_other_costs_timing_and_result_shapes(self):
        record = self.db.lookup("Underdark Rift")
        compile_card = lambda card: compile_oracle_card(
            card, capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertEqual("exact", compile_card(record).status)
        for old, new in (("{5},", "{4},"), ("Exile this land", "Sacrifice this land"),
                         (" Activate only as a sorcery.", ""), ("a d10", "a d12"),
                         ("its owner's", "its controller's"), ("or planeswalker", "or enchantment")):
            with self.subTest(old=old):
                altered = replace(record, oracle_text=record.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_card(altered).status)
        with patch("quorune.compiler.die_roll_library_depth.die_library_depth_activation",
                   return_value=None):
            self.assertNotEqual("exact", compile_card(record).status)

    def test_fixed_die_plan_preserves_the_unmodified_sample_and_partitions_ignored_rolls(self):
        kwargs = {"seed": 6, "instruction_key": "S7|0", "seat": "A"}
        plain = plan_fixed_die_roll(10, **kwargs)
        self.assertEqual(roll_fixed_die(10, **kwargs), plain.single_result)
        extended = plan_fixed_die_roll(10, **kwargs, extra_rolls=2, ignore_lowest=1)
        self.assertEqual(plain.single_result, extended.samples[0])
        self.assertEqual((min(extended.samples),), extended.ignored_natural_results)
        self.assertEqual(extended.retained_natural_results, extended.results)
        self.assertEqual(extended, plan_fixed_die_roll(10, **kwargs, extra_rolls=2, ignore_lowest=1))
        with self.assertRaises(ValueError):
            _ = extended.single_result
