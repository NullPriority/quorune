"""Native activation, damage and replay for the original free one-point redirections."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.damage import commit_prepared_damage_batch, damage_proposal, prepare_damage_batch
from quorune.damage_modifier_state import (
    DamageModifierDuration, DamageModifierError, DamageRedirectionEffect, DamageSubject,
)
from quorune.effect_runtime import damage_modifiers
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.ordering import ReplacementChoiceRequired
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.finite_damage_redirection import FINITE_DAMAGE_REDIRECTION_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


ORIGINALS = ("Warrior en-Kor", "Outrider en-Kor", "Nomads en-Kor", "Lancers en-Kor", "Spirit en-Kor")


class FiniteDamageRedirectionStateTests(unittest.TestCase):
    def test_one_point_wire_scope_preserves_the_original_full_redirect_record(self):
        source = DamageSubject("A1", "permanent", "A", "card-1", "card-1@3", "A")
        destination = DamageSubject("A2", "permanent", "A", "card-2", "card-2@1", "A")
        legacy = DamageRedirectionEffect("DR1", "S1", "A", source, destination,
                                         DamageModifierDuration.UNTIL_END_OF_TURN, 4)
        legacy_wire = legacy.to_dict()
        self.assertNotIn("maximum_damage", legacy_wire)
        self.assertEqual(legacy_wire, DamageRedirectionEffect.from_dict(legacy_wire).to_dict())

        finite = replace(legacy, maximum_damage=1)
        self.assertEqual({**legacy_wire, "maximum_damage": 1}, finite.to_dict())
        self.assertEqual(finite, DamageRedirectionEffect.from_dict(finite.to_dict()))
        self.assertEqual(source, replace(finite, destination=source).destination)
        for changed in (
            {"maximum_damage": True}, {"maximum_damage": None}, {"maximum_damage": 2},
            {"consume_on_application": False}, {"duration": "until_used"},
            {"destination": DamageSubject("B", "player", "B").to_dict()},
        ):
            with self.subTest(changed=changed), self.assertRaises(DamageModifierError):
                DamageRedirectionEffect.from_dict({**finite.to_dict(), **changed})


class FiniteDamageRedirectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "finite-damage-redirection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/finite-damage-redirection-cards.json"], path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("One point", [DeckEntry(commander, 1, "commander"),
                                              DeckEntry("Plains", 30)], [commander])

    def activation(self, game, source, target):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        return {"action_id": action["id"], "targets": [target.ref], "pay": "auto"}

    def activate(self, game, source, target):
        result = game.act("pilot:A", self.activation(game, source, target))
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_each_printed_activation_is_trusted_and_requires_your_creature(self):
        game = self.session(6145001)
        sources = [self.add(game.engine, name) for name in ORIGINALS]
        own = self.add(game.engine, "Grizzly Bears")
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="opposing-bears")
        land = self.add(game.engine, "Plains")
        self.prepare(game)
        self.checkpoint(game)
        for source in sources:
            with self.subTest(source=source.printed_name):
                self.assert_closed(game, source)
                self.reject(game, "A", self.activation(game, source, opponent))
                self.reject(game, "A", self.activation(game, source, land))
                self.activate(game, source, own)
        self.assertEqual(5, len(game.state.damage_redirections))
        self.assertEqual({source.logical_object_id for source in sources},
                         {effect.subject.logical_object_id for effect in game.state.damage_redirections})
        self.assertEqual({own.logical_object_id},
                         {effect.destination.logical_object_id for effect in game.state.damage_redirections})
        self.replay(game, load=True)

    def test_a_redirection_is_consumed_once_and_protection_sees_its_new_recipient(self):
        game = self.session(6145002)
        source = self.add(game.engine, "Lancers en-Kor")
        bearer = self.add(game.engine, "Silver Knight")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand", ref="first-shock")
        second = self.add(game.engine, "Shock", seat="B", zone="hand", ref="second-shock")
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        self.activate(game, source, bearer)
        self.cast_for(game, "B", shock, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 0), tuple(game.state.cards[card.object_id].marked_damage for card in (source, bearer)))
        self.assertEqual([], game.state.damage_redirections)
        self.cast_for(game, "B", second, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.cards[bearer.object_id].marked_damage)
        self.replay(game, load=True)

    def test_competing_activations_resume_the_same_damage_after_a_saved_private_choice(self):
        game = self.session(6145003)
        source = self.add(game.engine, "Warrior en-Kor")
        first = self.add(game.engine, "Grizzly Bears")
        second = self.add(game.engine, "Silver Knight")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="unseen-redirection-sentinel")
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.activate(game, source, first)
        self.activate(game, source, second)
        self.cast_for(game, "B", bolt, [source.ref])
        pending = self.resolve(game)
        self.assertEqual("replacement.order", pending.kind)
        self.assertEqual(["pilot:A"], list(game.pending_principals()))
        self.assertEqual(0, game.state.cards[source.object_id].marked_damage)
        game = self.replay(game, load=True)
        packet = game.packet("pilot:A", full=True)
        self.assertNotIn(private.ref, str(packet))
        options = packet["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "C", command)
        choice = game.act("pilot:A", command)
        self.assertTrue(choice.ok, choice.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 1, 0), tuple(game.state.cards[card.object_id].marked_damage
                                          for card in (source, first, second)))
        self.assertEqual([], game.state.damage_redirections)
        self.replay(game, load=True)

    def test_a_blinked_source_or_destination_does_not_gain_the_old_redirection(self):
        for changed in ("source", "destination"):
            with self.subTest(changed=changed):
                game = self.session(6145004)
                source = self.add(game.engine, "Lancers en-Kor")
                destination = self.add(game.engine, "Grizzly Bears")
                blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                self.add(game.engine, "Mountain", seat="B")
                self.add(game.engine, "Mountain", seat="B", ref="second-red-source")
                self.prepare(game)
                game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(game)
                self.activate(game, source, destination)
                changed_card = source if changed == "source" else destination
                old_incarnation = changed_card.logical_object_id
                self.cast_for(game, "B", blink, [changed_card.ref])
                self.assertIsNone(self.resolve(game))
                self.assertNotEqual(old_incarnation, game.state.cards[changed_card.object_id].logical_object_id)
                current_source = game.state.cards[source.object_id]
                self.cast_for(game, "B", shock, [current_source.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual((2, 0), tuple(game.state.cards[card.object_id].marked_damage
                                               for card in (source, destination)))
                self.replay(game, load=True)

    def test_simultaneous_combat_sources_share_only_one_redirection(self):
        game = self.session(6145005)
        source = self.add(game.engine, "Lancers en-Kor")
        destination = self.add(game.engine, "Grizzly Bears")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="combat-first")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="combat-second")
        self.prepare(game)
        self.activate(game, source, destination)
        proposals = tuple(damage_proposal(
            game.engine, proposal_id=f"damage:{attacker.ref}", actor="B", source_ref=attacker.ref,
            target=source.ref, amount=2, combat=True, reason="Simultaneous combat damage",
        ) for attacker in (first, second))
        with self.assertRaises(ReplacementChoiceRequired) as paused:
            prepare_damage_batch(game.engine, proposals)
        pending = paused.exception.pending
        self.assertEqual("A", pending.tree_choice.choice.chooser)
        self.assertEqual({row.proposal_id for row in proposals}, set(pending.event_order_options))
        selected = proposals[1].proposal_id
        self.assertNotEqual(selected, pending.event_id)
        effect_id = game.state.damage_redirections[0].effect_id
        prepared = prepare_damage_batch(game.engine, proposals, selections=(
            {"event_id": selected, "effect_id": effect_id},
        ))
        results = commit_prepared_damage_batch(game.engine, prepared).events
        self.assertTrue(all(event.combat for event in results))
        amounts = {(event.source, event.target): event.dealt_amount for event in results}
        self.assertEqual({(first.ref, source.ref): 2, (second.ref, source.ref): 1,
                          (second.ref, destination.ref): 1}, amounts)
        self.assertEqual([], game.state.damage_redirections)

    def test_redirected_damage_can_rejoin_one_source_recipient_occurrence(self):
        game = self.session(6145006)
        source = self.add(game.engine, "Lancers en-Kor")
        destination = self.add(game.engine, "Nomads en-Kor")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.activate(game, source, destination)
        self.activate(game, destination, source)
        self.cast_for(game, "B", shock, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 0), tuple(game.state.cards[card.object_id].marked_damage
                                       for card in (source, destination)))
        self.assertEqual([], game.state.damage_redirections)
        self.replay(game, load=True)

    def test_the_native_point_limit_witness_catches_the_full_redirect_mutation(self):
        self.test_a_redirection_is_consumed_once_and_protection_sees_its_new_recipient()
        original = damage_modifiers.DamageRedirectionEffect

        def full_redirect(**kwargs):
            return original(**{**kwargs, "maximum_damage": None})

        with patch.object(damage_modifiers, "DamageRedirectionEffect", side_effect=full_redirect):
            with self.assertRaises(AssertionError):
                self.test_a_redirection_is_consumed_once_and_protection_sees_its_new_recipient()

    def test_withholding_only_the_one_point_capability_preserves_other_exact_sources(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == FINITE_DAMAGE_REDIRECTION_CAPABILITY)
        row.update(status="blocked", blockers=["One-point redirection evidence withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        source = self.db.lookup("Warrior en-Kor")
        self.assertEqual("exact", compile_oracle_card(source, capability_registry=self.registry).status)
        blocked = compile_oracle_card(source, capability_registry=registry)
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(any(FINITE_DAMAGE_REDIRECTION_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))
        self.assertEqual("exact", compile_oracle_card(self.db.lookup("Shock"), capability_registry=registry).status)

    def test_the_complete_source_keeps_unrepresented_variants_out(self):
        original = self.db.lookup("Warrior en-Kor")
        self.assertEqual("exact", compile_oracle_card(original, capability_registry=self.registry).status)
        for text in (original.oracle_text.replace("next 1", "next 2"),
                     original.oracle_text.replace("you control", "an opponent controls"),
                     original.oracle_text.replace("{0}", "{T}"),
                     original.oracle_text + " Draw a card."):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(any(FINITE_DAMAGE_REDIRECTION_CAPABILITY in node.capability_dependencies
                                     for face in ir.faces for node in face.nodes))
