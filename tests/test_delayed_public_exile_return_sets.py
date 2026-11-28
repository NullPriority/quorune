"""Public exile sets return only their sealed original incarnations once."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import LINKED_PUBLIC_SET_END_STEP_VERSION
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class OpponentGraveyardExileCastCompilerTests(unittest.TestCase):
    def test_graveyard_exile_pool_requires_full_scope_and_skips_empty_delayed_set(self):
        from types import SimpleNamespace
        from test_granted_skulk import record
        from quorune.rules.opponents_graveyards_exile_cast import (
            CAPABILITY, DELAYED_MARKER, RETURN_OPERATION, check_exile_pool_end_step,
        )

        body = ("Exile all opponents' graveyards. You may cast spells from among those cards this turn, "
                "and mana of any type can be spent to cast them. At the beginning of the next end step, "
                "if any of those cards remain exiled, return them to their owners' graveyards.")
        registry = load_default_capability_registry()

        def compile_text(text):
            row = replace(record(text, "Sorcery"), name="Synthetic Test Card")
            return compile_oracle_card(row, capability_registry=registry, capability_profile="commander_review")

        complete = body + "\nExile Synthetic Test Card."
        ir = compile_text(complete)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(any(CAPABILITY in node.capability_dependencies for node in ir.faces[0].nodes))
        for mutated in (
            complete.replace("all opponents' graveyards", "target player's graveyard"),
            complete.replace("cast spells", "play cards"),
            complete.replace("owners' graveyards", "owners' hands"),
            complete.replace("next end step", "your next end step"),
        ):
            with self.subTest(mutated=mutated):
                self.assertNotEqual("exact", compile_text(mutated).status)

        dynamic = {"op": RETURN_OPERATION, "schema_version": 1, "objects": [
            {"object_id": "one", "ref": "ref-one", "logical_object_id": "one@1"},
        ]}
        trigger = SimpleNamespace(condition={DELAYED_MARKER: 1}, once=True, referred_object_ids=["one"],
                                  stack_template={"context":{"dynamic_effects":[dynamic]}})
        state = SimpleNamespace(cards={})
        self.assertIsNone(check_exile_pool_end_step(state, trigger, {"phase":"combat","step":"beginning_combat"}))
        self.assertFalse(check_exile_pool_end_step(state, trigger, {"phase":"ending","step":"end_step"}))
        trigger.referred_object_ids = ["wrong"]
        with self.assertRaisesRegex(ValueError, "original linked set"):
            check_exile_pool_end_step(state, trigger, {"phase":"ending","step":"end_step"})


class DelayedPublicExileReturnSetTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "delayed-public-exile-return-sets.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/delayed-public-exile-return-sets-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original delayed return objects", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_actual_mnemonic_betrayal_casts_the_linked_spell_and_returns_only_uncast_originals(self):
        game = self.playing(60374021)
        source = self.add(game.engine, "Mnemonic Betrayal", zone="hand")
        paid = self.add(game.engine, "Lightning Bolt", zone="graveyard", seat="B", ref="mnemonic-red-spell")
        held = self.add(game.engine, "Grizzly Bears", zone="graveyard", seat="C", ref="mnemonic-held-card")
        land = self.add(game.engine, "Plains", zone="graveyard", seat="D", ref="mnemonic-land")
        own = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="mnemonic-own")
        unexpected = self.add(game.engine, "Lightning Bolt", zone="exile", seat="D", ref="mnemonic-outside-pool")
        for item in (source, paid):
            self.assert_closed(game, item)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"U": 3, "B": 1, "C": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("exile", "exile", "exile", "exile", "graveyard"),
                         tuple(self.current(game, item).zone for item in (source, paid, held, land, own)))
        delayed, = (row for row in game.state.delayed_triggers if row.active)
        self.assertEqual({paid.object_id, held.object_id, land.object_id}, set(delayed.referred_object_ids))
        game = self.replay(game, load=True)
        self.priority(game,"A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        ids = {row["id"] for row in actions}
        self.assertIn("cast:" + paid.ref, ids)
        self.assertNotIn("play-land:" + land.ref, ids)
        self.assertNotIn("cast:" + unexpected.ref, ids)
        before = authoritative_state_hash(game.state)
        request = {"action_id":"cast:" + paid.ref,"targets":["D"],"pay":"auto"}
        self.assertFalse(game.act("pilot:B",request).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        self.assertEqual(0,game.state.players["A"].mana_pool["R"])
        cast = game.act("pilot:A",request)
        self.assertTrue(cast.ok,cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard",self.current(game,paid).zone)
        self.assertIn(paid.object_id,game.state.players["B"].zones["graveyard"])
        self.until(game,lambda current: current.state.step=="end_step" and
                   any(item.label=="Return the remaining exiled graveyard cards" for item in current.state.stack))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard","graveyard","exile","exile"),
                         tuple(self.current(game,item).zone for item in (held,land,unexpected,source)))
        self.assertIn(held.object_id,game.state.players["C"].zones["graveyard"])
        self.assertIn(land.object_id,game.state.players["D"].zones["graveyard"])
        self.replay(game,load=True)

    def test_two_complete_originals_keep_fixed_separate_public_selection_and_locked_delay(self):
        for name, selector, targeted in (("Ghostway", "$controller", False), ("Sudden Disappearance", "$target.0", True)):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                program, = semantics.programs_for_oracle(card.oracle_id)
                exile, followup = program.effects
                self.assertEqual(("exile", "return"), (exile["phase"], followup["phase"]))
                self.assertEqual((selector, LINKED_PUBLIC_SET_END_STEP_VERSION, "next_end_step", "owner"),
                                 (exile["cards"], exile["spec"]["schema_version"], followup["spec"]["timing"],
                                  followup["spec"]["controller"]))
                self.assertEqual(exile["binding_id"], followup["binding_id"])
                self.assertEqual(exile["spec"], followup["spec"])
                self.assertEqual(targeted, program.target_schema is not None)
                forged = {**exile, "cards": "$active"}
                self.assertFalse(bind_semantic_program_runtime(
                    replace(program, effects=[forged, followup]), capability_registry=self.registry,
                    profile="commander_review",
                )["strict"])
                changed = replace(card, oracle_text=card.oracle_text.replace("the next end step", "your next end step"))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def return_trigger(self, game, seat):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == "end_step"
                   and any(item.label == "Return linked exiled objects" for item in current.state.stack))
        trigger, = (item for item in game.state.stack if item.label == "Return linked exiled objects")
        return trigger

    def test_actual_ghostway_returns_a_borrowed_card_to_its_owner_and_omits_departed_cards_and_tokens(self):
        game = self.playing(60374001)
        spell = self.add(game.engine, "Ghostway", zone="hand")
        raise_alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        donate = self.add(game.engine, "Donate", seat="B", zone="hand")
        pull = self.add(game.engine, "Pull from Eternity", seat="B", zone="hand")
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="ghostway-owner-b")
        own = self.add(game.engine, "Slippery Bogle", ref="ghostway-own-hexproof")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="ghostway-other-seat")
        artifact = self.add(game.engine, "Sol Ring")
        for card in (spell, raise_alarm, donate, pull, own):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 2, "U": 1, "W": 1})
        game.state.players["A"].mana_pool.update({"C": 3, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", donate, ["A", borrowed.ref], reserve={"W": 1})
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (self.current(game, borrowed).controller, self.current(game, borrowed).owner))
        self.cast_for(game, "A", raise_alarm, reserve={"C": 2, "W": 1})
        self.resolve_top(game)
        tokens = {card.object_id for card in game.state.cards.values() if card.is_token and card.controller == "A"}
        self.assertEqual(2, len(tokens))
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["exile", "exile", "battlefield", "battlefield"],
                         [self.current(game, card).zone for card in (borrowed, own, other, artifact)])
        delayed, = (entry for entry in game.state.delayed_triggers if entry.active)
        self.assertEqual({borrowed.object_id, own.object_id}, set(delayed.referred_object_ids))
        self.assertTrue(tokens.isdisjoint(delayed.referred_object_ids))
        self.cast_for(game, "B", pull, [self.current(game, own).ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, own).zone)
        old = self.current(game, borrowed).logical_object_id
        game = self.replay(game, load=True)
        self.assertEqual("A", self.return_trigger(game, "B").controller)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "B"), (self.current(game, borrowed).zone, self.current(game, borrowed).controller))
        self.assertNotEqual(old, self.current(game, borrowed).logical_object_id)
        self.assertEqual("graveyard", self.current(game, own).zone)
        self.assertTrue(tokens.isdisjoint(game.state.players["A"].zones["battlefield"]))
        self.replay(game, load=True)

    def test_actual_sudden_disappearance_keeps_the_artifact_land_and_waits_for_the_aura_owners_choice(self):
        game = self.playing(60374002)
        spell = self.add(game.engine, "Sudden Disappearance", zone="hand")
        aura = self.add(game.engine, "Rancor", seat="B")
        land = self.add(game.engine, "Darksteel Citadel", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="disappearance-original-creature")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        a = self.add(game.engine, "Grizzly Bears", seat="C", ref="disappearance-aura-new-target")
        self.add(game.engine, "Grizzly Bears", seat="D", ref="disappearance-aura-other-target")
        attach_objects(game.state.cards, aura, creature, source_timestamp=aura.zone_timestamp, players=game.state.players)
        self.assert_closed(game, spell)
        self.assert_closed(game, aura)
        self.prepare(game, mana={"C": 5, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        returned = (aura, creature, ring)
        self.assertEqual(["exile"] * 3, [self.current(game, piece).zone for piece in returned])
        self.assertEqual("battlefield", self.current(game, land).zone)
        self.return_trigger(game, "A")
        pending = self.resolve(game)
        self.assertEqual(("aura.entry", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(["exile"] * 3, [self.current(game, piece).zone for piece in returned])
        before = authoritative_state_hash(game.state)
        legal = {"action_id": "choose", "aura_target": a.ref}
        self.assertFalse(game.act("pilot:A", legal).ok)
        self.assertFalse(game.act("pilot:B", {**legal, "aura_target": creature.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        choice = game.act("pilot:B", legal)
        self.assertTrue(choice.ok, choice.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([("battlefield", "B")] * 3,
                         [(self.current(game, piece).zone, self.current(game, piece).controller) for piece in returned])
        self.assertEqual(a.object_id, self.current(game, aura).attached_to)
        self.replay(game, load=True)

    def test_actual_empty_ghostway_stages_one_end_step_ability_and_countering_it_consumes_it(self):
        game = self.playing(60374003)
        spell = self.add(game.engine, "Ghostway", zone="hand")
        counter = self.add(game.engine, "Stifle", seat="B", zone="hand")
        self.assert_closed(game, spell)
        self.assert_closed(game, counter)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.add(game.engine, "Island", seat="B")
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        delayed, = (entry for entry in game.state.delayed_triggers if entry.active)
        self.assertEqual([], delayed.referred_object_ids)
        trigger = self.return_trigger(game, "A")
        self.cast_for(game, "B", counter, [trigger.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(entry.active for entry in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_stifled_sudden_return_keeps_the_objects_exiled_on_later_end_steps(self):
        game = self.playing(60374004)
        spell = self.add(game.engine, "Sudden Disappearance", zone="hand")
        counter = self.add(game.engine, "Stifle", seat="B", zone="hand")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, spell)
        self.assert_closed(game, counter)
        self.prepare(game, mana={"C": 5, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", self.current(game, ring).zone)
        anchor = self.current(game, ring).logical_object_id
        trigger = self.return_trigger(game, "A")
        self.cast_for(game, "B", counter, [trigger.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "end_step")
        self.assertEqual(("exile", anchor), (self.current(game, ring).zone, self.current(game, ring).logical_object_id))
        self.assertFalse(any(entry.active for entry in game.state.delayed_triggers))
        self.assertFalse(any(item.label == "Return linked exiled objects" for item in game.state.stack))
        self.replay(game, load=True)
