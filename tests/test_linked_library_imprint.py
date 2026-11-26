"""Actual paired private library imprint, reveal and replacement-aware entry."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_library_imprint_model import (
    LIBRARY_IMPRINT_CAPABILITY, library_imprint_death_effects, library_imprint_selection_effect,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class LinkedLibraryImprintTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-imprint.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-library-imprint-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Private library imprint", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def act(self, game, actor, command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def top(self, game, *, count=4):
        ids = game.state.players["A"].zones["library"]
        return [game.state.cards[oid] for oid in reversed(ids[-count:])]

    def card(self, game, card):
        return game.state.cards[card.object_id]

    def ready(self, game, shell, *, mana=None, opposing=None):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, shell)
        self.prepare(game, mana=mana or {"C": 5})
        game.state.players["B"].mana_pool.update(opposing or {"C": 1, "B": 2})
        self.checkpoint(game)

    def enter(self, game, shell):
        self.cast(game, "A", shell)
        self.resolve_top(game)
        self.assertEqual("battlefield", self.card(game, shell).zone)
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == shell.object_id]

    def selection(self, game, expected):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(["pilot:A"], game.pending_principals())
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual([card.ref for card in expected], [row["id"] for row in context["objects"]])
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            for card in expected:
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)
        return context

    def imprint(self, game, selected, remainder):
        command = {"action_id": "choose",
                   "cards": {"exile": [selected.ref], "bottom": [card.ref for card in remainder]}}
        self.act(game, "A", command)
        current = self.card(game, selected)
        self.assertEqual(("exile", True, ["A"], []),
                         (current.zone, current.face_down, current.known_to, current.revealed_to))

    def assert_private_exile(self, game, card):
        current = self.card(game, card)
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(principal, full=True)
            masked, = (row for row in packet["state"]["players"]["A"]["ex"] if row["id"] == current.ref)
            self.assertEqual({"id": current.ref, "n": "?", "fd": 1}, masked)
            rendered = json.dumps(packet)
            self.assertNotIn(current.printed_name, rendered)
            self.assertNotIn(current.object_id, rendered)
        visible = json.dumps(game.packet("pilot:A", full=True))
        self.assertIn(current.ref, visible)
        self.assertIn(current.printed_name, visible)

    def add_four(self, game):
        self.add(game.engine, "Dryad Arbor", zone="library", ref="imprint-top-arbor")
        self.add(game.engine, "Grizzly Bears", zone="library", ref="imprint-top-card-2")
        self.add(game.engine, "Island", zone="library", ref="imprint-top-island")
        self.add(game.engine, "Sol Ring", zone="library", ref="imprint-top-card-4")
        return self.top(game)

    def test_paid_private_top_four_requires_one_face_down_choice_and_exact_bottom_order(self):
        game = self.session(706607301)
        shell = self.add(game.engine, "Clone Shell", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        cards = self.add_four(game)
        picked, *rest = cards
        ordered = list(reversed(rest))
        self.ready(game, shell)
        self.assertEqual(1, len(self.enter(game, shell)))
        self.selection(game, cards)
        complete = {"action_id": "choose", "cards": {"exile": [picked.ref], "bottom": [card.ref for card in ordered]}}
        self.reject(game, "B", complete)
        self.reject(game, "A", {"action_id": "choose", "cards": [picked.ref]})
        self.reject(game, "A", {"action_id": "choose", "cards": {"exile": [], "bottom": [card.ref for card in cards]}})
        self.reject(game, "A", {"action_id": "choose", "cards": {"exile": [picked.ref], "bottom": [ordered[0].ref]}})
        game = self.replay(game, load=True)
        self.imprint(game, picked, ordered)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([card.object_id for card in ordered], game.state.players["A"].zones["library"][:3])
        self.assert_private_exile(game, picked)
        self.cast(game, "B", murder, [shell.ref])
        self.assertIsNone(self.resolve(game))
        result = self.card(game, picked)
        self.assertEqual(("exile", False), (result.zone, result.face_down))
        for seat in "ABCD":
            self.assertIn(result.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        reveal, = (event for event in game.state.events if event.code == "card.reveal"
                   and event.details.get("object") == result.ref)
        self.assertEqual("A", reveal.actor)
        self.replay(game, load=True)

    def test_later_controller_cannot_look_but_their_death_trigger_puts_the_creature_in(self):
        game = self.session(706607302)
        shell = self.add(game.engine, "Clone Shell", zone="hand")
        theft = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.add(game.engine, "Vedalken Orrery", seat="B")
        self.add(game.engine, "Orb of Dreams", seat="C")
        cards = self.add_four(game)
        picked = next(card for card in cards if card.printed_name == "Grizzly Bears")
        self.ready(game, shell, opposing={"R": 1, "C": 3, "B": 2})
        self.enter(game, shell)
        self.selection(game, cards)
        self.imprint(game, picked, [card for card in cards if card != picked])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", theft, [shell.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", self.card(game, shell).controller)
        self.assert_private_exile(game, picked)
        game = self.replay(game, load=True)
        self.cast(game, "B", murder, [shell.ref])
        self.assertIsNone(self.resolve(game))
        returned = self.card(game, picked)
        self.assertEqual(("battlefield", "A", "B", False),
                         (returned.zone, returned.owner, returned.controller, returned.face_down))
        self.assertTrue(returned.tapped)
        reveal, = (i for i, event in enumerate(game.state.events) if event.code == "card.reveal"
                   and event.details.get("object") == returned.ref)
        applied, = (i for i, event in enumerate(game.state.events) if event.code == "replacement.apply"
                    and event.details.get("object") == returned.ref
                    and event.details.get("destination") == "battlefield")
        self.assertLess(reveal, applied)
        self.assertEqual("graveyard", self.card(game, shell).zone)
        self.replay(game, load=True)

    def test_early_death_and_a_later_source_incarnation_do_not_capture_old_exile(self):
        game = self.session(706607303)
        shell = self.add(game.engine, "Clone Shell", zone="hand")
        zombify = self.add(game.engine, "Zombify", zone="hand")
        murders = [self.add(game.engine, "Murder", seat="B", zone="hand", ref=f"imprint-murder-{i}")
                   for i in range(2)]
        cards = self.add_four(game)
        self.ready(game, shell, mana={"C": 8, "B": 1}, opposing={"C": 2, "B": 4})
        self.enter(game, shell)
        old_source = self.card(game, shell).logical_object_id
        self.cast(game, "B", murders[0], [shell.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("graveyard", self.card(game, shell).zone)
        self.selection(game, cards)
        old_card, *rest = cards
        self.imprint(game, old_card, rest)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", zombify, [self.card(game, shell).ref])
        self.resolve_top(game)
        self.assertNotEqual(old_source, self.card(game, shell).logical_object_id)
        next_cards = self.top(game)
        self.selection(game, next_cards)
        new_card, *rest = next_cards
        self.imprint(game, new_card, rest)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", murders[1], [self.card(game, shell).ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.card(game, old_card).face_down)
        self.assertEqual(("exile", False), (self.card(game, new_card).zone, self.card(game, new_card).face_down))
        self.assert_private_exile(game, old_card)
        self.replay(game, load=True)

    def test_actual_entry_multiplier_exiles_two_on_same_link_and_death_handles_every_card(self):
        game = self.session(706607304)
        shell = self.add(game.engine, "Clone Shell", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.add(game.engine, "Panharmonicon")
        self.add(game.engine, "Grizzly Bears", zone="library", ref="imprint-four-below-card-4")
        for i in range(3):
            self.add(game.engine, "Plains", zone="library", ref=f"imprint-four-below-{i}")
        initial = self.add_four(game)
        self.ready(game, shell)
        self.enter(game, shell)
        pending = game.state.pending_decision
        if pending.kind == "trigger.order":
            refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
            self.act(game, "A", {"action_id": "order", "triggers": refs})
        self.assertEqual(2, len(game.state.stack))
        self.selection(game, initial)
        noncreature, *rest = initial
        self.imprint(game, noncreature, rest)
        next_cards = self.top(game)
        self.selection(game, next_cards)
        creature = next(card for card in next_cards if card.printed_name == "Grizzly Bears")
        self.imprint(game, creature, [card for card in next_cards if card != creature])
        self.assertIsNone(self.resolve(game))
        self.assert_private_exile(game, noncreature)
        self.assert_private_exile(game, creature)
        game = self.replay(game, load=True)
        self.cast(game, "B", murder, [shell.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", False), (self.card(game, creature).zone, self.card(game, creature).face_down))
        self.assertEqual(("exile", False), (self.card(game, noncreature).zone, self.card(game, noncreature).face_down))
        self.replay(game, load=True)

    def test_empty_library_has_no_imprint_and_short_library_makes_the_only_choice_mandatory(self):
        for count in (0, 1):
            with self.subTest(count=count):
                game = self.session(706607310 + count)
                shell = self.add(game.engine, "Clone Shell", zone="hand")
                murder = self.add(game.engine, "Murder", seat="B", zone="hand")
                library = game.state.players["A"].zones["library"]
                for object_id in tuple(library):
                    game.engine.move_card(object_id, "graveyard", log=False)
                sole = self.add(game.engine, "Grizzly Bears", zone="library", ref="imprint-short") if count else None
                self.ready(game, shell)
                self.enter(game, shell)
                if sole is not None:
                    self.selection(game, [sole])
                    self.reject(game, "A", {"action_id": "choose", "cards": {"exile": [], "bottom": [sole.ref]}})
                    self.imprint(game, sole, [])
                self.assertIsNone(self.resolve(game))
                self.cast(game, "B", murder, [shell.ref])
                self.assertIsNone(self.resolve(game))
                if sole is not None:
                    self.assertEqual("battlefield", self.card(game, sole).zone)
                else:
                    self.assertEqual([], game.state.players["A"].zones["exile"])
                self.replay(game, load=True)

    def artifact_search(self, game, artifact, *, invalid=()):
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.act(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertEqual("semantic.search", self.resolve(game).kind)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        schema = context["legal_actions"][0]["choice_schema"]
        self.assertTrue(schema["rules_may_fail_to_find"])
        candidates = [value["id"] for value in context["search_cards"]]
        if artifact is not None:
            self.assertIn(artifact.ref, candidates)
        for card in invalid:
            self.assertNotIn(card.ref, candidates)
            self.reject(game, "A", {"action_id": "choose", "search_cards": [card.ref]})
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            view = json.dumps(game.packet(principal, full=True))
            for candidate in candidates:
                self.assertNotIn(json.dumps(candidate), view)
        chosen = {"action_id": "choose", "search_cards": [artifact.ref] if artifact is not None else []}
        self.reject(game, "B", chosen)
        game = self.replay(game, load=True)
        self.act(game, "A", chosen)
        if artifact is not None:
            current = self.card(game, artifact)
            self.assertEqual(("exile", False), (current.zone, current.face_down))
            for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
                packet = game.packet(principal, full=True)
                row, = (row for row in packet["state"]["players"]["A"]["ex"] if row["id"] == current.ref)
                self.assertEqual(current.printed_name, row["n"])
        return game

    def test_actual_hoarding_dragon_return_uses_death_controller_and_original_artifact_owner(self):
        for index, answer in enumerate(("apply", "decline")):
            with self.subTest(answer=answer):
                game = self.session(706607321 + index)
                dragon = self.add(game.engine, "Hoarding Dragon", zone="hand")
                artifact = self.add(game.engine, "Sol Ring", zone="library", ref="hoard-selected")
                wrong = self.add(game.engine, "Island", zone="library", ref="hoard-wrong-type")
                different = self.add(game.engine, "Sol Ring", seat="C", zone="library", ref="hoard-foreign")
                unrelated = self.add(game.engine, "Sol Ring", zone="exile", ref="hoard-unlinked-exile")
                theft = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
                murder = self.add(game.engine, "Murder", seat="B", zone="hand")
                self.add(game.engine, "Vedalken Orrery", seat="B")
                self.ready(game, dragon, mana={"C": 3, "R": 2}, opposing={"C": 3, "R": 1, "B": 2})
                self.assertEqual(1, len(self.enter(game, dragon)))
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                game = self.artifact_search(game, artifact, invalid=(wrong, different))
                self.assertEqual(before + 1, game.state.players["A"].stats["shuffle_count"])
                self.cast(game, "B", theft, [dragon.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("B", self.card(game, dragon).controller)
                self.cast(game, "B", murder, [dragon.ref])
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self.assertEqual(["pilot:B"], game.pending_principals())
                command = {"action_id": "choose", "choice": answer}
                self.reject(game, "A", command)
                game = self.replay(game, load=True)
                self.act(game, "B", command)
                self.assertIsNone(self.resolve(game))
                current = self.card(game, artifact)
                self.assertEqual(("hand" if answer == "apply" else "exile", "A"), (current.zone, current.owner))
                self.assertEqual("exile", self.card(game, unrelated).zone)
                self.replay(game, load=True)

    def test_actual_hoarding_dragon_search_decline_and_failure_to_find_have_distinct_shuffle(self):
        for index, search in enumerate((False, True)):
            with self.subTest(search=search):
                game = self.session(706607325 + index)
                dragon = self.add(game.engine, "Hoarding Dragon", zone="hand")
                artifact = self.add(game.engine, "Sol Ring", zone="library", ref="hoard-available-but-unselected")
                murder = self.add(game.engine, "Murder", seat="B", zone="hand")
                self.ready(game, dragon, mana={"C": 3, "R": 2})
                self.enter(game, dragon)
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                if search:
                    game = self.artifact_search(game, None)
                else:
                    self.assertEqual("semantic.choice", self.resolve(game).kind)
                    self.act(game, "A", {"action_id": "choose", "choice": "decline"})
                self.assertEqual(before + int(search), game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual("library", self.card(game, artifact).zone)
                self.cast(game, "B", murder, [dragon.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("library", self.card(game, artifact).zone)
                self.replay(game, load=True)

    def test_actual_hoarding_dragon_original_entry_after_early_death_cannot_feed_its_new_incarnation(self):
        game = self.session(706607327)
        dragon = self.add(game.engine, "Hoarding Dragon", zone="hand")
        zombify = self.add(game.engine, "Zombify", zone="hand")
        old_card = self.add(game.engine, "Sol Ring", zone="library", ref="hoard-earlier-incarnation")
        new_card = self.add(game.engine, "Clone Shell", zone="library", ref="hoard-current-incarnation")
        murders = [self.add(game.engine, "Murder", seat="B", zone="hand", ref=f"hoard-murder-{index}")
                   for index in range(2)]
        self.ready(game, dragon, mana={"C": 6, "R": 2, "B": 1}, opposing={"C": 2, "B": 4})
        self.enter(game, dragon)
        original = self.card(game, dragon).logical_object_id
        self.cast(game, "B", murders[0], [dragon.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("graveyard", self.card(game, dragon).zone)
        game = self.artifact_search(game, old_card)
        self.cast(game, "A", zombify, [self.card(game, dragon).ref])
        self.resolve_top(game)
        self.assertNotEqual(original, self.card(game, dragon).logical_object_id)
        game = self.artifact_search(game, new_card)
        self.cast(game, "B", murders[1], [self.card(game, dragon).ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.act(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.card(game, new_card).zone)
        self.assertEqual("exile", self.card(game, old_card).zone)
        self.replay(game, load=True)

    def test_actual_hoarding_dragon_two_entry_triggers_return_both_current_linked_cards(self):
        game = self.session(706607328)
        dragon = self.add(game.engine, "Hoarding Dragon", zone="hand")
        self.add(game.engine, "Panharmonicon")
        first = self.add(game.engine, "Sol Ring", zone="library", ref="hoard-copied-first")
        second = self.add(game.engine, "Clone Shell", zone="library", ref="hoard-copied-second")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.ready(game, dragon, mana={"C": 3, "R": 2})
        self.enter(game, dragon)
        if game.state.pending_decision.kind == "trigger.order":
            refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
            self.act(game, "A", {"action_id": "order", "triggers": refs})
        self.assertEqual(2, len(game.state.stack))
        game = self.artifact_search(game, first)
        game = self.artifact_search(game, second)
        self.cast(game, "B", murder, [dragon.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.act(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["hand", "hand"], [self.card(game, card).zone for card in (first, second)])
        self.replay(game, load=True)

    def test_actual_hoarding_dragon_replacement_to_hand_retains_no_link_and_no_public_identity(self):
        from unittest.mock import patch
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        collect = zone_replacements.collect_zone_change_replacement_effects
        alternatives = tuple(
            ReplacementEffect(effect_id=f"fixture:hoard-redirect-{zone}", source_id="fixture:search-redirect",
                event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
                conditions={"destination": {"eq": "exile"}},
                operations=(SetField("destination", zone),))
            for zone in ("hand", "graveyard")
        )

        def plus_redirects(host, **kwargs):
            return (*collect(host, **kwargs), *alternatives)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", side_effect=plus_redirects):
            game = self.session(706607329)
            dragon = self.add(game.engine, "Hoarding Dragon", zone="hand")
            artifact = self.add(game.engine, "Sol Ring", zone="library", ref="hoard-hidden-replacement")
            murder = self.add(game.engine, "Murder", seat="B", zone="hand")
            self.ready(game, dragon, mana={"C": 3, "R": 2})
            self.enter(game, dragon)
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            self.act(game, "A", {"action_id": "choose", "choice": "apply"})
            self.assertEqual("semantic.search", self.resolve(game).kind)
            self.act(game, "A", {"action_id": "choose", "search_cards": [artifact.ref]})
            self.assertEqual("replacement.order", game.state.pending_decision.kind)
            self.assertEqual(["pilot:A"], game.pending_principals())
            self.assertEqual("library", self.card(game, artifact).zone)
            ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
            redirect, = [row["id"] for row in ctx["options"] if "redirect-hand" in row["id"]]
            self.reject(game, "B", {"action_id": "choose", "replacement": redirect})
            game = self.replay(game, load=True)
            self.act(game, "A", {"action_id": "choose", "replacement": redirect})
            self.assertIsNone(self.resolve(game))
            self.assertEqual("hand", self.card(game, artifact).zone)
            for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                public = json.dumps(game.packet(principal, full=True))
                self.assertNotIn(json.dumps(artifact.ref), public)
                self.assertNotIn("Sol Ring", public)
            self.cast(game, "B", murder, [dragon.ref])
            self.assertIsNone(self.resolve(game))
            self.assertEqual("hand", self.card(game, artifact).zone)
            self.replay(game, load=True)

    def test_full_original_pair_requires_both_exact_triggers_and_live_dependencies(self):
        card = self.db.lookup("Clone Shell", fuzzy=False)
        for profile in ("traditional", "commander_duel", PROFILE):
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            enter, death = ir.faces[0].nodes
            self.assertEqual(("permanent.enter.self", "creature.dies.self"), (enter.event, death.event))
            self.assertEqual(tuple(card.oracle_text.splitlines()), tuple(card.oracle_text[n.span.start:n.span.end] for n in (enter, death)))
            self.assertEqual((library_imprint_selection_effect(),), tuple(enter.effects))
            self.assertEqual(library_imprint_death_effects(), tuple(death.effects))
        report, semantics = register_card(self.db, self.registry, "Clone Shell")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        pair = [p for p in semantics.programs_for_oracle(card.oracle_id)
                if LIBRARY_IMPRINT_CAPABILITY in p.capability_dependencies]
        self.assertEqual(2, len(pair))
        for program in pair:
            caps = tuple(cap for cap in program.capability_dependencies if cap != LIBRARY_IMPRINT_CAPABILITY)
            modified = replace(program, capability_dependencies=caps,
                               capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(modified, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{LIBRARY_IMPRINT_CAPABILITY}", bound["blockers"])
            if program.event == "creature.dies.self":
                malformed = replace(program, effects=tuple(reversed(program.effects)))
                self.assertFalse(bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("top four", "top five"), ("exile one face down", "you may exile one face down"),
                              ("this creature dies", "this creature leaves the battlefield"),
                              ("a creature card", "a permanent card"), ("under your control.", "under your control. Rewrite history.")):
            original = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=self.registry).status)
