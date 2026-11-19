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
from quorune.compiler.event_player_choice_templates import spell_caster_land_return_effect
from quorune.compiler.fixed_entry_return_requirements import FIXED_ENTRY_RETURN_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
MANA_BREACH = "Mana Breach"
SPELL_CAST = "trigger.event.normalized_spell_cast"
FIXTURES = ROOT / "tests" / "fixtures"


class SpellCasterLandReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest", "Island", "Plains", "Unsummon"},
            "ability-source-counter-cards.json": {"Boomerang", "Grizzly Bears", "Twincast"},
            "additional-conditional-modifier-cards.json": {"Disenchant"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "spell-caster-land-return-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "spell-caster-land-return.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Spell caster land return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(MANA_BREACH, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, MANA_BREACH)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_original_binds_a_single_untargeted_caster_land_return(self):
        card, program = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(("spell.cast", "battlefield"), (program.event, program.active_zone))
        self.assertIsNone(program.event_condition)
        self.assertIsNone(program.target_schema)
        self.assertEqual([spell_caster_land_return_effect()], thaw_value(program.effects))
        self.assertTrue({SPELL_CAST, FIXED_ENTRY_RETURN_CAPABILITY, "trigger.placement.apnap",
                         "zone.change.destination_replacement"} <= set(program.capability_dependencies))

    def test_unqualified_cast_and_exact_return_operands_are_required(self):
        card, program = self.original_program()
        effect, = thaw_value(program.effects)
        mutations = (
            {"event": "card.drawn", "coverage": ()},
            {"active_zone": "graveyard"},
            {"event_condition": {"field": "types", "op": "contains_any", "value": ["creature"]}},
            {"effects": [{**effect, "players": ["$controller"]}]},
            {"effects": [{**effect, "players": ["$context.player", "$controller"]}], "coverage": ()},
            {"effects": [{**effect, "count": True}]},
            {"effects": [{**effect, "count": 2}]},
            {"effects": [{**effect, "predicate": {**effect["predicate"], "include_phased_out": 0}}]},
            {"effects": [{**effect, "then": "sacrifice"}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1}},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        for old, new in (
            ("a player casts a spell", "an opponent casts a spell"),
            ("a player casts a spell", "a player casts a creature spell"),
            ("a land they control", "two lands they control"),
            ("a land they control", "a permanent they control"),
        ):
            with self.subTest(text=new):
                ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def _triggers(self, game, source):
        return [item for item in game.state.stack if item.source_object_id == source.object_id
                and item.kind == "triggered_ability"]

    def _choice(self, game, seat, lands):
        self.assertEqual(("choice.apnap", [seat]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
        ctx = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(1, ctx["count"])
        self.assertEqual({card.ref for card in lands}, set(ctx["options"]))
        for other in set("ABCD") - {seat}:
            self.assertIsNone(game.packet("pilot:" + other, full=True).get("decision"))

    def _choose(self, game, seat, land):
        return game.act("pilot:" + seat, {"action_id": "choose", "cards": [land.ref]})

    def _accept(self, game, seat, land):
        result = self._choose(game, seat, land)
        self.assertTrue(result.ok, result.summary)

    def test_actual_response_triggers_choose_for_their_casters_and_survive_source_departure(self):
        game = self.session(76156670)
        source = self.add(game.engine, MANA_BREACH)
        creature = self.add(game.engine, "Grizzly Bears")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="C", zone="hand")
        b_island = self.add(game.engine, "Island", seat="B", ref="breach-b-island")
        borrowed = self.add(game.engine, "Forest", seat="B", ref="breach-b-borrowed")
        borrowed.owner = "D"
        c_lands = [self.add(game.engine, "Plains", seat="C", ref=f"breach-c-plains-{n}") for n in range(2)]
        outside = self.add(game.engine, "Forest", seat="D", ref="breach-d-forest")
        private = self.add(game.engine, "Twincast", seat="D", zone="hand", ref="breach-private-copy")
        for card in (source, bounce, removal):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

        self.cast(game, "B", bounce, [creature.ref])
        self.cast(game, "C", removal, [source.ref])
        self.assertEqual([("A", "B", []), ("A", "C", [])], [
            (item.controller, item.context["player"], item.targets) for item in self._triggers(game, source)
        ])
        self.assertIsNotNone(self.resolve(game))
        self._choice(game, "C", c_lands)
        self._accept(game, "C", c_lands[0])
        self.assertEqual("hand", game.state.cards[c_lands[0].object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertIsNotNone(self.resolve(game))
        self._choice(game, "B", [b_island, borrowed])
        game = self.replay(game, load=True)

        before = authoritative_state_hash(game.state)
        for seat, option in (("A", borrowed), ("B", outside), ("B", creature)):
            with self.subTest(seat=seat, option=option.ref):
                self.assertFalse(self._choose(game, seat, option).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        self._accept(game, "B", borrowed)
        self.assertIn(borrowed.object_id, game.state.players["D"].zones["hand"])
        self.assertEqual("battlefield", game.state.cards[b_island.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[outside.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[creature.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_twincast_creates_a_copy_without_an_additional_cast_trigger(self):
        game = self.session(76156671)
        source = self.add(game.engine, MANA_BREACH)
        original_spell = self.add(game.engine, "Boomerang", zone="hand")
        twincast = self.add(game.engine, "Twincast", seat="B", zone="hand")
        original_target = self.add(game.engine, "Grizzly Bears", seat="B", ref="breach-copy-original-target")
        copy_target = self.add(game.engine, "Grizzly Bears", seat="C", ref="breach-copy-new-target")
        lands = {seat: [self.add(game.engine, "Island", seat=seat, ref=f"breach-copy-{seat}-{n}") for n in range(2)]
                 for seat in "AB"}
        for card in (source, original_spell, twincast):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "A", original_spell, [original_target.ref])
        original = next(item for item in game.state.stack if item.card_object_id == original_spell.object_id)
        self.cast(game, "B", twincast, [original.ref])
        self.assertEqual(["A", "B"], [item.context["player"] for item in self._triggers(game, source)])
        self.assertIsNotNone(self.resolve(game))
        self._choice(game, "B", lands["B"])
        self._accept(game, "B", lands["B"][0])

        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        result = game.act("pilot:B", {"action_id": "choose", "targets": [copy_target.ref]})
        self.assertTrue(result.ok, result.summary)
        copy, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(("B", [copy_target.ref]), (copy.controller, copy.targets))
        self.assertEqual(["A"], [item.context["player"] for item in self._triggers(game, source)])
        self.assertIsNotNone(self.resolve(game))
        self._choice(game, "A", lands["A"])
        self.assertEqual("hand", game.state.cards[copy_target.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[original_target.object_id].zone)
        self._accept(game, "A", lands["A"][0])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[original_target.object_id].zone)
        self.assertEqual(2, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_landless_and_departed_casters_require_no_choice(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76156672 + departed)
                source = self.add(game.engine, MANA_BREACH)
                creature = self.add(game.engine, "Grizzly Bears")
                caster = "B" if departed else "A"
                spell = self.add(game.engine, "Unsummon", seat=caster, zone="hand")
                unrelated = self.add(game.engine, "Forest", seat="D")
                if departed:
                    self.add(game.engine, "Island", seat="B")
                    self.prepare(game)
                else:
                    self.prepare(game, mana={"U": 1})
                self.checkpoint(game)
                self.cast(game, caster, spell, [creature.ref])
                self.assertEqual([caster], [item.context["player"] for item in self._triggers(game, source)])
                if departed:
                    self.priority(game, caster)
                    result = game.act("pilot:" + caster, {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertNotIn(caster, game.engine.active_seats)
                    self.assertEqual([caster], [item.context["player"] for item in self._triggers(game, source)])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[unrelated.object_id].zone)
                self.assertFalse(game.state.stack)
                self.replay(game, load=True)

    def test_omitting_the_caster_land_lowering_restores_the_original_residual(self):
        with patch("quorune.compiler.event_player_choice_templates._spell_caster_land_return_result", return_value=None):
            bound, _ = register_card(self.db, self.registry, MANA_BREACH)
            other, _ = register_card(self.db, self.registry, "Boomerang")
        self.assertFalse(bound["strict_capability_ready"], bound)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_the_cast_event_and_land_return_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (SPELL_CAST, FIXED_ENTRY_RETURN_CAPABILITY):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Caster return dependency withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                bound, _ = register_card(self.db, registry, MANA_BREACH)
                other, _ = register_card(self.db, registry, "Boomerang")
                self.assertFalse(bound["strict_capability_ready"], bound)
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
