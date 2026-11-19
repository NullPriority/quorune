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
from quorune.replacement.immutable import thaw_value
from quorune.ring_state import current_ring_state
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_outcome_shapes import CAST_EVENT_COUNTER_CAPABILITY, zero_mana_cast_counter_recheck
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
BOROMIR = "Boromir, Warden of the Tower"
BAUBLE = "Vexing Bauble"
ORIGINALS = (BOROMIR, BAUBLE)
SPELL_CAST = "trigger.event.normalized_spell_cast"
FIXTURES = ROOT / "tests" / "fixtures"
ZERO_PAID = {"field": "mana_spent_total", "op": "eq", "value": 0}
OPPONENT = {"field": "controller", "op": "ne", "value": "$source.controller"}


class ZeroManaCastCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Tormod's Crypt"},
            "ability-source-counter-cards.json": {"Counterspell", "Grizzly Bears"},
            "activation-result-views-cards.json": {"Ornithopter"},
            "attached-cast-restriction-cards.json": {"Seal of Cleansing"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "bounded-hand-cast-cards.json": {"Thalia, Guardian of Thraben"},
            "current-turn-spell-count-life-cards.json": {"Rug of Smothering"},
            "expend-cards.json": {"Frogmite"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "zero-mana-cast-counter-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "zero-mana-cast-counters.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Zero committed mana", [
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

    def test_complete_originals_keep_caster_relation_and_independent_abilities(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, trigger, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual("battlefield", trigger.active_zone)
                self.assertIsNone(trigger.target_schema)
                self.assertEqual({"all": [OPPONENT, ZERO_PAID]} if name == BOROMIR else ZERO_PAID,
                                 thaw_value(trigger.event_condition))
                self.assertEqual([{"op": "counter_stack_target", "stack": "$context.stack"}],
                                 thaw_value(trigger.effects))
                self.assertIn("intervening_condition", trigger.coverage)
                self.assertTrue({SPELL_CAST, CAST_EVENT_COUNTER_CAPABILITY, "stack.counter.effect"}
                                <= set(trigger.capability_dependencies))
                activation, = (program for program in programs if program.event == "activate")
                if name == BOROMIR:
                    self.assertIn("the-ring-tempts-you", activation.coverage)
                    self.assertIn("indestructible", activation.coverage)
                    self.assertIn("Vigilance", card.oracle_text)
                else:
                    self.assertEqual(("draw", 1), (activation.effects[0]["op"], activation.effects[0]["count"]))

    def test_malformed_condition_scope_and_legacy_missing_payment_fail_closed(self):
        card, program, _ = self.original_programs(BAUBLE)
        effect, = thaw_value(program.effects)
        cases = (
            {"event": "spell.cast_or_copy"},
            {"active_zone": "command"},
            {"coverage": ()},
            {"event_condition": {**ZERO_PAID, "value": False}},
            {"event_condition": {**ZERO_PAID, "value": 0.0}},
            {"event_condition": {**ZERO_PAID, "value": 1}},
            {"event_condition": {**ZERO_PAID, "op": "lte"}},
            {"event_condition": {"any": [ZERO_PAID, OPPONENT]}},
            {"event_condition": {"all": [{**OPPONENT, "op": "eq"}, ZERO_PAID]}},
            {"effects": [{**effect, "stack": "$target.0"}]},
            {"effects": [effect, {"op": "draw", "player": "$controller", "count": 1}]},
            {"target_schema": {"zones": ["stack"], "categories": ["spell"], "count": 1}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
        for old, new in (("a player casts a spell", "you cast a spell"),
                         ("no mana was spent", "one mana was spent"),
                         ("counter that spell", "draw a card")):
            with self.subTest(text=new):
                ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

        context = {"event": "spell.cast", "stack": "stack-1"}
        self.assertTrue(zero_mana_cast_counter_recheck(program, {**context, "mana_spent_total": 0}))
        self.assertFalse(zero_mana_cast_counter_recheck(program, context))
        for invalid in (False, 0.0, 1, -1, "0"):
            self.assertFalse(zero_mana_cast_counter_recheck(program, {**context, "mana_spent_total": invalid}))
        self.assertFalse(zero_mana_cast_counter_recheck(program, {**context, "event": "spell.copy", "mana_spent_total": 0}))

    def _trigger_for(self, game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    def _spell(self, game, source):
        return next(item for item in game.state.stack if item.card_object_id == source.object_id)

    def _accept(self, game, actor, command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _activate(self, game, actor, source, targets=()):
        self.priority(game, actor)
        action, = (row for row in game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + source.ref + ":"))
        self._accept(game, actor, {"action_id": action["id"], "targets": list(targets), "pay": "auto"})

    def test_actual_affinity_cast_is_legal_counts_for_other_effects_and_survives_source_departure(self):
        game = self.session(76156761)
        bauble = self.add(game.engine, BAUBLE)
        rug = self.add(game.engine, "Rug of Smothering", seat="C")
        seal = self.add(game.engine, "Seal of Cleansing", seat="C")
        frogmite = self.add(game.engine, "Frogmite", zone="hand")
        for index in range(3):
            self.add(game.engine, "Ornithopter", ref=f"bauble-affinity-source-{index}")
        for card in (bauble, rug, seal, frogmite):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

        self.cast(game, "A", frogmite)
        cast = self._spell(game, frogmite)
        trigger, = self._trigger_for(game, bauble)
        self.assertEqual(("A", [], cast.ref, 0, 4),
            (trigger.controller, trigger.targets, trigger.context["stack"],
             trigger.context["mana_spent_total"], trigger.context["mana_value"]))
        self.assertEqual(1, len(self._trigger_for(game, rug)))
        self.assertEqual(1, len(game.engine._current_turn_history("spell_cast")))

        self._activate(game, "C", seal, [bauble.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[bauble.object_id].zone)
        self.assertEqual(1, len(self._trigger_for(game, bauble)))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[frogmite.object_id].zone)
        self.assertEqual(39, game.state.players["A"].life)
        self.assertEqual(1, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_printed_zero_with_tax_resolves_and_bauble_retains_paid_draw_activation(self):
        game = self.session(76156762)
        bauble = self.add(game.engine, BAUBLE)
        tax = self.add(game.engine, "Thalia, Guardian of Thraben", seat="B")
        crypt = self.add(game.engine, "Tormod's Crypt", zone="hand")
        for card in (bauble, tax, crypt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.cast(game, "A", crypt)
        cast = self._spell(game, crypt)
        self.assertEqual(1, cast.context["mana_spent_total"])
        self.assertNotIn(bauble.object_id, [item.source_object_id for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[crypt.object_id].zone)

        hand = set(game.state.players["A"].zones["hand"])
        self._activate(game, "A", bauble)
        self.assertEqual("graveyard", game.state.cards[bauble.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.assertEqual(hand, set(game.state.players["A"].zones["hand"]))
        self.assertIsNone(self.resolve(game))
        drawn, = set(game.state.players["A"].zones["hand"]) - hand
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(game.state.cards[drawn].ref, packet)
        self.replay(game, load=True)

    def test_actual_boromir_filters_own_casts_and_keeps_the_protection_and_ring_choice(self):
        for controller in ("A", "B"):
            with self.subTest(controller=controller):
                game = self.session(76156763 if controller == "A" else 76156764)
                boromir = self.add(game.engine, BOROMIR, seat=controller)
                own_creature = self.add(game.engine, "Grizzly Bears", ref="boromir-ring-candidate")
                other = self.add(game.engine, "Grizzly Bears", seat="C", ref="boromir-other-body")
                free = self.add(game.engine, "Ornithopter", zone="hand")
                self.assert_closed(game, boromir)
                self.prepare(game)
                self.checkpoint(game)
                self.cast(game, "A", free)
                self.assertEqual(0, self._spell(game, free).context["mana_spent_total"])
                self.assertEqual(int(controller == "B"), len(self._trigger_for(game, boromir)))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard" if controller == "B" else "battlefield",
                                 game.state.cards[free.object_id].zone)

                if controller == "A":
                    self._activate(game, "A", boromir)
                    self.assertEqual("graveyard", game.state.cards[boromir.object_id].zone)
                    pending = self.resolve(game)
                    self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                    self.assertIn("indestructible", game.engine._combat_keywords(game.state.cards[own_creature.object_id]))
                    self.assertNotIn("indestructible", game.engine._combat_keywords(game.state.cards[other.object_id]))
                    self._accept(game, "A", {"action_id": "choose", "objects": [own_creature.ref]})
                    state = current_ring_state(game.engine, "A")
                    self.assertEqual(1, state.temptation_count)
                    self.assertEqual(own_creature.object_id, state.bearer.identity.object_id)
                self.replay(game, load=True)

    def test_actual_previous_counter_of_the_original_spell_leaves_the_trigger_no_other_spell(self):
        game = self.session(76156765)
        bauble = self.add(game.engine, BAUBLE)
        free = self.add(game.engine, "Ornithopter", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        for index in range(2):
            self.add(game.engine, "Island", seat="B", ref=f"bauble-ordinary-counter-island-{index}")
        self.assert_closed(game, bauble)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "A", free)
        spell = self._spell(game, free)
        self.cast(game, "B", counter, [spell.ref])
        self.assertEqual(2, self._spell(game, counter).context["mana_spent_total"])
        self.assertEqual(1, len(self._trigger_for(game, bauble)))
        self.resolve_top(game)
        self.assertFalse(any(item.ref == spell.ref for item in game.state.stack))
        self.assertEqual(1, len(self._trigger_for(game, bauble)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[free.object_id].zone)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_omitting_the_exact_zero_mana_binding_restores_only_the_two_original_residuals(self):
        with patch("quorune.compiler.public_trigger_conditions._zero_mana_cast_counter_binding", return_value=None):
            for name in ORIGINALS:
                bound, _ = register_card(self.db, self.registry, name)
                self.assertFalse(bound["strict_capability_ready"], bound)
            other, _ = register_card(self.db, self.registry, "Counterspell")
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_normalized_cast_and_original_spell_counter(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (SPELL_CAST, CAST_EVENT_COUNTER_CAPABILITY):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Zero-mana cast counter dependency withdrawn"])
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
