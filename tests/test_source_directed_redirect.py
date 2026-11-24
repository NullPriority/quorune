"""Native source redirection, original target slots, and paid activation evidence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousObjectIdentity, ContinuousEffectDuration, ContinuousEffectOrigin, Layer
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import ContinuousEffect, ContinuousOperation
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.source_directed_redirect_model import SOURCE_DIRECTED_REDIRECT_CAPABILITY, SourceDirectedRedirectSpec
from quorune.trigger_participation import WardSpec
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class SourceDirectedRedirectTests(InheritedNativeFixture, unittest.TestCase):
    meddler = "Trial Slot Meddler"
    skite = "Trial Redirect Golem"
    pinger = "Trial Needle Mage"
    copy_spell = "Trial Binding Reflection"
    growth = "Trial Growth Spark"
    first = "Trial River Guardian"
    second = "Trial Oak Guard"
    secret = "Trial Sealed Watcher"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "source-redirect.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "persistent-public-copy-cards.json",
            fixtures / "source-directed-redirect-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Source redirect", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [name])

    def _begin(self, game, *programs, mana, response_mana):
        game.state.config.semantic_policy = "trusted_only"
        for card in programs:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update(response_mana)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _entry_redirect(self, game, source, target):
        self.cast(game, "B", source)
        self.resolve_top(game)
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["B"]), (pending.kind, pending.actors))
        accepted = game.act("pilot:B", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)

    def _activation_offer(self, game, source, actor):
        self.priority(game, actor)
        return next(action for action in game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
                    if action["id"].startswith(f"activate:{source.ref}:"))

    def _pay_skite(self, game, source, target, *, life):
        action = self._activation_offer(game, source, "B")
        option = next(row for row in action["cost_options"] if bool(row.get("life_payment")) == life)
        command = {"action_id": action["id"], "cost_option": option["id"], "targets": [target.ref], "pay": "auto"}
        response = game.act("pilot:B", command)
        self.assertTrue(response.ok, response.summary)

    def _choices(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        payload = pending.payload_by_actor["B"]
        self.assertEqual("retarget_stack_item", payload["operation"])
        offered, = (row for row in payload["legal_actions"] if row["id"] == "choose")
        return tuple(offered["choice_schema"]["legal_values"])

    def _choose(self, game, value):
        response = game.act("pilot:B", {"action_id": "choose", "choice": value})
        self.assertTrue(response.ok, response.summary)

    def test_meddler_can_replace_one_grouped_target_or_decline_after_reload(self):
        game = self.session(115_701_01)
        engine = game.engine
        spell = self.add(engine, self.copy_spell, zone="hand", ref="REDIRECTED-SPELL")
        source = self.add(engine, self.meddler, seat="B", zone="hand", ref="MEDDLER-SOURCE")
        first = self.add(engine, self.first, ref="PRESERVED-RECIPIENT")
        second = self.add(engine, self.second, seat="C", ref="ORIGINAL-MODEL")
        secret = self.add(engine, self.secret, seat="D", zone="hand", ref="PRIVATE-REDIRECT-DECOY")
        self._begin(game, spell, source, mana={"C":4,"U":2}, response_mana={"C":2,"U":1})
        self.cast(game, "A", spell, [first.ref, second.ref])
        original = game.state.stack[-1]
        retained = dict(original.context["target_snapshots"][first.ref])
        self._entry_redirect(game, source, original)
        self.assertEqual(("slot:0", "slot:1", "decline"), self._choices(game))
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:B", full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id":"choose", "choice":"slot:1"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        preserved = self.replay(game, load=True)
        game = self.replay(game, load=True)
        self._choose(game, "slot:1")
        changed = next(item for item in game.state.stack if item.ref == original.ref)
        self.assertEqual([first.ref, source.ref], changed.targets)
        self.assertEqual(retained, changed.context["target_snapshots"][first.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.meddler, game.engine.display_name(first.object_id))
        self.replay(game, load=True)

        self._choose(preserved, "decline")
        unchanged = next(item for item in preserved.state.stack if item.ref == original.ref)
        self.assertEqual([first.ref, second.ref], unchanged.targets)
        self.assertIsNone(self.resolve(preserved))
        self.assertEqual(self.second, preserved.engine.display_name(first.object_id))
        self.replay(preserved, load=True)

    def test_spellskite_pays_either_phyrexian_arm_and_requires_the_legal_change(self):
        for use_life in (False, True):
            with self.subTest(life=use_life):
                game = self.session(115_701_02)
                engine = game.engine
                spell = self.add(engine, self.growth, zone="hand", ref="GROWTH-SPELL")
                source = self.add(engine, self.skite, seat="B", ref="REDIRECT-GOLEM")
                first = self.add(engine, self.first, ref="ORIGINAL-GROWTH-TARGET")
                base_power = engine._numeric_stat(source.object_id, "power")
                life_before = game.state.players["B"].life
                self._begin(game, spell, source, mana={"G":1}, response_mana={"U":1})
                self.cast(game, "A", spell, [first.ref])
                original = game.state.stack[-1]
                self._pay_skite(game, source, original, life=use_life)
                self.assertEqual(life_before - (2 if use_life else 0), game.state.players["B"].life)
                self.assertEqual(1 if use_life else 0, game.state.players["B"].mana_pool["U"])
                self.assertEqual(("slot:0",), self._choices(game))
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", {"action_id":"choose", "choice":"decline"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                self._choose(game, "slot:0")
                self.assertIsNone(self.resolve(game))
                self.assertEqual(base_power + 3, game.engine._numeric_stat(source.object_id, "power"))
                self.replay(game, load=True)

    def test_redirect_ignores_a_reentered_source_and_preserves_the_initial_target(self):
        game = self.session(115_701_03)
        engine = game.engine
        spell = self.add(engine, self.growth, zone="hand", ref="STALE-REDIRECT-SPELL")
        source = self.add(engine, self.skite, seat="B", ref="REENTERED-SOURCE")
        first = self.add(engine, self.first, ref="UNCHANGED-TARGET")
        base_power = engine._numeric_stat(first.object_id, "power")
        self._begin(game, spell, source, mana={"G":1}, response_mana={"U":1})
        self.cast(game, "A", spell, [first.ref])
        original = game.state.stack[-1]
        self._pay_skite(game, source, original, life=False)
        old = source.logical_object_id
        owner = ZoneTransitionOwner(engine)
        owner.move_card(source.object_id, "exile")
        owner.move_card(source.object_id, "battlefield")
        self.assertNotEqual(old, source.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(base_power + 3, game.engine._numeric_stat(first.object_id, "power"))
        self.replay(game, load=True)

    def test_source_redirect_of_a_targeted_activation_announces_ward_to_its_original_controller(self):
        game = self.session(115_701_04)
        engine = game.engine
        pinger = self.add(engine, self.pinger, ref="ORIGINAL-TARGETING-ACTIVATION")
        source = self.add(engine, self.skite, seat="B", ref="WARD-REDIRECT-RECIPIENT")
        first = self.add(engine, self.first, seat="C", ref="ORIGINAL-PING-TARGET")
        pinger.acquired_control_turn_count = -1
        commit_continuous_effect(engine.state, ContinuousEffect(
            effect_id="fixture:redirect-ward", source_id="fixture:ward-grant", layer=Layer.ABILITY,
            sublayer="6", timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(WardSpec(generic_cost=1))),),
            origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            locked_objects=(ContinuousObjectIdentity(source.object_id, source.logical_object_id),),
        ))
        self._begin(game, pinger, source, mana={}, response_mana={"U":1})
        action = self._activation_offer(game, pinger, "A")
        accepted = game.act("pilot:A", {"action_id":action["id"], "targets":[first.ref], "pay":"auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        original = game.state.stack[-1]
        self._pay_skite(game, source, original, life=False)
        self.assertEqual(("slot:0",), self._choices(game))
        self._choose(game, "slot:0")
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("ward_counter_unless_pay", pending.payload_by_actor["A"]["operation"])
        game = self.replay(game, load=True)
        declined = game.act("pilot:A", {"action_id":"choose", "pay":False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[first.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[source.object_id].marked_damage)
        self.replay(game, load=True)

    def test_source_directed_redirect_requires_its_exact_owned_ability_and_verified_authority(self):
        def compile_card(name, registry):
            return compile_oracle_card(self.db.lookup(name), capability_registry=registry,
                                       capability_profile="commander_review")

        for name, optional in ((self.meddler, True), (self.skite, False)):
            ir = compile_card(name, self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = (node for node in ir.faces[0].nodes if node.template_id == "source-directed-redirect-v1")
            spec = SourceDirectedRedirectSpec(optional)
            self.assertEqual(spec.effects, node.effects)
            self.assertEqual(spec.target_schema, node.target_schema)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == SOURCE_DIRECTED_REDIRECT_CAPABILITY)
        row.update(status="blocked", blockers=["Source-directed redirect authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in (self.meddler, self.skite):
            self.assertNotEqual("exact", compile_card(name, registry).status)
        with patch("quorune.compiler.source_directed_redirect.source_directed_redirect_spec", return_value=None):
            self.assertNotEqual("exact", compile_card(self.skite, self.registry).status)
