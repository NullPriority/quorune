"""Native target-controller information for ordinary destruction and fixed tokens."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.controlled_artifact_token_result import (
    DESTROY_CONTROLLED_ARTIFACT_DRAW_CAPABILITY, DESTROY_CONTROLLED_ARTIFACT_TOKEN_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
ARTIFACT_DRAW_ADVENTURE = "Kellan, Inquisitive Prodigy // Tail the Suspect"


class DestroyOriginalControllerTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "destroy-original-controller-tokens.sqlite3"
        fixture = ROOT / "tests/fixtures/destroy-original-controller-tokens-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Original artifact controller", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool["U"] = 2
        self.prepare(game, mana=mana or {"R": 1})
        self.checkpoint(game)

    def assert_tokens(self, game, count):
        tokens = [card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token and card.printed_name == "Phyrexian Goblin Token"]
        self.assertEqual(count, len(tokens))
        for card in tokens:
            self.assertEqual(("A", "A"), (card.owner, card.controller))
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness")))
        if tokens:
            self.assertEqual(1, len({card.zone_timestamp for card in tokens}))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_control_change_after_cast_gives_the_resolver_tokens_for_the_foreign_owned_target(self):
        game = self.session(76351001)
        spell = self.add(game.engine, "Gleeful Demolition", zone="hand")
        target = self.add(game.engine, "Leaden Myr", seat="B")
        flash = self.add(game.engine, "Vedalken Orrery")
        steal = self.add(game.engine, "Act of Treason", zone="hand")
        self.ready(game, spell, target, flash, steal, mana={"C": 2, "R": 2})
        self.cast(game, "A", spell, [target.ref])
        self.assertEqual("B", game.state.cards[target.object_id].controller)
        self.cast(game, "A", steal, [target.ref])
        self.resolve_top(game)
        self.assertEqual(("B", "A"), (game.state.cards[target.object_id].owner, game.state.cards[target.object_id].controller))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertIn(target.object_id, game.state.players["B"].zones["graveyard"])
        self.assert_tokens(game, 3)
        self.replay(game, load=True)

    def test_actual_surviving_owned_and_destroyed_opposing_targets_use_their_own_controller(self):
        cases = (("Darksteel Ingot", "A", "battlefield", 3), ("Sol Ring", "B", "graveyard", 0))
        for index, (name, seat, destination, tokens) in enumerate(cases):
            with self.subTest(target=name):
                game = self.session(76351010 + index)
                spell = self.add(game.engine, "Gleeful Demolition", zone="hand")
                target = self.add(game.engine, name, seat=seat)
                invalid = self.add(game.engine, "Grizzly Bears", seat="C")
                self.ready(game, spell, target)
                self.priority(game, "A")
                offer = self.action(game, "A", spell)
                self.assertIn(target.ref, offer["target_schema"]["groups"][0]["legal_refs"])
                self.assertNotIn(invalid.ref, offer["target_schema"]["groups"][0]["legal_refs"])
                self.reject(game, "A", {"action_id": offer["id"], "targets": [invalid.ref], "pay": "auto"})
                self.cast(game, "A", spell, [target.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(destination, game.state.cards[target.object_id].zone)
                self.assert_tokens(game, tokens)
                self.replay(game, load=True)

    def test_actual_destination_choice_keeps_last_known_control_and_does_not_retarget_after_departure(self):
        for displaced in (False, True):
            with self.subTest(displaced=displaced):
                game = self.session(76351020 + displaced)
                spell = self.add(game.engine, "Gleeful Demolition", zone="hand")
                target = self.add(game.engine, "Sol Ring", ref="demolition-original")
                other = self.add(game.engine, "Sol Ring", seat="C", ref="demolition-unrelated-artifact")
                private = self.add(game.engine, "Boomerang", seat="D", zone="hand", ref="demolition-unrelated-hand")
                support = (
                    (self.add(game.engine, "Boomerang", seat="B", zone="hand"),) if displaced
                    else (self.add(game.engine, "Rest in Peace", seat="C"),
                          self.add(game.engine, "Leyline of the Void", seat="B"))
                )
                self.ready(game, spell, *support)
                self.cast(game, "A", spell, [target.ref])
                if displaced:
                    self.cast(game, "B", support[0], [target.ref])
                    self.resolve_top(game)
                else:
                    pending = self.resolve(game)
                    self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
                    self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
                    self.assert_tokens(game, 0)
                    for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
                        self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
                    game = self.replay(game, load=True)
                    options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                    self.assertEqual(2, len(options))
                    command = {"action_id": "choose", "replacement": options[0]["id"]}
                    self.reject(game, "B", command)
                    self.reject(game, "A", {**command, "replacement": "unoffered"})
                    result = game.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                pending = self.resolve(game)
                if pending is not None:
                    self.assertEqual("replacement.order", pending.kind)
                    principal = game.pending_principals()[0]
                    options = game.packet(principal, full=True)["decision"]["ctx"]["options"]
                    result = game.act(principal, {"action_id": "choose", "replacement": options[0]["id"]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if displaced else "exile", game.state.cards[target.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
                self.assert_tokens(game, 0 if displaced else 3)
                self.replay(game, load=True)

    def test_complete_original_binds_the_exact_controller_condition_and_rejects_wider_siblings(self):
        cap = DESTROY_CONTROLLED_ARTIFACT_TOKEN_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Gleeful Demolition")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        first, branch = program.effects
        self.assertEqual({"op": "destroy", "card": "$target.0"}, dict(first))
        self.assertEqual("target_controller_information", branch["condition"]["reference"]["kind"])
        self.assertEqual(3, branch["effects"][0]["quantity"])
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        original = self.db.lookup("Gleeful Demolition", fuzzy=False)
        for before, after in (("If you controlled", "If an opponent controlled"), ("create three", "create two"),
                              ("Destroy target artifact.", "Destroy target creature."),
                              ("creature tokens.", "creature tokens. Rewrite history.")):
            with self.subTest(unsupported=after):
                candidate = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)


    def test_optional_artifact_draw_uses_resolving_control_and_exact_targets(self):
        cases = (
            ("zero", "Sol Ring", "B", None, None, "battlefield", 0),
            ("indestructible", "Darksteel Ingot", "A", None, None, "battlefield", 1),
            ("foreign-controller", "Sol Ring", "B", None, None, "graveyard", 0),
            ("borrowed", "Leaden Myr", "B", "Act of Treason", "A", "graveyard", 1),
            ("given-away", "Leaden Myr", "A", "Act of Treason", "B", "graveyard", 0),
            ("illegal", "Sol Ring", "A", "Boomerang", "B", "hand", 0),
        )
        for index, (case, name, owner, reply, responder, destination, draws) in enumerate(cases):
            with self.subTest(case=case):
                game = self.session(76351080 + index)
                source = self.add(game.engine, ARTIFACT_DRAW_ADVENTURE)
                artifact = self.add(game.engine, name, seat=owner)
                invalid = self.add(game.engine, "Grizzly Bears", seat="C")
                response = self.add(game.engine, reply, seat=responder, zone="hand") if reply else None
                support = self.add(game.engine, "Vedalken Orrery", seat=responder) if reply == "Act of Treason" else None
                if reply == "Act of Treason":
                    self.add(game.engine, "Mountain", seat=responder, ref="attack-response-red")
                    self.add(game.engine, "Sol Ring", seat=responder, ref="attack-response-generic")
                if reply == "Boomerang":
                    for index in range(2):
                        self.add(game.engine, "Island", seat=responder, ref=f"attack-response-blue-{index}")
                self.ready(game, *(card for card in (source, artifact, response, support) if card is not None))

                self.attack(game, [(source, "B")])
                if case == "zero":
                    self.reject(game, "A", {"action_id": "choose", "targets": [invalid.ref]})
                self.target(game, "A", [] if case == "zero" else [artifact.ref])
                if response is not None:
                    self.cast(game, responder, response, [artifact.ref])
                    self.resolve_top(game)
                    if reply == "Act of Treason":
                        current = game.state.cards[artifact.object_id]
                        self.assertEqual((owner, responder), (current.owner, current.controller))
                before_hand = set(game.state.players["A"].zones["hand"])
                top_id = game.state.players["A"].zones["library"][-1]
                private_ref = game.state.cards[top_id].ref
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                new_hand = set(game.state.players["A"].zones["hand"]) - before_hand
                self.assertEqual({top_id} if draws else set(), new_hand)
                self.assertEqual(destination, game.state.cards[artifact.object_id].zone)
                if destination == "graveyard":
                    self.assertIn(artifact.object_id, game.state.players[owner].zones["graveyard"])
                for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                    self.assertNotIn(private_ref, json.dumps(game.packet(principal, full=True)))
                self.replay(game, load=True)

    def cast_adventure_face(self, game, card, face):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row.get("card") == card.ref and row.get("face") == face)
        command = {"action_id": offer["id"], "face": face, "pay": "auto"}
        if offer.get("cost_options"):
            command["cost_option"] = offer["cost_options"][0]["id"]
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_retained_investigate_and_extra_land_adventure_uses_normal_exile_and_replay(self):
        game = self.session(76351090)
        source = self.add(game.engine, ARTIFACT_DRAW_ADVENTURE, zone="hand")
        lands = [self.add(game.engine, "Plains", zone="hand", ref=f"tail-suspect-land-{index}")
                 for index in range(3)]
        self.ready(game, source, mana={"C": 2, "G": 2, "U": 2})
        before = set(game.state.cards)
        self.cast_adventure_face(game, source, "Tail the Suspect")
        self.settle(game)
        clues = [card for oid, card in game.state.cards.items()
                 if oid not in before and card.zone == "battlefield" and card.is_token]
        clue, = clues
        self.assertEqual("A", clue.controller)
        self.assertIn("Clue", game.engine._effective_card_data(clue)["type_line"])
        current = game.state.cards[source.object_id]
        self.assertEqual("exile", current.zone)
        self.assertEqual("A", current.annotations["adventure_cast_permission"]["player"])
        game = self.replay(game, load=True)
        for land in lands[:2]:
            self.priority(game, "A")
            result = game.act("pilot:A", {"action_id": "play-land:" + land.ref})
            self.assertTrue(result.ok, result.summary)
        self.priority(game, "A")
        self.reject(game, "A", {"action_id": "play-land:" + lands[2].ref})
        self.cast_adventure_face(game, source, "Kellan, Inquisitive Prodigy")
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_optional_artifact_draw_binds_only_the_closed_attack_and_current_target_controller(self):
        cap = DESTROY_CONTROLLED_ARTIFACT_DRAW_CAPABILITY
        report, semantics = register_card(self.db, self.registry, ARTIFACT_DRAW_ADVENTURE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = (row for row in semantics.programs_for_oracle(report["oracle_id"]) if cap in row.capability_dependencies)
        self.assertEqual(("creature.attacks", "battlefield", 1),
                         (program.event, program.active_zone, program.target_schema["up_to"]))
        before, branch = program.effects
        self.assertEqual({"op": "destroy_targets", "cards": "$targets", "maximum_targets": 1}, dict(before))
        self.assertEqual("target_controller_information", branch["condition"]["reference"]["kind"])
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}], list(branch["effects"]))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != cap)
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        original = self.db.lookup(ARTIFACT_DRAW_ADVENTURE, fuzzy=False)
        for old, new in (("controlled that permanent", "owned that permanent"),
                         ("destroy up to one", "destroy up to two"),
                         ("draw a card", "draw two cards"), ("target artifact", "target creature")):
            faces = ({**original.faces[0], "oracle_text": original.faces[0]["oracle_text"].replace(old, new)},
                     original.faces[1])
            candidate = replace(original, faces=faces, oracle_text=original.oracle_text.replace(old, new))
            lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", lowered.status)
