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
from quorune.compiler.affected_player_sacrifice_templates import FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_effect_payment import FIXED_EFFECT_PAYMENT_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.spell_caster_sacrifice_payment import spell_caster_sacrifice_payment_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
AETHER = "Aether Barrier"
SPELLTITHE = "Spelltithe Enforcer"
ORIGINALS = (AETHER, SPELLTITHE)
SPELL_CAST = "trigger.event.normalized_spell_cast"
FIXTURES = ROOT / "tests" / "fixtures"


class SpellCasterSacrificePaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest", "Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Grizzly Bears"},
            "attached-cast-restriction-cards.json": {"Seal of Cleansing"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "spell-caster-land-return-cards.json": {"Mana Breach"},
        }
        parts = [FIXTURES / "spell-caster-sacrifice-payment-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "spell-caster-sacrifice-payment.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Caster mana or sacrifice", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_originals_use_two_cast_filters_and_one_caster_payment_contract(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, program = self.original_program(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(("spell.cast", "battlefield"), (program.event, program.active_zone))
                self.assertIsNone(program.target_schema)
                condition = ({"field": "types", "op": "contains_any", "value": ["creature"]}
                             if name == AETHER else
                             {"field": "controller", "op": "ne", "value": "$source.controller"})
                self.assertEqual(condition, thaw_value(program.event_condition))
                self.assertEqual([spell_caster_sacrifice_payment_effect()], thaw_value(program.effects))
                self.assertTrue({SPELL_CAST, FIXED_EFFECT_PAYMENT_CAPABILITY,
                    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, "trigger.placement.apnap",
                    "zone.change.destination_replacement"} <= set(program.capability_dependencies))

    def test_malformed_payment_recipient_consequence_and_event_fail_closed(self):
        card, program = self.original_program(AETHER)
        effect, = thaw_value(program.effects)
        decline, = effect["declined_effects"]
        mutations = (
            {"active_zone": "graveyard"},
            {"event": "card.drawn", "coverage": ()},
            {"event_condition": None, "coverage": ()},
            {"effects": [{**effect, "schema_version": 14.0}]},
            {"effects": [{**effect, "schema_version": 15}]},
            {"effects": [{**effect, "player": "$controller"}]},
            {"effects": [{**effect, "cost": {**effect["cost"], "GENERIC": True}}]},
            {"effects": [{**effect, "effects": [decline]}]},
            {"effects": [{**effect, "declined_effects": [{**decline, "actor": "$context.player"}]}]},
            {"effects": [{**effect, "declined_effects": [{**decline, "players": ["$controller"]}]}]},
            {"effects": [{**effect, "declined_effects": [{**decline, "count": True}]}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1}},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
        for old, new in (("{1}", "{2}"), ("a permanent", "a creature"),
                         ("a player casts a creature spell", "you cast a creature spell")):
            with self.subTest(text=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def _trigger(self, game, source, caster, controller):
        trigger, = (item for item in game.state.stack if item.kind == "triggered_ability")
        self.assertEqual((source.object_id, caster, controller, []),
                         (trigger.source_object_id, trigger.context["player"], trigger.controller, trigger.targets))

    def _decision(self, game, kind, actor):
        pending = game.state.pending_decision
        self.assertIsNotNone(pending)
        self.assertEqual((kind, [actor]), (pending.kind, pending.actors))
        for viewer in set("ABCD") - {actor}:
            self.assertIsNone(game.packet("pilot:" + viewer, full=True).get("decision"))
        return game.packet("pilot:" + actor, full=True)["decision"]["ctx"]

    def _accept(self, game, actor, command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _reject_unchanged(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_aether_self_cast_pays_and_preserves_the_permanent_and_noncreature_cast(self):
        game = self.session(76156721)
        source = self.add(game.engine, AETHER)
        cast = self.add(game.engine, "Grizzly Bears", zone="hand", ref="aether-own-creature-spell")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="aether-noncreature-target")
        kept = self.add(game.engine, "Grizzly Bears", ref="aether-kept")
        later = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        for card in (source, cast, later):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1, "C": 2})
        self.checkpoint(game)

        self.cast(game, "A", cast)
        self._trigger(game, source, "A", "A")
        self.assertIsNotNone(self.resolve(game))
        self._decision(game, "semantic.choice", "A")
        game = self.replay(game, load=True)
        pay = {"action_id": "choose", "pay": True}
        self._reject_unchanged(game, "B", pay)
        self._reject_unchanged(game, "A", {**pay, "pay": 1})
        self._accept(game, "A", pay)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[cast.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[kept.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))

        self.cast(game, "B", later, [other.ref])
        self.assertFalse(any(item.kind == "triggered_ability" for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_actual_spelltithe_uses_opponent_payer_and_decline_sacrifices_to_the_owner(self):
        game = self.session(76156722)
        source = self.add(game.engine, SPELLTITHE)
        target = self.add(game.engine, "Grizzly Bears")
        cast = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        island = self.add(game.engine, "Island", seat="B", ref="spelltithe-blue-source")
        borrowed = self.add(game.engine, "Forest", seat="B", ref="spelltithe-borrowed-green")
        borrowed.owner = "D"
        private = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="spelltithe-private-card")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)

        self.cast(game, "B", cast, [target.ref])
        self._trigger(game, source, "B", "A")
        self.assertIsNotNone(self.resolve(game))
        self._decision(game, "semantic.choice", "B")
        decline = {"action_id": "choose", "pay": False}
        self._reject_unchanged(game, "A", decline)
        self._accept(game, "B", decline)
        choice = self._decision(game, "choice.apnap", "B")
        self.assertEqual({island.ref, borrowed.ref}, set(choice["options"]))
        self.assertFalse(game.state.cards[borrowed.object_id].tapped)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        sacrifice = {"action_id": "choose", "cards": [borrowed.ref]}
        self._reject_unchanged(game, "D", sacrifice)
        self._reject_unchanged(game, "B", {**sacrifice, "cards": [source.ref]})
        self._accept(game, "B", sacrifice)
        self.assertIn(borrowed.object_id, game.state.players["D"].zones["graveyard"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        for viewer in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_spelltithe_payment_uses_only_the_opponent_casters_resources(self):
        game = self.session(76156726)
        source = self.add(game.engine, SPELLTITHE)
        target = self.add(game.engine, "Grizzly Bears")
        spell = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        island = self.add(game.engine, "Island", seat="B", ref="spelltithe-paid-blue-source")
        forest = self.add(game.engine, "Forest", seat="B", ref="spelltithe-paid-generic-source")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.cast(game, "B", spell, [target.ref])
        self._trigger(game, source, "B", "A")
        self.assertIsNotNone(self.resolve(game))
        self._decision(game, "semantic.choice", "B")
        self.assertTrue(game.state.cards[island.object_id].tapped)
        self.assertFalse(game.state.cards[forest.object_id].tapped)
        self._accept(game, "B", {"action_id": "choose", "pay": True})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.state.players["A"].mana_pool.get("C", 0))
        self.assertEqual("battlefield", game.state.cards[forest.object_id].zone)
        self.assertTrue(game.state.cards[forest.object_id].tapped)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_unpayable_caster_selects_sacrifice_after_source_departure(self):
        game = self.session(76156723)
        source = self.add(game.engine, AETHER, seat="B")
        seal = self.add(game.engine, "Seal of Cleansing", seat="C")
        cast = self.add(game.engine, "Grizzly Bears", zone="hand", ref="aether-cast-without-spare")
        existing = self.add(game.engine, "Grizzly Bears", ref="aether-only-sacrifice-choice")
        for card in (source, seal, cast):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1, "C": 1})
        self.checkpoint(game)
        self.cast(game, "A", cast)
        self._trigger(game, source, "A", "B")
        self.priority(game, "C")
        action, = (row for row in game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + seal.ref + ":"))
        self._accept(game, "C", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self._trigger(game, source, "A", "B")
        self.assertIsNotNone(self.resolve(game))
        choice = self._decision(game, "choice.apnap", "A")
        self.assertEqual([existing.ref], choice["options"])
        self._accept(game, "A", {"action_id": "choose", "cards": [existing.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[existing.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[cast.object_id].zone)
        self.replay(game, load=True)

    def test_actual_empty_permanent_set_and_departed_caster_complete_without_choice(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76156724 + departed)
                if departed:
                    source = self.add(game.engine, SPELLTITHE)
                    target = self.add(game.engine, "Grizzly Bears")
                    cast = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    self.add(game.engine, "Island", seat="B")
                    self.prepare(game)
                else:
                    source = self.add(game.engine, AETHER, seat="B")
                    cast = self.add(game.engine, "Grizzly Bears", zone="hand")
                    self.prepare(game, mana={"G": 1, "C": 1})
                self.checkpoint(game)
                if departed:
                    self.cast(game, "B", cast, [target.ref])
                    self.priority(game, "B")
                    self._accept(game, "B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertNotIn("B", game.engine.active_seats)
                    self._trigger(game, source, "B", "A")
                else:
                    self.cast(game, "A", cast)
                    self._trigger(game, source, "A", "B")
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.stack)
                self.assertFalse(game.state.game_over)
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_omitting_the_exact_caster_sacrifice_payment_restores_both_residuals(self):
        with patch("quorune.compiler.event_player_choice_templates._spell_caster_sacrifice_payment_result",
                   return_value=None):
            for name in ORIGINALS:
                bound, _ = register_card(self.db, self.registry, name)
                self.assertFalse(bound["strict_capability_ready"], bound)
            other, _ = register_card(self.db, self.registry, "Mana Breach")
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_payment_and_sacrifice_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_EFFECT_PAYMENT_CAPABILITY, FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Caster sacrifice payment dependency withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                for name in ORIGINALS:
                    bound, _ = register_card(self.db, registry, name)
                    self.assertFalse(bound["strict_capability_ready"], bound)
                other, _ = register_card(self.db, registry, "Mana Breach")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
