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

class GrantorFiniteDamageRedirectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "grantor-finite-redirection.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "finite-damage-redirection-cards.json",
                                fixtures / "grantor-damage-redirection-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Grantor finite redirection", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def offers(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{card.ref}:")
                and "#grantor-" in row.get("ability", "")]

    def activate(self, game, seat, card, target):
        offer, = self.offers(game, seat, card)
        response = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual(seat, game.state.stack[-1].controller)

    def grant(self, game, spell, creature):
        self.assert_closed(game, spell)
        self.cast_for(game, "A", spell, [creature.ref])
        self.resolve_top(game)

    def changed_control_scene(self, seed):
        game = self.session(seed)
        spell = self.add(game.engine, "Martyrdom", zone="hand")
        bearer = self.add(game.engine, "Indomitable Ancients")
        steal = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="hidden-grantor-card")
        game.state.players["A"].mana_pool.update({"W": 2, "C": 1})
        self.prepare(game, actor="B", active="B", mana={"C": 2, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", steal, [bearer.ref])
        self.grant(game, spell, bearer)
        original, = self.offers(game, "A", bearer)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[bearer.object_id].controller)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        return game, bearer, shock, original

    def test_only_grantor_is_offered_and_can_commit_after_control_change(self):
        game, bearer, shock, original = self.changed_control_scene(6145311)
        self.assertEqual([], self.offers(game, "B", bearer))
        self.reject(game, "B", {"action_id": original["id"], "targets": ["A"], "pay": "auto"})
        offered, = self.offers(game, "A", bearer)
        self.assertEqual(original["id"], offered["id"])
        self.activate(game, "A", bearer, "A")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        effect, = game.state.damage_redirections
        self.assertEqual(("A", "B"), (effect.controller, effect.destination.controller))
        self.cast_for(game, "B", shock, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        self.assertEqual(1, game.state.cards[bearer.object_id].marked_damage)
        self.replay(game, load=True)

    def test_controller_activation_authority_mutation_is_detected(self):
        from quorune.rules.activation import proposal, query

        game, bearer, _, _ = self.changed_control_scene(6145312)
        real = query.activation_actor_allowed

        def controller_mutation(host, seat, source, ability):
            if ability.activation_grantor is not None:
                return source.controller == seat
            return real(host, seat, source, ability)

        self.priority(game, "A")
        with (patch.object(query, "activation_actor_allowed", side_effect=controller_mutation),
              patch.object(proposal, "activation_actor_allowed", side_effect=controller_mutation)):
            with self.assertRaisesRegex(AssertionError, "activate:"):
                self.assertEqual([], self.offers(game, "B", bearer))

    def test_repeated_points_and_exact_creature_planeswalker_player_domain_replay(self):
        game = self.session(6145321)
        spell = self.add(game.engine, "Martyrdom", zone="hand")
        bearer = self.add(game.engine, "Indomitable Ancients")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        walker = self.add(game.engine, "Gideon Jura", seat="C")
        walker.counters["loyalty"] = 6
        battle = self.add(game.engine, "Invasion of Zendikar", seat="C")
        battle.counters["defense"], battle.battle_protector = 3, "D"
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        shocks = [self.add(game.engine, "Shock", seat="B", zone="hand", ref=f"grantor-shock-{idx}")
                  for idx in range(3)]
        self.prepare(game, mana={"C": 1, "W": 2})
        game.state.players["B"].mana_pool.update({"R": 4})
        self.checkpoint(game)
        self.grant(game, spell, bearer)
        offer, = self.offers(game, "A", bearer)
        refs = set(offer["target_schema"]["legal_refs"])
        self.assertTrue({"A", "C", creature.ref, walker.ref, bearer.ref} <= refs, refs)
        self.assertEqual("battlefield", game.state.cards[battle.object_id].zone)
        self.assertNotIn(battle.ref, refs)
        self.reject(game, "A", {"action_id": offer["id"], "targets": [battle.ref], "pay": "auto"})
        for target in ("A", "A", creature.ref, walker.ref):
            self.activate(game, "A", bearer, target)
            self.assertIsNone(self.resolve(game))
        self.assertEqual(4, len(game.state.damage_redirections))
        self.cast_for(game, "B", bolt, ["A"])
        self.assertEqual("replacement.order", self.resolve(game).kind)
        command = {"action_id": "choose", "replacement": game.state.damage_redirections[0].effect_id}
        self.reject(game, "C", command)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        self.assertEqual(2, game.state.cards[bearer.object_id].marked_damage)
        for shock, target in zip(shocks[:2], (creature.ref, walker.ref), strict=True):
            self.cast_for(game, "B", shock, [target])
            self.assertIsNone(self.resolve(game))
        self.activate(game, "A", bearer, bearer.ref)
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", shocks[2], [bearer.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 5, 6), (game.state.cards[creature.object_id].marked_damage,
                                    game.state.cards[walker.object_id].counters["loyalty"],
                                    game.state.cards[bearer.object_id].marked_damage))
        self.assertEqual([], game.state.damage_redirections)
        self.replay(game, load=True)

    def test_blinked_and_expired_destinations_cannot_redirect(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                game = self.session(6145330 + blink)
                spell = self.add(game.engine, "Martyrdom", zone="hand")
                bearer = self.add(game.engine, "Indomitable Ancients")
                response = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
                shock = self.add(game.engine, "Shock", seat="B", zone="hand")
                self.add(game.engine, "Mountain", seat="B")
                self.prepare(game, mana={"C": 1, "W": 2})
                game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(game)
                self.grant(game, spell, bearer)
                self.activate(game, "A", bearer, "A")
                self.assertIsNone(self.resolve(game))
                if blink:
                    old = game.state.cards[bearer.object_id].logical_object_id
                    self.cast_for(game, "B", response, [bearer.ref])
                    self.assertIsNone(self.resolve(game))
                    self.assertNotEqual(old, game.state.cards[bearer.object_id].logical_object_id)
                else:
                    turn = game.state.turn_sequence
                    self.until(game, lambda current: current.state.turn_sequence > turn)
                    self.assertEqual([], game.state.damage_redirections)
                self.assertFalse(self.offers(game, "A", bearer))
                self.cast_for(game, "B", shock, ["A"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(38, game.state.players["A"].life)
                self.assertEqual(0, game.state.cards[bearer.object_id].marked_damage)
                self.replay(game, load=True)

    def test_destination_loses_creature_type_then_redirects_when_type_returns(self):
        game = self.session(6145341)
        spell = self.add(game.engine, "Martyrdom", zone="hand")
        bearer = self.add(game.engine, "Indomitable Ancients")
        aura = self.add(game.engine, "Imprisoned in the Moon", seat="B", zone="hand")
        destroy = self.add(game.engine, "Disenchant", zone="hand")
        shocks = [self.add(game.engine, "Shock", seat="B", zone="hand", ref=f"type-shock-{idx}")
                  for idx in range(2)]
        self.add(game.engine, "Mountain", seat="B")
        self.add(game.engine, "Mountain", seat="B", ref="second-type-mountain")
        self.add(game.engine, "Plains", ref="first-type-plains")
        self.add(game.engine, "Plains", ref="second-type-plains")
        game.state.players["A"].mana_pool.update({"C": 2, "W": 3})
        self.prepare(game, actor="B", active="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.grant(game, spell, bearer)
        self.activate(game, "A", bearer, "A")
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", aura, [bearer.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.offers(game, "A", bearer))
        self.cast_for(game, "B", shocks[0], ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual(1, len(game.state.damage_redirections))
        self.cast_for(game, "A", destroy, [aura.ref])
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", shocks[1], ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)
        self.assertEqual(1, game.state.cards[bearer.object_id].marked_damage)
        self.replay(game, load=True)

    def test_grantor_authority_withdrawal_keeps_old_finite_route_exact(self):
        from quorune.granted_activation_model import GRANTOR_REDIRECTION_CAPABILITY

        original = self.db.lookup("Martyrdom", fuzzy=False)
        wider = replace(original, oracle_text=original.oracle_text.replace("Only you may activate this ability.", "Any player may activate this ability."))
        self.assertNotEqual("exact", compile_oracle_card(wider, capability_registry=self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == GRANTOR_REDIRECTION_CAPABILITY).update(
            status="blocked", blockers=["grantor-only authority unavailable"],
        )
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = {row["test_id"] for row in declarations["declarations"]}
        disabled = CapabilityRegistry(raw)
        result = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
        disabled.mark_evidence_verified(validate_capability_evidence_index(result, registry=disabled))
        self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=disabled).status)
        self.assertEqual("exact", compile_oracle_card(self.db.lookup("Nomads en-Kor", fuzzy=False),
                                                      capability_registry=disabled).status)
