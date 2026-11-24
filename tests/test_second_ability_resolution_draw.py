from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.ability_resolution_ordinal import (
    ABILITY_RESOLVED_HISTORY, is_second_ability_draw, validate_second_ability_draw,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import resolution_condition_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_history_model import TurnHistoryEvent
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
HOST = "Harvestrite Host"
VOYAGER = "South Pole Voyager"
TANNUK = "Tannuk, Memorial Ensign"
ORIGINALS = {HOST: "modify_stats_until_end_of_turn", VOYAGER: "life", TANNUK: "damage_each_opponent"}
HISTORY = "condition.history.fixed_public_query"
DRAW = "zone.draw.library_to_hand"
RESONATOR = "Strionic Resonator"
ENVOY = "Expedition Envoy"
HOP = "Hop to It"


class SecondAbilityResolutionDrawTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Forest", "Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "fixed-qualified-stack-copy-cards.json": {RESONATOR},
            "library-search-rules-cards.json": {"Evolving Wilds"},
            "second-ability-resolution-draw-cards.json": set(ORIGINALS) | {ENVOY, HOP},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for filename, names in support.items():
            document = json.loads((FIXTURES / filename).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            subsets.append(piece)
        path = folder / "second-ability-resolution-draw.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Second ability resolution", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = [value for value in semantics.programs_for_oracle(card.oracle_id)
                    if any(is_second_ability_draw(effect) for effect in value.effects)]
        return card, program

    def test_complete_second_resolution_originals_keep_their_owned_prefix_and_exact_draw(self):
        for name, first_operation in ORIGINALS.items():
            with self.subTest(name=name):
                card, program = self._original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                first, wrapper = thaw_value(program.effects)
                self.assertEqual(first_operation, first["op"])
                self.assertIsNone(wrapper["resolution_ordinal"])
                self.assertEqual(({"op": "draw", "player": "$controller", "count": 1, "private": True},),
                                 validate_second_ability_draw(wrapper))
                self.assertLessEqual({HISTORY, DRAW, "resolution.effect.public_condition"},
                                     set(program.capability_dependencies))
                self.assertEqual(name == HOST, program.target_schema is not None)

    def test_resolution_ordinal_shapes_and_turn_history_reject_unsealed_values(self):
        _, program = self._original(HOST)
        prefix, wrapper = thaw_value(program.effects)
        for changes in ({"resolution_ordinal": 2}, {"schema_version": 20.0},
                        {"condition": {**wrapper["condition"], "ordinal": 3}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_second_ability_draw({**wrapper, **changes})
        attack = replace(program, event="creature.attacks")
        report = bind_semantic_program_runtime(attack, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"], report)
        event = TurnHistoryEvent(ABILITY_RESOLVED_HISTORY, actor="A", object_incarnation="source@1",
                                 target="ability", target_kind="ability_program")
        self.assertEqual(event, TurnHistoryEvent.from_dict(event.to_dict()))
        with self.assertRaises(ValueError):
            replace(event, target_kind="player")

    def _events(self, game, source=None):
        history = game.state.turn_history
        self.assertEqual(game.state.turn_sequence, history.turn_sequence)
        identity = game.state.cards[source.object_id].logical_object_id if source else None
        return [event for event in history.events if event.kind == ABILITY_RESOLVED_HISTORY
                and (identity is None or event.object_incarnation == identity)]

    def _hand(self, game):
        return set(game.state.players["A"].zones["hand"])

    def _assert_private_draw(self, game, before, count):
        after = self._hand(game)
        self.assertLessEqual(before, after)
        new = after - before
        self.assertEqual(count, len(new))
        for oid in new:
            ref = game.state.cards[oid].ref
            self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
            for seat in "BCD":
                self.assertNotIn(ref, json.dumps(game.packet("pilot:" + seat, full=True)))

    def _order(self, game):
        while game.state.pending_decision is not None and game.state.pending_decision.kind == "trigger.order":
            refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
            result = game.act(game.pending_principals()[0], {"action_id": "order", "triggers": refs})
            self.assertTrue(result.ok, result.summary)

    def _activate(self, game, source, targets=()):
        self.priority(game, "A")
        card = game.state.cards[source.object_id]
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [entry for entry in offers if entry["id"].startswith(f"activate:{card.ref}:")]
        result = game.act("pilot:A", {"action_id": offered["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_voyager_copy_counts_for_its_original_source_separately_from_a_same_named_source(self):
        game = self.session(77151001)
        first = self.add(game.engine, VOYAGER)
        second = self.add(game.engine, VOYAGER, ref="separate-voyager")
        resonator = self.add(game.engine, RESONATOR)
        envoy = self.add(game.engine, ENVOY, zone="hand")
        for card in (first, second, resonator, envoy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", envoy)
        self.resolve_top(game)
        self._order(game)
        copied, = [item for item in game.state.stack if item.source_object_id == first.object_id]
        self.assertEqual(2, len(game.state.stack))
        self._activate(game, resonator, [copied.ref])
        self.resolve_top(game)
        self.assertEqual(3, len(game.state.stack))
        self.assertEqual([], self._events(game))
        before = self._hand(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self._assert_private_draw(game, before, 1)
        self.assertEqual(2, len(self._events(game, first)))
        self.assertEqual(1, len(self._events(game, second)))
        self.assertEqual({copied.semantic_key}, {event.target for event in self._events(game, first)})
        self.replay(game, load=True)

    def test_actual_harvestrite_excludes_its_illegal_target_and_draws_only_on_the_second_valid_resolution(self):
        game = self.session(77151010)
        host = self.add(game.engine, HOST, zone="hand")
        victim = self.add(game.engine, "Walking Corpse")
        rabbits = self.add(game.engine, HOP, zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        for card in (host, victim, rabbits, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "W": 2})
        self.checkpoint(game)
        self.cast(game, "A", host)
        self.resolve_top(game)
        self.target(game, "A", [victim.ref])
        self.cast(game, "B", bounce, [victim.ref])
        self.resolve_top(game)
        before = self._hand(game)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before, 0)
        self.assertEqual([], self._events(game, host))
        starting_power = game.engine._numeric_stat(host.object_id, "power")
        self.cast(game, "A", rabbits)
        self.resolve_top(game)
        self._order(game)
        self.assertEqual(3, self.trigger_count(game, host))
        for _ in range(3):
            self.target(game, "A", [host.ref])
        before = self._hand(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before, 1)
        self.assertEqual(3, len(self._events(game, host)))
        self.assertEqual(starting_power + 3, game.engine._numeric_stat(host.object_id, "power"))
        self.replay(game, load=True)

    def _fetch(self, game, source, desired):
        self._activate(game, source)
        self.assertEqual("search.fetch", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "search_card": desired.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_tannuk_keeps_each_damage_prefix_and_resets_the_ordinal_on_another_turn(self):
        game = self.session(77151020)
        source = self.add(game.engine, TANNUK)
        first_land = self.add(game.engine, "Evolving Wilds", zone="hand")
        spare_land = self.add(game.engine, "Evolving Wilds", ref="wilds-for-next-turn")
        first_basic = self.add(game.engine, "Forest", zone="library", ref="tannuk-first-basic")
        second_basic = self.add(game.engine, "Forest", zone="library", ref="tannuk-next-turn-basic")
        for card in (source, first_land, spare_land):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        result = game.act("pilot:A", {"action_id": "play-land:" + first_land.ref})
        self.assertTrue(result.ok, result.summary)
        before = self._hand(game)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before, 0)
        self.assertEqual(1, len(self._events(game, source)))
        self.assertEqual((39, 39, 39), tuple(game.state.players[s].life for s in "BCD"))
        self._fetch(game, first_land, first_basic)
        self._assert_private_draw(game, before, 1)
        self.assertEqual(2, len(self._events(game, source)))
        self.assertEqual((38, 38, 38), tuple(game.state.players[s].life for s in "BCD"))
        self.finish_turn(game)
        self.assertEqual("B", game.state.active_player)
        self.assertEqual([], self._events(game, source))
        self.priority(game, "A")
        before = self._hand(game)
        self._fetch(game, spare_land, second_basic)
        self._assert_private_draw(game, before, 0)
        self.assertEqual(1, len(self._events(game, source)))
        self.assertEqual((37, 37, 37), tuple(game.state.players[s].life for s in "BCD"))
        self.replay(game, load=True)

    def test_omitting_the_second_ability_draw_lowering_restores_the_three_originals(self):
        with patch.object(resolution_condition_templates, "_second_ability_draw_template", return_value=None):
            for name in ORIGINALS:
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_history_and_draw_authority_withdrawals_deny_the_exact_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (HISTORY, DRAW):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped ability ordinal or private draw authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    result, _ = register_card(self.db, registry, name)
                    self.assertFalse(result["strict_capability_ready"], result)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
