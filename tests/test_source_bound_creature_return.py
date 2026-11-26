"""Exercise the original-source condition across a natural end step."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.delayed_object_model import SOURCE_BOUND_CREATURE_RETURN_CAPABILITY, source_bound_creature_return_effect
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.source_bound_delayed_return import SOURCE_BOUND_NEXT_END_STEP
from scripts.build_test_database import build_fixture_database


class SourceBoundCreatureReturnTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Keeper of the Quiet"
    profile = "commander_review"

    @classmethod
    def use_database(cls, path, commander, land):
        cls.db = CardDatabase(path)
        cls.land_name = land
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source-bound return", [DeckEntry(commander, 1, "commander"),
                                  DeckEntry(land, 30)], [commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-bound-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "activation-target-view-cards.json",
            "activation-paid-creature-subtype-cards.json", "mutate-casting-cards.json",
            "activated-self-transform-cards.json", "manifest-cards.json", "any-number-target-cards.json",
            "fixed-target-reanimation-cards.json", "controlled-small-creature-delayed-return-cards.json",
        )], path)
        cls.use_database(path, "Generic Bound Commander", "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def game_with_death(self, seed, *, blink=False, exile=False):
        game = self.session(seed)
        source = self.add(game.engine, self.source_name)
        elf = self.add(game.engine, "Llanowar Elves", ref="small-graveyard-card")
        kill = self.add(game.engine, "Murder", zone="hand")
        other = None
        if blink:
            self.add(game.engine, self.land_name, ref="end-step-blink-mana")
            other = self.add(game.engine, "Cloudshift", zone="hand")
        if exile:
            other = self.add(game.engine, "Cremate", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"B": 3, "C": 1})
        self.checkpoint(game)
        self.cast(game, "A", kill, [elf.ref])
        self.assertIsNone(self.resolve(game))
        delayed, = self.active_returns(game)
        self.assertEqual(("A", source.logical_object_id), (delayed.controller, delayed.source_logical_object_id))
        self.assertEqual([elf.object_id], delayed.referred_object_ids)
        return game, source, elf, other

    def active_returns(self, game):
        return [row for row in game.state.delayed_triggers if row.active and SOURCE_BOUND_NEXT_END_STEP in row.condition]

    def reach_end_step(self, game):
        self.until(game, lambda session: (session.state.phase, session.state.step) == ("ending", "end_step"))

    def offer_return(self, game, elf):
        self.reach_end_step(game)
        self.assertFalse(self.active_returns(game))
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("graveyard", game.state.cards[elf.object_id].zone)

    def choose_return(self, game, apply):
        answer = {"action_id": "choose", "choice": "apply" if apply else "decline"}
        before = authoritative_state_hash(game.state)
        rejection = game.act("pilot:B", answer)
        self.assertFalse(rejection.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        outcome = game.act("pilot:A", answer)
        self.assertTrue(outcome.ok, outcome.summary)

    def test_optional_return_at_the_first_end_step_with_pending_reload(self):
        for apply in (False, True):
            with self.subTest(accept=apply):
                game, _source, elf, _unused = self.game_with_death(60371041 + apply)
                self.offer_return(game, elf)
                game = self.replay(game, load=True)
                self.choose_return(game, apply)
                self.assertIsNone(self.resolve(game))
                card = game.state.cards[elf.object_id]
                self.assertEqual("battlefield" if apply else "graveyard", card.zone)
                self.assertEqual("A", card.controller)
                self.replay(game, load=True)

    def test_owner_graveyard_qualifies_when_an_opponent_controlled_the_creature(self):
        game = self.session(60371043)
        self.add(game.engine, self.source_name)
        elf = self.add(game.engine, "Llanowar Elves", ref="borrowed-owner-elf")
        theft = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        kill = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.prepare(game, active="B", actor="B", mana={"R": 1, "B": 2, "C": 3})
        self.checkpoint(game)
        self.cast(game, "B", theft, [elf.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[elf.object_id].controller)
        self.cast(game, "B", kill, [elf.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", self.active_returns(game)[0].controller)
        self.offer_return(game, elf)
        self.choose_return(game, True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A", "A"),
                         (game.state.cards[elf.object_id].zone, game.state.cards[elf.object_id].owner,
                          game.state.cards[elf.object_id].controller))
        self.replay(game, load=True)

    def test_new_source_incarnation_cannot_authorize_the_old_return(self):
        for trigger_already_stacked in (False, True):
            with self.subTest(trigger_already_stacked=trigger_already_stacked):
                game, source, elf, blink = self.game_with_death(60371044 + trigger_already_stacked, blink=True)
                original = game.state.cards[source.object_id].logical_object_id
                if trigger_already_stacked:
                    self.reach_end_step(game)
                    self.assertTrue(any(row.context.get("delayed_trigger_ref") for row in game.state.stack))
                self.cast(game, "A", blink, [source.ref])
                self.assertIsNone(self.resolve(game))
                self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
                self.reach_end_step(game)
                self.assertFalse(self.active_returns(game))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[elf.object_id].zone)
                self.replay(game, load=True)

    def test_departed_member_does_not_create_an_optional_return_choice(self):
        game, _source, elf, exile = self.game_with_death(60371046, exile=True)
        self.cast(game, "A", exile, [elf.ref])
        self.assertIsNone(self.resolve(game))
        self.reach_end_step(game)
        self.assertFalse(self.active_returns(game))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[elf.object_id].zone)
        self.replay(game, load=True)

    def test_larger_creatures_and_another_owners_graveyard_do_not_schedule(self):
        game = self.session(60371047)
        self.add(game.engine, self.source_name)
        bear = self.add(game.engine, "Grizzly Bears")
        other = self.add(game.engine, "Llanowar Elves", seat="B", ref="other-owners-elf")
        one = self.add(game.engine, "Murder", zone="hand", ref="first-invalid-death")
        two = self.add(game.engine, "Murder", zone="hand", ref="second-invalid-death")
        self.prepare(game, mana={"B": 4, "C": 2})
        self.checkpoint(game)
        self.cast(game, "A", one, [bear.ref])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", two, [other.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.active_returns(game))
        self.replay(game, load=True)

    def test_merged_small_creature_seals_both_graveyard_incarnations(self):
        game = self.session(60371048)
        self.add(game.engine, self.source_name)
        root = self.add(game.engine, "Llanowar Elves", ref="return-merge-root")
        part = self.add(game.engine, "Gemrazer", zone="hand")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.prepare(game, mana={"G": 1, "C": 4, "B": 2})
        self.checkpoint(game)
        action = self.action(game, "A", part)
        cost, = [row["id"] for row in action["cost_options"] if row["id"].startswith("mutate")]
        result = game.act("pilot:A", {
            "action_id": action["id"], "cost_option": cost, "targets": [root.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertIsNotNone(game.state.cards[root.object_id].permanent_composition)
        self.cast(game, "A", kill, [root.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "cards": [part.ref, root.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        delayed, = self.active_returns(game)
        self.assertEqual({root.object_id, part.object_id}, set(delayed.referred_object_ids))
        self.offer_return(game, root)
        self.choose_return(game, True)
        self.assertIsNone(self.resolve(game))
        for card in (root, part):
            arrived = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", None, "A"), (arrived.zone, arrived.permanent_composition, arrived.controller))
        self.replay(game, load=True)

    def test_manifested_aura_offers_owner_attachment_after_optional_return(self):
        game = self.session(60371049)
        self.add(game.engine, self.source_name)
        hide = self.add(game.engine, "Soul Summons", zone="hand")
        weaken = self.add(game.engine, "Disfigure", zone="hand")
        aura = self.add(game.engine, "Pacifism", zone="library", ref="low-power-aura")
        receiver = self.add(game.engine, "Grizzly Bears", seat="B", ref="aura-recipient")
        self.prepare(game, mana={"W": 1, "C": 1, "B": 1})
        self.checkpoint(game)
        self.cast(game, "A", hide)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[aura.object_id].face_down)
        self.assertNotIn("Pacifism", json.dumps(game.packet("pilot:B", full=True)))
        self.cast(game, "A", weaken, [aura.ref])
        self.assertIsNone(self.resolve(game))
        self.offer_return(game, aura)
        self.choose_return(game, True)
        decision = game.state.pending_decision
        self.assertEqual(("aura.entry", ["A"]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", {"action_id": "choose", "aura_target": receiver.ref})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[aura.object_id]
        self.assertEqual(("battlefield", "A", receiver.object_id), (returned.zone, returned.controller, returned.attached_to))
        self.replay(game, load=True)

    def test_removing_the_original_source_check_is_detected(self):
        from quorune.model import DelayedTrigger
        from quorune.rules import source_bound_delayed_return as implementation
        from quorune.trigger_processing import TriggerProcessingOwner

        def witness():
            trigger = DelayedTrigger(
                trigger_id="pending-return", ref="D1", controller="A", label="Pending source-bound return",
                source_object_id="source", source_logical_object_id="source@1", event_kind="step.begin",
                condition=implementation.source_bound_end_step_condition(), stack_template={},
            )
            replacement = SimpleNamespace(logical_object_id="source@2", zone="battlefield", phased_out=False)
            state = SimpleNamespace(cards={"source": replacement}, delayed_triggers=[trigger], turn_sequence=1)
            owner = TriggerProcessingOwner(SimpleNamespace(state=state))
            day = {"phase": "ending", "step": "end_step", "player": "B"}
            self.assertEqual([], owner.matching_delayed_triggers("step.begin", day), "Original source is required")
            self.assertFalse(trigger.active)

        witness()
        with patch.object(implementation, "delayed_return_source_present", return_value=True):
            with self.assertRaisesRegex(AssertionError, "Original source is required"):
                witness()

    def test_withdrawal_and_changed_qualifiers_fail_closed(self):
        source = self.db.lookup(self.source_name, fuzzy=False)
        result = compile_oracle_card(source, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", result.status)
        for old, new in (("power 1 or less", "power 2 or less"), ("your graveyard", "a graveyard"),
                         ("next end step", "next upkeep")):
            changed = replace(source, oracle_text=source.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=self.profile)
            self.assertFalse(any(effect == source_bound_creature_return_effect()
                                 for face in result.faces for node in face.nodes for effect in node.effects))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == SOURCE_BOUND_CREATURE_RETURN_CAPABILITY).update(
            status="blocked", blockers=["Source-bound return authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        result = compile_oracle_card(source, capability_registry=registry, capability_profile=self.profile)
        self.assertNotEqual("exact", result.status)
