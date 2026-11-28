"""Native action, privacy and replay evidence for fixed hand ninjutsu grants."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragment_primitives import AbilityFragmentError
from quorune.ability_fragments import ability_fragment_from_dict
from quorune.carddb import CardDatabase
from quorune.combat_entry_activations import NINJUTSU_ACTIVATED_CAPABILITY, NINJUTSU_ACTIVATED_EVENT
from quorune.deck import DeckDefinition, DeckEntry
from quorune.granted_activation_model import HAND_NINJUTSU_GRANT_CAPABILITY
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_selection_capability_shapes import (
    FIXED_LIBRARY_SELECTION_CAPABILITY,
    FIXED_LIBRARY_SELECTION_MECHANIC,
    PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_CAPABILITY,
    PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION,
    fixed_library_selection_node_capabilities,
)
from scripts.build_test_database import build_fixture_database


class HandNinjutsuGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "hand-ninjutsu.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        copies = json.loads((fixtures / "fixed-qualified-stack-copy-cards.json").read_text(encoding="utf-8"))
        lithoform, = (card for card in copies["cards"] if card["name"] == "Lithoform Engine")
        copy_fixture = database.with_name("hand-ninjutsu-copy-card.json")
        copy_fixture.write_text(json.dumps({
            **copies,
            "cards": [lithoform],
            "rulings": [row for row in copies["rulings"] if row["oracle_id"] == lithoform["oracle_id"]],
        }), encoding="utf-8")
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "fixed-combat-entry-keyword-lifecycle-cards.json",
            fixtures / "activation-view-equivalence-cards.json",
            fixtures / "ability-kind-cost-modifier-cards.json",
            copy_fixture,
            fixtures / "hand-ninjutsu-grant-cards.json",
            fixtures / "private-fixed-top-take-or-graveyard-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Hand ninjutsu witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepare_combat(self, game, assignments, *, blocked=(), before_blockers=False):
        self.prepare(game, mana={"U": 4, "B": 3, "C": 7})
        engine = game.engine
        engine.state.config.semantic_policy = "trusted_only"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=not before_blockers,
            had_attacking_creature=True,
            defending_players=[*dict.fromkeys(defender for _, defender in assignments)],
            attackers={card.object_id: defender for card, defender in assignments},
            attack_target_context={
                card.object_id: {"target": defender, "kind": "player", "defending_player": defender}
                for card, defender in assignments
            },
            blockers={card.object_id: [] for card in blocked},
        )
        for card, defender in assignments:
            card.attacking = defender
        engine._grant_priority("A")
        engine.pump()

    def offers(self, game, card, seat="A"):
        return [
            offer for offer in game.engine._priority_action_hints(seat)["actions"]
            if offer.get("source") == card.ref and offer["id"].startswith("activate:")
        ]

    def activate_ninjutsu(self, game, offer, cost):
        self.priority(game, "A")
        result = game.act("pilot:A", {"action_id": offer["id"], "cost_objects": [cost.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def grant_offer(offers):
        return next(offer for offer in offers if "#ninjutsu-" in offer["ability"])

    @staticmethod
    def intrinsic_offer(offers):
        return next(offer for offer in offers if offer["ability"] == "ab1")

    def test_private_selection_and_once_per_turn_follow_successful_activation(self):
        game = self.session(70249001)
        observer = self.add(game.engine, "Satoru Umezawa", ref="ninjutsu-observer")
        self.assert_closed(game, observer)
        ninja = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="ninjutsu-private-card")
        intrinsic = self.add(game.engine, "Generic Ninjutsu Adept", zone="hand", ref="ninjutsu-intrinsic-card")
        other_hand = self.add(game.engine, "Generic Returning Attacker", seat="B", zone="hand", ref="ninjutsu-foreign-hand")
        noncreature = self.add(game.engine, "Generic Bound Growth", zone="hand", ref="ninjutsu-noncreature")
        wrong_zone = self.add(game.engine, "Generic Returning Attacker", zone="graveyard", ref="ninjutsu-graveyard")
        first = self.add(game.engine, "Generic Returning Attacker", ref="ninjutsu-first-attacker")
        first.owner = "B"
        second = self.add(game.engine, "Generic Returning Attacker", ref="ninjutsu-second-attacker")
        blocked = self.add(game.engine, "Generic Returning Attacker", ref="ninjutsu-blocked-attacker")
        self.add(game.engine, "Satoru Umezawa", seat="B", ref="ninjutsu-other-observer")
        viewed = tuple(
            self.add(game.engine, "Generic Bound Plains", zone="library", ref=f"ninjutsu-private-library-{index}")
            for index in range(3)
        )
        library = game.state.players["A"].zones["library"]
        viewed_ids = [card.object_id for card in viewed]
        library[:] = [object_id for object_id in library if object_id not in viewed_ids] + list(reversed(viewed_ids))
        self.prepare_combat(game, ((first, "C"), (second, "B"), (blocked, "B")), blocked=(blocked,), before_blockers=True)
        self.assertEqual([], self.offers(game, ninja))
        self.prepare_combat(game, ((first, "C"), (second, "B"), (blocked, "B")), blocked=(blocked,))
        self.checkpoint(game)
        self.assertEqual([], self.offers(game, other_hand, "B"))
        self.assertEqual([], self.offers(game, noncreature))
        self.assertEqual([], self.offers(game, wrong_zone))
        self.assertNotIn(ninja.ref, json.dumps(game.packet("pilot:D", full=True)))
        offer, = self.offers(game, ninja)
        self.assertEqual(2, len(self.offers(game, intrinsic)))
        self.assertEqual({first.ref, second.ref}, set(offer["cost_summary"]["choose_cost"][0]["legal_refs"]))
        mana_before = game.state.players["A"].mana_pool.copy()
        before = authoritative_state_hash(game.state)
        malformed = game.act("pilot:A", {"action_id": offer["id"], "cost_objects": [blocked.ref], "pay": "auto"})
        self.assertFalse(malformed.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.stack)

        self.activate_ninjutsu(game, offer, first)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertIn(first.object_id, game.state.players["B"].zones["hand"])
        self.assertEqual("hand", game.state.cards[ninja.object_id].zone)
        self.assertEqual(2, mana_before["C"] - game.state.players["A"].mana_pool["C"])
        self.assertEqual(1, mana_before["U"] - game.state.players["A"].mana_pool["U"])
        self.assertEqual(1, mana_before["B"] - game.state.players["A"].mana_pool["B"])
        self.assertEqual(["activated_ability", "triggered_ability"], [item.kind for item in game.state.stack])
        self.assertEqual(observer.object_id, game.state.stack[-1].source_object_id)
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        actor_decision = game.packet("pilot:A", full=True)["decision"]
        offered = actor_decision["legal_actions"][0]["choice_schema"]
        self.assertEqual([card.ref for card in viewed], offered["legal_refs"])
        self.assertEqual(
            {"minimum": 1, "maximum": 1, "label": "Hand", "order": "selection_order"},
            offered["partitions"]["hand"],
        )
        self.assertEqual("bottom_to_top", offered["partitions"]["bottom"]["order"])
        for seat in "BCD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in viewed:
                self.assertNotIn(card.ref, packet)
        game = self.replay(game, load=True)
        answer = {"action_id": "choose", "cards": {"hand": [viewed[1].ref], "bottom": [viewed[0].ref, viewed[2].ref]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", answer).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "cards": [viewed[1].ref]}).ok)
        self.assertFalse(game.act("pilot:A", {
            "action_id": "choose", "cards": {"hand": [], "bottom": [card.ref for card in viewed]},
        }).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", answer)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[viewed[1].object_id].zone)
        self.assertEqual([viewed[0].object_id, viewed[2].object_id], game.state.players["A"].zones["library"][:2])
        for seat in "BCD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in viewed:
                self.assertNotIn(card.ref, packet)
        self.assertEqual(set(game.engine.seats), set(game.state.cards[ninja.object_id].revealed_to))
        self.resolve_top(game)
        entrant = game.state.cards[ninja.object_id]
        self.assertEqual(("battlefield", True, "C"), (entrant.zone, entrant.tapped, entrant.attacking))

        self.activate_ninjutsu(game, self.intrinsic_offer(self.offers(game, intrinsic)), second)
        self.assertEqual(0, self.trigger_count(game, observer))
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[intrinsic.object_id].attacking)
        self.replay(game, load=True)

    def test_fixed_library_selection_versions_coexist_in_live_replay(self):
        game = self.session(70249011)
        satoru = self.add(game.engine, "Satoru Umezawa", ref="partition-version-observer")
        sarinth = self.add(game.engine, "Sarinth Steelseeker", ref="singleton-version-observer")
        entrant = self.add(game.engine, "Ornithopter", zone="hand", ref="partition-artifact-entrant")
        attacker = self.add(game.engine, "Generic Returning Attacker", ref="partition-returned-attacker")
        forest = self.add(game.engine, "Forest", zone="library", ref="singleton-private-top")
        looked = tuple(
            self.add(game.engine, "Generic Bound Plains", zone="library", ref=f"partition-private-{index}")
            for index in range(3)
        )
        library = game.state.players["A"].zones["library"]
        ordered = [forest.object_id, *(card.object_id for card in reversed(looked))]
        library[:] = [oid for oid in library if oid not in ordered] + ordered

        effects_by_version = {}
        for source, version in ((satoru, 16), (sarinth, 15)):
            self.assert_closed(game, source)
            effect, = (
                effect for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                for effect in program.effects if effect.get("op") == "fixed_library_selection"
            )
            self.assertEqual(version, effect["schema_version"])
            effects_by_version[version] = effect

        def dependencies(effect):
            return fixed_library_selection_node_capabilities(
                effects=(effect,), target_schema=None, mechanic_ids=(FIXED_LIBRARY_SELECTION_MECHANIC,),
            )

        self.assertEqual((FIXED_LIBRARY_SELECTION_CAPABILITY,), dependencies(effects_by_version[16]))
        self.assertEqual(
            (FIXED_LIBRARY_SELECTION_CAPABILITY, PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_CAPABILITY),
            dependencies(effects_by_version[15]),
        )
        for original, crossed in ((15, 16), (16, 15)):
            self.assertEqual((), dependencies({**effects_by_version[original], "schema_version": crossed}))

        self.prepare_combat(game, ((attacker, "B"),))
        self.checkpoint(game)
        grant, = self.offers(game, entrant)
        self.activate_ninjutsu(game, grant, attacker)

        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        action, = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        schema = action["choice_schema"]
        self.assertEqual([card.ref for card in looked], schema["legal_refs"])
        self.assertEqual((1, 1), (schema["partitions"]["hand"]["minimum"], schema["partitions"]["hand"]["maximum"]))
        self.assertEqual("bottom_to_top", schema["partitions"]["bottom"]["order"])
        for seat in "BCD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(card.ref not in json.dumps(packet) for card in (*looked, forest)))
        game = self.replay(game, load=True)
        partition = {"action_id": action["id"], "cards": {
            "hand": [looked[1].ref], "bottom": [looked[2].ref, looked[0].ref],
        }}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", partition).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": action["id"], "choice": "keep"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", partition)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[looked[1].object_id].zone)
        self.assertEqual(
            [looked[2].object_id, looked[0].object_id], game.state.players["A"].zones["library"][:2],
        )

        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[entrant.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, sarinth))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]
        top, = decision["ctx"]["objects"]
        self.assertEqual(forest.ref, top["id"])
        action, = decision["legal_actions"]
        self.assertEqual(("reveal_and_take", "put_into_graveyard", "keep"), tuple(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            self.assertNotIn(forest.ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": action["id"], "cards": {"hand": [forest.ref]}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": action["id"], "choice": "reveal_and_take"})
        self.assertTrue(result.ok, result.summary)
        taken = game.state.cards[forest.object_id]
        self.assertEqual("hand", taken.zone)
        self.assertEqual(set("ABCD"), set(taken.revealed_to))
        self.replay(game, load=True)

    def test_returning_grantor_leaves_paid_activation_and_expires_unannounced_grant(self):
        game = self.session(70249002)
        observer = self.add(game.engine, "Satoru Umezawa", ref="ninjutsu-returning-grantor")
        entrant = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="ninjutsu-retained-activation")
        other = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="ninjutsu-expiring-grant")
        remaining = self.add(game.engine, "Generic Returning Attacker", ref="ninjutsu-remaining-attacker")
        self.prepare_combat(game, ((observer, "D"), (remaining, "C")))
        self.checkpoint(game)
        offer, = self.offers(game, entrant)
        stale, = self.offers(game, other)
        self.activate_ninjutsu(game, offer, observer)
        self.assertEqual("hand", observer.zone)
        self.assertEqual(["activated_ability"], [item.kind for item in game.state.stack])
        self.assertEqual([], self.offers(game, other))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": stale["id"], "cost_objects": [remaining.ref], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[entrant.object_id]
        self.assertEqual(("battlefield", True, "D"), (current.zone, current.tapped, current.attacking))
        self.replay(game, load=True)

    def test_hand_grant_refuses_forged_shape_or_missing_child(self):
        record = self.db.lookup("Satoru Umezawa", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = [node for node in ir.faces[0].nodes if node.event == NINJUTSU_ACTIVATED_EVENT]
        self.assertIn(NINJUTSU_ACTIVATED_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual({"schema_version": 1, "kind": "once_per_turn"}, trigger.trigger_limit)
        self.assertEqual(PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION, trigger.effects[0]["schema_version"])
        grant, = [node for node in ir.faces[0].nodes if HAND_NINJUTSU_GRANT_CAPABILITY in node.capability_dependencies]
        encoded, = grant.handlers[0]["modifier"]["add_ability_fragments"]
        fragment = ability_fragment_from_dict(encoded)
        self.assertEqual(("hand",), fragment.activation_zones)
        for extra in ({"sorcery_speed": True}, {"cost_text": "{X}"}, {"schema_version": []}, {"grant_incarnation": "wrong"}):
            with self.subTest(extra=extra), self.assertRaises(AbilityFragmentError):
                ability_fragment_from_dict({**encoded, "value": {**encoded["value"], **extra}})
        for text in ("Each creature card in any hand has ninjutsu {2}{U}{B}.",
                     "Each creature card in your hand has ninjutsu {U/B}.",
                     "Each creature card in your hand has commander ninjutsu {2}{U}{B}.",
                     "Whenever you activate a ninjutsu ability, draw a card. This ability triggers only once each turn."):
            with self.subTest(text=text):
                modified = replace(record, oracle_text=text)
                self.assertNotEqual("exact", compile_oracle_card(modified, capability_registry=self.registry).status)
        from quorune.compiler.fixed_library_selection_templates import fixed_library_selection_effect_template

        ordinary = fixed_library_selection_effect_template(
            "Look at the top three cards of your library. "
            "Put one of them into your hand and the rest on the bottom of your library in any order."
        )
        self.assertNotIn("schema_version", ordinary.compiled()[1][0])

        game = self.session(70249004)
        attacker = self.add(game.engine, "Generic Returning Attacker", ref="ninjutsu-mutation-attacker")
        entrant = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="ninjutsu-mutation-target")
        self.add(game.engine, "Satoru Umezawa", ref="ninjutsu-mutation-provider")
        self.prepare_combat(game, ((attacker, "B"),))
        self.assertEqual(1, len(self.offers(game, entrant)))
        real_lookup = game.engine.semantics.get
        with patch.object(game.engine.semantics, "get",
                          side_effect=lambda key: None if key == fragment.semantic_key else real_lookup(key)):
            self.assertEqual([], self.offers(game, entrant))


    def test_countered_observer_does_not_rearm_during_the_same_turn(self):
        game = self.session(70249011)
        observer = self.add(game.engine, "Satoru Umezawa", ref="countered-observer")
        first = self.add(game.engine, "Generic Returning Attacker", ref="countered-first-attacker")
        second = self.add(game.engine, "Generic Returning Attacker", ref="countered-second-attacker")
        first_ninja = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="countered-first-ninja")
        second_ninja = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="countered-second-ninja")
        counter = self.add(game.engine, "Stifle", seat="B", zone="hand", ref="countered-observer-stifle")
        self.assert_closed(game, observer)
        self.assert_closed(game, counter)
        self.prepare_combat(game, ((first, "C"), (second, "D")))
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        library_before = list(game.state.players["A"].zones["library"])
        self.activate_ninjutsu(game, self.grant_offer(self.offers(game, first_ninja)), first)
        activation, trigger = game.state.stack
        self.assertEqual(observer.object_id, trigger.source_object_id)
        self.cast_for(game, "B", counter, [trigger.ref])
        self.resolve_top(game)
        self.assertEqual([activation.ref], [item.ref for item in game.state.stack])
        self.assertEqual(library_before, game.state.players["A"].zones["library"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[first_ninja.object_id].attacking)

        self.activate_ninjutsu(game, self.grant_offer(self.offers(game, second_ninja)), second)
        self.assertEqual(["activated_ability"], [item.kind for item in game.state.stack])
        self.assertEqual(library_before, game.state.players["A"].zones["library"])
        self.resolve_top(game)
        self.assertEqual("D", game.state.cards[second_ninja.object_id].attacking)
        self.replay(game, load=True)

    def test_silver_fur_reduces_grant_even_when_returned_as_payment(self):
        game = self.session(70249012)
        observer = self.add(game.engine, "Satoru Umezawa", ref="discount-grantor")
        master = self.add(game.engine, "Silver-Fur Master", ref="discount-reducing-attacker")
        entrant = self.add(game.engine, "Generic Returning Attacker", zone="hand", ref="discount-private-entrant")
        for source in (observer, master):
            self.assert_closed(game, source)
        self.prepare_combat(game, ((master, "C"),))
        self.checkpoint(game)
        granted = self.grant_offer(self.offers(game, entrant))
        self.assertEqual([master.ref], granted["cost_summary"]["choose_cost"][0]["legal_refs"])
        command = {"action_id": granted["id"], "cost_objects": [master.ref], "pay": "manual"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {**command, "payment": {"U": 1, "B": 1}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        mana = dict(game.state.players["A"].mana_pool)
        result = game.act("pilot:A", {**command, "payment": {"C": 1, "U": 1, "B": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[master.object_id].zone)
        for symbol in ("C", "U", "B"):
            self.assertEqual(1, mana[symbol] - game.state.players["A"].mana_pool[symbol])
        self.assertEqual(observer.object_id, game.state.stack[-1].source_object_id)
        game = self.replay(game, load=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        refs = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        result = game.act("pilot:A", {"action_id": "choose", "cards": {"hand": refs[:1], "bottom": refs[1:]}})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        card = game.state.cards[entrant.object_id]
        self.assertEqual(("battlefield", True, "C"), (card.zone, card.tapped, card.attacking))
        self.replay(game, load=True)

    def test_ninjutsu_occurrence_omission_breaks_stack_witness(self):
        def witness(seed):
            game = self.session(seed)
            observer = self.add(game.engine, "Satoru Umezawa")
            ninja = self.add(game.engine, "Generic Ninjutsu Adept", zone="hand")
            attacker = self.add(game.engine, "Generic Returning Attacker")
            self.prepare_combat(game, ((attacker, "B"),))
            self.activate_ninjutsu(game, self.intrinsic_offer(self.offers(game, ninja)), attacker)
            self.assertEqual(1, self.trigger_count(game, observer))

        witness(70249005)
        with patch("quorune.rules.activation.commit._dispatch_ninjutsu_activation", return_value=None):
            with self.assertRaises(AssertionError):
                witness(70249006)

    def test_overlapping_ninjutsu_reveals_follow_paid_stack_lifetimes(self):
        def offered_activations(game, source):
            packet = game.packet("pilot:A", full=True)
            return [
                offer for offer in packet["decision"]["ctx"]["legal"]["actions"]
                if offer["id"].startswith(f"activate:{source.ref}:")
            ]

        def assert_hand_reveal(game, card, viewers):
            current = game.state.cards[card.object_id]
            self.assertEqual("hand", current.zone)
            self.assertEqual(viewers, current.revealed_to)
            for seat in "BCD":
                hand = game.packet(f"pilot:{seat}", full=True)["state"]["players"]["A"]
                self.assertEqual(
                    seat in viewers,
                    any(row["id"] == card.ref for row in hand.get("known_hand", ())),
                )

        scenarios = (
            "lower first with a legacy copy", "upper first", "repeated grant", "new incarnation",
        )
        for seed, scenario in enumerate(scenarios, start=70249020):
            with self.subTest(scenario=scenario):
                game = self.session(seed)
                self.add(game.engine, "Satoru Umezawa", ref="reveal-provider")
                source_name = (
                    "Generic Returning Attacker" if scenario == "repeated grant" else "Generic Ninjutsu Adept"
                )
                ninja = self.add(game.engine, source_name, zone="hand", ref="reveal-card")
                first = self.add(game.engine, "Generic Returning Attacker", ref="reveal-first-attacker")
                second = self.add(game.engine, "Generic Returning Attacker", ref="reveal-second-attacker")
                counter_b = self.add(game.engine, "Stifle", seat="B", zone="hand", ref="reveal-counter-b")
                counter_c = self.add(game.engine, "Stifle", seat="C", zone="hand", ref="reveal-counter-c")
                if scenario == "lower first with a legacy copy":
                    copier = self.add(game.engine, "Lithoform Engine", ref="reveal-copier")
                if scenario == "new incarnation":
                    bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="reveal-bounce")

                self.prepare_combat(game, ((first, "C"), (second, "D")))
                game.state.players["B"].mana_pool["U"] = 2
                game.state.players["C"].mana_pool["U"] = 1
                if scenario in {"upper first", "new incarnation"}:
                    ninja.revealed_to.append("C")
                baseline = list(ninja.revealed_to)
                original_incarnation = ninja.logical_object_id
                public = list(game.engine.seats)
                self.checkpoint(game)
                assert_hand_reveal(game, ninja, baseline)

                first_offer = (
                    self.grant_offer if scenario in {"repeated grant", "upper first"} else self.intrinsic_offer
                )
                second_offer = self.intrinsic_offer if scenario == "upper first" else self.grant_offer
                self.activate_ninjutsu(game, first_offer(offered_activations(game, ninja)), first)
                original, = (item for item in game.state.stack if item.source_object_id == ninja.object_id)

                copied_ref = None
                if scenario == "lower first with a legacy copy":
                    self.priority(game, "A")
                    copy_action = next(
                        offer for offer in offered_activations(game, copier)
                        if original.ref in json.dumps(offer.get("target_schema", {}))
                    )
                    result = game.act("pilot:A", {
                        "action_id": copy_action["id"], "targets": [original.ref], "pay": "auto",
                    })
                    self.assertTrue(result.ok, result.summary)
                    self.resolve_top(game)
                    copied = game.state.stack[-1]
                    self.assertEqual(original.ref, copied.context.get("copied_from_stack"))
                    copied_ref = copied.ref

                self.priority(game, "A")
                offer = second_offer(offered_activations(game, ninja))
                command = {"action_id": offer["id"], "cost_objects": [second.ref], "pay": "auto"}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertFalse(game.act("pilot:A", {**command, "cost_objects": [first.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                assert_hand_reveal(game, ninja, public)
                self.activate_ninjutsu(game, offer, second)
                latest_ref = game.state.stack[-1].ref
                self.assertNotEqual(original.ref, latest_ref)
                game = self.replay(game, load=True)
                assert_hand_reveal(game, ninja, public)

                if scenario == "new incarnation":
                    self.resolve_top(game)
                    self.assertEqual("battlefield", game.state.cards[ninja.object_id].zone)
                    self.cast_for(game, "B", bounce, [ninja.ref])
                    self.resolve_top(game)
                    returned = game.state.cards[ninja.object_id]
                    self.assertEqual("hand", returned.zone)
                    self.assertNotEqual(original_incarnation, returned.logical_object_id)
                    self.assertEqual([], returned.revealed_to)

                    self.cast_for(game, "C", counter_c, [original.ref])
                    self.resolve_top(game)
                    self.assertNotIn(original.ref, [item.ref for item in game.state.stack])
                    self.assertEqual([], game.state.cards[ninja.object_id].revealed_to)
                else:
                    removed, survivor = (
                        (latest_ref, original.ref) if scenario == "upper first" else (original.ref, latest_ref)
                    )
                    self.cast_for(game, "B", counter_b, [removed])
                    self.resolve_top(game)
                    refs = {item.ref for item in game.state.stack}
                    self.assertNotIn(removed, refs)
                    self.assertIn(survivor, refs)
                    assert_hand_reveal(game, ninja, public)

                    self.cast_for(game, "C", counter_c, [survivor])
                    self.resolve_top(game)
                    refs = {item.ref for item in game.state.stack}
                    self.assertNotIn(survivor, refs)
                    if copied_ref is not None:
                        self.assertIn(copied_ref, refs)
                    assert_hand_reveal(game, ninja, baseline)
                self.replay(game)
