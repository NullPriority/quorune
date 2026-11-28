"""Copy the real dying creature from its sealed battlefield snapshot."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destroyed_creature_copy import DESTROYED_CREATURE_COPY_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class DestroyedCreatureHalfCopiesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "destroyed-creature-half-copies.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "destroyed-creature-half-copies-cards.json",
            fixtures / "enchanted-player-static-cards.json",
            *[fixtures / name for name in ('activation-paid-creature-subtype-cards.json', 'activation-paid-result-slot-cards.json', 'activation-public-state-cards.json', 'attack-declaration-power-cards.json')],
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed death snapshots", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_original_rejects_an_undeclared_death_copy_owner(self):
        bind, registry = register_card(self.db, self.registry, "Saw in Half")
        self.assertTrue(bind["strict_capability_ready"], bind["blockers"])
        program, = registry.programs_for_oracle(bind["oracle_id"])
        self.assertIn(DESTROYED_CREATURE_COPY_CAPABILITY, program.capability_dependencies)
        reduced = tuple(cap for cap in program.capability_dependencies if cap != DESTROYED_CREATURE_COPY_CAPABILITY)
        without = replace(program, capability_dependencies=reduced,
                          capability_closure=self.registry.closure(reduced, profile="commander_review").to_dict())
        runtime = bind_semantic_program_runtime(without, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(runtime["strict"], runtime)

    def _resolve_negative_copy(self, seed):
        game = self.session(seed)
        effect = self.add(game.engine, "Saw in Half", zone="hand")
        weaken = self.add(game.engine, "Disfigure", zone="hand")
        original = self.add(game.engine, "Char-Rumbler", seat="C")
        game.engine.change_control(original.object_id, "B", reason="Prepare borrowed creature")
        for card in (effect, weaken, original):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", weaken, [original.ref], reserve={"C": 2, "B": 1})
        self.resolve_top(game)
        self.assertEqual((-3, 1), tuple(game.engine._numeric_stat(original.object_id, s) for s in ("power", "toughness")))
        self.cast_for(game, "A", effect, [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        copies = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(copies), "Both committed death copies must enter")
        for card in copies:
            self.assertEqual(("B", "B"), (card.owner, card.controller))
            self.assertEqual(("-1", "1"), tuple(card.annotations["copy_overrides"][k] for k in ("power", "toughness")))
            self.assertEqual((-1, 1), tuple(game.engine._numeric_stat(card.object_id, s) for s in ("power", "toughness")))
            self.assertIn("double strike", {i.casefold() for i in game.engine._effective_card_data(card)["keywords"]})
        return game

    def test_actual_negative_last_known_stats_use_the_borrowing_controller_and_replay(self):
        game = self._resolve_negative_copy(70790001)
        self.replay(game, load=True)

    def test_destruction_death_omission_mutant_loses_both_actual_copies(self):
        from quorune import destruction

        owner = destruction.destroy_permanent_refs
        def without_death(*args, **kwargs):
            result = owner(*args, **kwargs)
            return replace(result, died_object_ids=())
        with patch.object(destruction, "destroy_permanent_refs", side_effect=without_death):
            with self.assertRaisesRegex(AssertionError, "Both committed death copies must enter"):
                self._resolve_negative_copy(70790002)

    def test_actual_indestructible_and_redirected_destruction_never_copy(self):
        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                game = self.session(70790005 + redirect)
                spell = self.add(game.engine, "Saw in Half", zone="hand")
                self.assert_closed(game, spell)
                if redirect:
                    replacement = self.add(game.engine, "Rest in Peace", seat="D")
                    self.assert_closed(game, replacement)
                target = self.add(game.engine, "Grizzly Bears" if redirect else "Darksteel Myr", seat="B")
                self.prepare(game, mana={"C": 2, "B": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [target.ref])
                self.resolve_top(game)
                self.assertEqual("exile" if redirect else "battlefield", game.state.cards[target.object_id].zone)
                self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
                self.replay(game, load=True)

    def test_actual_copiable_aura_uses_the_death_controller_for_two_replayable_entries(self):
        from quorune.continuous_effect_model import (ContinuousEffect, ContinuousEffectDuration,
            ContinuousEffectOrigin, ContinuousObjectIdentity, ContinuousOperation, Layer)
        from quorune.continuous_effect_state import commit_continuous_effect
        from quorune.record import authoritative_state_hash

        game = self.session(70790007)
        source = self.add(game.engine, "Saw in Half", zone="hand")
        original = self.add(game.engine, "Pacifism", seat="B", ref="copiable-aura-destruction-model")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="first-attachment")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="second-attachment")
        identity = ContinuousObjectIdentity(original.object_id, original.logical_object_id)
        for layer, sublayer, operations in (
            (Layer.TYPE, "4", (ContinuousOperation("set_types", ["Creature"], field="card_types"),
                               ContinuousOperation("set_types", [], field="subtypes"))),
            (Layer.POWER_TOUGHNESS, "7b", (ContinuousOperation("set_power_toughness", [3, 5]),)),
        ):
            commit_continuous_effect(game.state, ContinuousEffect(
                effect_id=f"fixture:noncopiable-animation:{sublayer}", source_id=original.object_id,
                layer=layer, sublayer=sublayer, timestamp=game.engine._next_zone_timestamp(),
                operations=operations, origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, locked_objects=(identity,),
            ))
        self.assert_closed(game, source)
        self.assert_closed(game, original)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [original.ref])
        self.until(game, lambda state: state.state.pending_decision is not None
                   and state.state.pending_decision.kind == "aura.entry")
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        game = self.replay(game, load=True)
        for attachment in (first, second):
            decision = game.state.pending_decision
            self.assertEqual(("aura.entry", ["B"]), (decision.kind, decision.actors))
            self.assertFalse(any(card.is_token for card in game.state.cards.values()))
            before = authoritative_state_hash(game.state)
            command = {"action_id": "choose", "aura_target": attachment.ref}
            self.assertFalse(game.act("pilot:A", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            result = game.act("pilot:B", command)
            self.assertTrue(result.ok, result.summary)
        copies = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(copies))
        self.assertEqual({"B"}, {card.controller for card in copies})
        self.assertEqual({first.object_id, second.object_id}, {card.attached_to for card in copies})
        self.assertEqual({"Pacifism"}, {game.engine.display_name(card.object_id) for card in copies})
        self.replay(game, load=True)

class HofriGhostforgeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        base = json.loads((fixtures / "bound-effect-program-cards.json").read_bytes())
        support = {"Generic Bound Commander", "Generic Bound Plains"}
        base["cards"] = [row for row in base["cards"] if row["name"] in support]
        assert {row["name"] for row in base["cards"]} == support
        base["rulings"] = []
        base_path = folder / "support.json"
        base_path.write_text(json.dumps(base), encoding="utf-8")
        database = folder / "hofri-ghostforge.sqlite3"
        build_fixture_database([base_path, fixtures / "hofri-ghostforge-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Tokens linked to a committed exile", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def spirits(self, game):
        return tuple(card for card in game.state.cards.values()
                     if card.zone == "battlefield" and card.is_token
                     and "spirit" in game.engine._effective_type_parts(card)[1])

    def reject(self, game, seat, action):
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, action)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def leave(self, game, spell, token):
        from quorune.rules.linked_death_token_copy import LEAVE_EVENT

        self.cast(game, "B", spell, [token.ref])
        self.resolve_top(game)
        departure, = tuple(item for item in game.state.stack if item.source_object_id == token.object_id)
        self.assertEqual(LEAVE_EVENT, game.engine.semantics.get(departure.semantic_key).event)
        self.assertIsNone(self.resolve(game))

    def test_actual_hofri_uses_simultaneous_death_lki_with_source_gone_and_returns_to_owner(self):
        from quorune.rules.linked_death_token_copy import TOKEN_LINK

        game = self.session(707_021_158)
        source = self.add(game.engine, "Hofri Ghostforge")
        victim = self.add(game.engine, "Elvish Visionary", seat="B")
        opponent = self.add(game.engine, "Grizzly Bears", seat="C")
        other_exile = self.add(game.engine, "Grizzly Bears", seat="D", zone="exile", ref="UNRELATED-EXILE")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        game.engine.change_control(victim.object_id, "A", reason="Prepare a borrowed nontoken creature")
        victim.counters["+1/+1"] = 2
        victim.tapped = True
        for card in (source, victim, wrath, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 2})
        game.state.players["B"].mana_pool["U"] = 1
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        hand_size = len(game.state.players["A"].zones["hand"])
        self.cast(game, "A", wrath)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "exile", "graveyard"),
                         tuple(game.state.cards[card.object_id].zone for card in (source, victim, opponent)))
        token, = self.spirits(game)
        self.assertEqual(("A", "A"), (token.owner, token.controller))
        self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, key) for key in ("power", "toughness")))
        self.assertFalse(token.tapped)
        self.assertFalse(token.counters)
        self.assertEqual(hand_size, len(game.state.players["A"].zones["hand"]), "The copied ETB draws")
        self.assertEqual(victim.object_id, token.annotations[TOKEN_LINK]["cards"][0]["identity"]["object_id"])
        game = self.replay(game, load=True)
        self.leave(game, bounce, token)
        self.assertIn(victim.object_id, game.state.players["B"].zones["graveyard"])
        self.assertNotIn(victim.object_id, game.state.players["A"].zones["graveyard"])
        self.assertEqual("exile", game.state.cards[other_exile.object_id].zone)
        self.replay(game, load=True)

    def test_actual_hofri_doubled_tokens_share_link_but_later_copies_and_reexile_do_not(self):
        from quorune.rules.linked_death_token_copy import TOKEN_LINK

        game = self.session(707_021_159)
        source = self.add(game.engine, "Hofri Ghostforge")
        victim = self.add(game.engine, "Grizzly Bears")
        season = self.add(game.engine, "Doubling Season")
        kill = self.add(game.engine, "Murder", zone="hand")
        clone = self.add(game.engine, "Cackling Counterpart", zone="hand")
        cremation = self.add(game.engine, "Cremate", seat="B", zone="hand")
        bounces = tuple(self.add(game.engine, "Unsummon", seat="B", zone="hand", ref=f"RETURN-{i}")
                        for i in range(3))
        for card in (source, season, kill, clone, cremation):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 2, "U": 2})
        game.state.players["B"].mana_pool.update({"U": 3, "B": 1})
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.cast_for(game, "A", kill, [victim.ref], reserve={"C": 1, "U": 2})
        self.assertIsNone(self.resolve(game))
        first, second = self.spirits(game)
        self.assertEqual(first.annotations[TOKEN_LINK]["cards"], second.annotations[TOKEN_LINK]["cards"])
        self.assertEqual(3, game.engine._numeric_stat(first.object_id, "power"))
        self.assertIn("haste", game.engine._combat_keywords(first))
        initial_exile = game.state.cards[victim.object_id].logical_object_id

        self.cast(game, "A", clone, [first.ref])
        self.assertIsNone(self.resolve(game))
        copies = [card for card in self.spirits(game) if card.object_id not in {first.object_id, second.object_id}]
        self.assertEqual(2, len(copies))
        game = self.replay(game, load=True)
        self.leave(game, bounces[0], copies[0])
        self.assertEqual(initial_exile, game.state.cards[victim.object_id].logical_object_id)
        self.assertEqual("exile", game.state.cards[victim.object_id].zone)
        self.leave(game, bounces[1], first)
        self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
        self.cast(game, "B", cremation, [victim.ref])
        self.assertIsNone(self.resolve(game))
        reexiled = game.state.cards[victim.object_id].logical_object_id
        self.assertNotEqual(initial_exile, reexiled)
        self.leave(game, bounces[2], second)
        self.assertEqual(("exile", reexiled),
                         (game.state.cards[victim.object_id].zone, game.state.cards[victim.object_id].logical_object_id))
        self.replay(game, load=True)

    def test_actual_hofri_never_copies_unavailable_deaths_or_graveyard_replacements(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                game = self.session(707_021_160 + replacement)
                source = self.add(game.engine, "Hofri Ghostforge")
                victim = self.add(game.engine, "Grizzly Bears")
                kill = self.add(game.engine, "Murder", zone="hand")
                cremation = self.add(game.engine, "Cremate", seat="B", zone="hand")
                if replacement:
                    self.assert_closed(game, self.add(game.engine, "Rest in Peace", seat="C"))
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 1, "B": 2})
                game.state.players["B"].mana_pool["B"] = 1
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                self.cast(game, "A", kill, [victim.ref])
                self.resolve_top(game)
                if replacement:
                    self.assertFalse(game.state.stack)
                else:
                    self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
                    self.assertEqual(1, len(game.state.stack))
                    self.reject(game, "D", {"action_id": "cast:" + cremation.ref, "targets": [victim.ref], "pay": "auto"})
                    self.cast(game, "B", cremation, [victim.ref])
                    self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", game.state.cards[victim.object_id].zone)
                self.assertFalse(self.spirits(game))
                self.replay(game, load=True)

    def test_actual_hofri_copies_battlefield_clone_values_and_links_its_physical_card(self):
        from quorune.rules.linked_death_token_copy import TOKEN_LINK

        game = self.session(707_021_162)
        source = self.add(game.engine, "Hofri Ghostforge")
        original = self.add(game.engine, "Clone", zone="hand")
        model = self.add(game.engine, "Grizzly Bears", seat="B")
        kill = self.add(game.engine, "Murder", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (source, original, kill, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "U": 1, "B": 2})
        game.state.players["B"].mana_pool["U"] = 1
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.cast_for(game, "A", original, reserve={"C": 1, "B": 2})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        option = next(option["id"] for option in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                      if model.ref in option["id"] and not option["id"].startswith("decline:"))
        chosen = game.act("pilot:A", {"action_id": "choose", "replacement": option})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Grizzly Bears", game.engine.display_name(original.object_id))
        self.cast(game, "A", kill, [original.ref])
        self.assertIsNone(self.resolve(game))
        token, = self.spirits(game)
        self.assertEqual("Grizzly Bears", game.engine.display_name(token.object_id))
        identity, = token.annotations[TOKEN_LINK]["cards"]
        self.assertEqual(original.object_id, identity["identity"]["object_id"])
        self.assertEqual("Clone", game.state.cards[original.object_id].printed_name)
        self.leave(game, bounce, token)
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[model.object_id].zone)
        self.replay(game, load=True)

    def test_hofri_requires_its_complete_original_producer_and_copiable_child(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.linked_death_token_copy import CAPABILITY, DEATH_EVENT, LEAVE_EVENT

        card = self.db.lookup("Hofri Ghostforge", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}
        producer = programs[DEATH_EVENT]
        child = programs[LEAVE_EVENT]
        self.assertEqual(child.key, producer.effects[0]["return_ability"]["value"]["semantic_key"])
        self.assertTrue(child.provenance["granted_only"])
        for program in (producer, child):
            deps = tuple(value for value in program.capability_dependencies if value != CAPABILITY)
            closure = self.registry.closure(deps, profile="commander_review").to_dict() if deps else None
            stripped = replace(program, capability_dependencies=deps, capability_closure=closure)
            self.assertFalse(bind_semantic_program_runtime(
                stripped, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        for change in ("a token creature", "a nontoken creature"):
            text = card.oracle_text.replace("another nontoken creature", change)
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
            ).status)
        with patch("quorune.compiler.linked_death_token_copy.linked_death_token_copy_nodes", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)
