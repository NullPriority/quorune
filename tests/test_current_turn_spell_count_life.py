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
from quorune.event_player_result_values import SPELL_CAST_COUNT_HISTORY, SpellCastCountLifeSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
RUG = "Rug of Smothering"
RESERVOIR = "Aetherflux Reservoir"
ORIGINALS = (RESERVOIR, RUG)
SPELL_CAST = "trigger.event.normalized_spell_cast"
LIFE_CHANGE = "life.change.effect"
FIXTURES = ROOT / "tests" / "fixtures"


class CurrentTurnSpellCountLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Counterspell", "Grizzly Bears", "Twincast"},
            "attached-cast-restriction-cards.json": {"Seal of Cleansing"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "current-turn-spell-count-life-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "current-turn-spell-count-life.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current turn spell-count life", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (program for program in programs if program.event == "spell.cast")
        return card, trigger, programs

    def test_complete_originals_bind_current_spell_count_and_keep_their_siblings(self):
        for name, recipient in ((RUG, "event_caster"), (RESERVOIR, "controller")):
            with self.subTest(name=name):
                card, trigger, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual("battlefield", trigger.active_zone)
                self.assertIsNone(trigger.target_schema)
                spec = SpellCastCountLifeSpec(recipient)
                self.assertEqual(spec.event_condition, thaw_value(trigger.event_condition))
                self.assertEqual([spec.effect()], thaw_value(trigger.effects))
                self.assertTrue({SPELL_CAST, LIFE_CHANGE, *SPELL_CAST_COUNT_HISTORY.capability_dependencies}
                                <= set(trigger.capability_dependencies))
                if name == RUG:
                    self.assertIn("Flying", card.oracle_text)
                    self.assertTrue(any("combat.block.flying" in p.capability_dependencies for p in programs))
                else:
                    activation, = (program for program in programs if program.event == "activate")
                    damage, = activation.effects
                    self.assertEqual(("damage", 50), (damage["op"], damage["amount"]))

    def test_unqualified_event_matching_result_and_history_recipient_are_required(self):
        card, program, _ = self.original_programs(RUG)
        result, = thaw_value(program.effects)
        amount = result["amount"]
        mutations = (
            {"active_zone": "graveyard"},
            {"event": "card.drawn", "coverage": ()},
            {"event_condition": {"field": "controller", "op": "eq", "value": "$source.controller"}},
            {"effects": [{**result, "player": "$controller"}]},
            {"effects": [{**result, "amount": {**amount, "recipient": "controller"}}]},
            {"effects": [{**result, "amount": {**amount, "schema_version": True}}]},
            {"effects": [{**result, "amount": {**amount, "schema_version": 1.0}}]},
            {"effects": [{**result, "amount": {**amount, "field": "spell_cast_count"}}]},
            {"effects": [{**result, "op": "damage"}]},
            {"effects": [result, result]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1}},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        for old, new in (("lose 1 life", "lose 2 life"), ("a player casts", "an opponent casts"),
                         ("each spell", "each instant"), ("this turn", "last turn")):
            with self.subTest(text=new):
                ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def _triggers(self, game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    def _accept(self, game, actor, command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _activate(self, game, actor, source, target):
        self.priority(game, actor)
        action, = (row for row in game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + source.ref + ":"))
        self._accept(game, actor, {"action_id": action["id"], "targets": [target], "pay": "auto"})

    def _add_islands(self, game, seat, count):
        for index in range(count):
            self.add(game.engine, "Island", seat=seat, ref=f"spell-count-{seat}-island-{index}")

    def test_actual_rug_counts_each_casters_responses_and_excludes_the_twincast_copy(self):
        game = self.session(76156741)
        rug = self.add(game.engine, RUG)
        seal = self.add(game.engine, "Seal of Cleansing", seat="C")
        spell_a = self.add(game.engine, "Unsummon", zone="hand")
        spell_b = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="rug-b-first-spell")
        twincast = self.add(game.engine, "Twincast", seat="B", zone="hand")
        targets = {seat: self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"rug-target-{seat}") for seat in "BCD"}
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="rug-private-response")
        self._add_islands(game, "B", 3)
        for card in (rug, seal, spell_a, spell_b, twincast):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)

        self.cast(game, "A", spell_a, [targets["B"].ref])
        original = next(item for item in game.state.stack if item.card_object_id == spell_a.object_id)
        self.cast(game, "B", spell_b, [targets["C"].ref])
        self.cast(game, "B", twincast, [original.ref])
        triggers = self._triggers(game, rug)
        self.assertEqual([("A", []), ("B", []), ("B", [])],
                         [(item.context["player"], item.targets) for item in triggers])
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])

        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(38, game.state.players["B"].life)
        copied_choice = {"action_id": "choose", "targets": [targets["D"].ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", copied_choice).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._accept(game, "B", copied_choice)
        copy, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual([targets["D"].ref], copy.targets)
        self.assertEqual(["A", "B"], [item.context["player"] for item in self._triggers(game, rug)])
        self.assertEqual(3, len(game.engine._current_turn_history("spell_cast")))

        self._activate(game, "C", seal, rug.ref)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[rug.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([39, 36, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertTrue(all(game.state.cards[c.object_id].zone == "hand" for c in targets.values()))
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_reservoir_counts_a_countered_spell_and_keeps_fifty_life_activation(self):
        game = self.session(76156742)
        reservoir = self.add(game.engine, RESERVOIR)
        first = self.add(game.engine, "Unsummon", zone="hand", ref="reservoir-countered")
        second = self.add(game.engine, "Unsummon", zone="hand", ref="reservoir-response")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        first_target = self.add(game.engine, "Grizzly Bears", seat="B", ref="reservoir-kept-target")
        second_target = self.add(game.engine, "Grizzly Bears", seat="C", ref="reservoir-moved-target")
        self._add_islands(game, "B", 2)
        for card in (reservoir, first, second, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        game.state.players["A"].life = 60
        game.state.players["D"].life = 60
        self.checkpoint(game)

        self.cast(game, "A", first, [first_target.ref])
        first_spell = next(item for item in game.state.stack if item.card_object_id == first.object_id)
        self.cast(game, "B", counter, [first_spell.ref])
        self.cast(game, "A", second, [second_target.ref])
        self.assertEqual(["A", "A"], [item.context["player"] for item in self._triggers(game, reservoir)])
        self.assertEqual(60, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(64, game.state.players["A"].life)
        self.assertEqual("battlefield", game.state.cards[first_target.object_id].zone)
        self.assertEqual("hand", game.state.cards[second_target.object_id].zone)

        self._activate(game, "A", reservoir, "D")
        self.assertEqual(14, game.state.players["A"].life)
        self.assertEqual(60, game.state.players["D"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(10, game.state.players["D"].life)
        self.assertEqual([40, 40], [game.state.players[seat].life for seat in "BC"])
        self.replay(game, load=True)

    def test_actual_departed_rug_caster_leaves_no_life_result_for_another_player(self):
        game = self.session(76156743)
        rug = self.add(game.engine, RUG)
        spell = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        target = self.add(game.engine, "Grizzly Bears")
        self._add_islands(game, "B", 1)
        self.assert_closed(game, rug)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "B", spell, [target.ref])
        self.priority(game, "B")
        self._accept(game, "B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertNotIn("B", game.engine.active_seats)
        self.assertEqual(["B"], [item.context["player"] for item in self._triggers(game, rug)])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 40, 40], [game.state.players[seat].life for seat in "ACD"])
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_omitting_the_exact_spell_count_lowering_restores_both_original_residuals(self):
        with patch("quorune.compiler.event_player_results._spell_cast_count_life_result", return_value=None):
            for name in ORIGINALS:
                bound, _ = register_card(self.db, self.registry, name)
                self.assertFalse(bound["strict_capability_ready"], bound)
            other, _ = register_card(self.db, self.registry, "Counterspell")
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_current_history_and_life_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (*SPELL_CAST_COUNT_HISTORY.capability_dependencies, LIFE_CHANGE):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Current spell-count life dependency withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                for name in ORIGINALS:
                    bound, _ = register_card(self.db, registry, name)
                    self.assertFalse(bound["strict_capability_ready"], bound)
                other, _ = register_card(self.db, registry, "Counterspell")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
