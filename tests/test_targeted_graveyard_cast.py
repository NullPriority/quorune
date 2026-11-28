from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_bound_effect_programs as bound
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_grant_riders import CAST_GRANT_RIDER_FIELD, CastGrantRiderBinding
from quorune.rules.graveyard_card_targets import targeted_graveyard_cast_runtime_dependencies
from quorune.targeted_graveyard_cast_model import (
    EQUIPPED_PLAYER_COMBAT_DAMAGE, GRAVEYARD_CAST_CAPABILITY, GRAVEYARD_CAST_MAXIMUM,
    surveil_graveyard_cast_effects, validate_graveyard_cast_effect,
)
from scripts.build_test_database import build_fixture_database


class TargetedGraveyardCastCompilerTests(unittest.TestCase):
    def test_targeted_graveyard_cast_requires_exact_scope_and_rider(self):
        from test_targeted_graveyard_return_effect_clauses import card_record
        from quorune.oracle_ir import compile_oracle_card

        registry = load_default_capability_registry()
        body = "You may cast target instant or sorcery card from an opponent's graveyard without paying its mana cost."
        rider = " If that spell would be put into their graveyard, exile it instead."

        def compile_body(text):
            return compile_oracle_card(card_record(text), capability_registry=registry, capability_profile="commander_review")

        for text, exile in ((body, False), (body + rider, True)):
            ir = compile_body(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            effect, = node.effects
            self.assertEqual("opponent", node.target_schema["owner_relation"])
            self.assertEqual(exile, effect["exile_on_graveyard"])
            program = SimpleNamespace(effects=node.effects, target_schema=node.target_schema)
            self.assertIn(GRAVEYARD_CAST_CAPABILITY, targeted_graveyard_cast_runtime_dependencies(program))
            with self.assertRaises(ValueError):
                targeted_graveyard_cast_runtime_dependencies(SimpleNamespace(
                    effects=node.effects, target_schema={**node.target_schema, "owner_relation": "any"},
                ))
        self.assertNotEqual("exact", compile_body(body.replace("without paying its mana cost", "this turn")).status)
        self.assertNotEqual("exact", compile_body(body + rider + " Draw a card for it.").status)
        with patch("quorune.compiler.targeted_graveyard_cast_templates.targeted_graveyard_cast_template", return_value=None):
            self.assertNotEqual("exact", compile_body(body).status)
        old = CastGrantRiderBinding("graveyard_exile", "spell@1", "A", "provider")
        self.assertEqual(1, old.to_dict()["schema_version"])
        self.assertEqual(old, CastGrantRiderBinding.from_dict(old.to_dict()))
        borrowed = CastGrantRiderBinding("graveyard_exile", "spell@1", "A", "provider", owner="B")
        self.assertEqual(2, borrowed.to_dict()["schema_version"])
        self.assertEqual(borrowed, CastGrantRiderBinding.from_dict(borrowed.to_dict()))
        with self.assertRaises(ValueError):
            CastGrantRiderBinding.from_dict({**borrowed.to_dict(), "schema_version": 1})


class TargetedGraveyardCastRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-graveyard-cast.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "targeted-graveyard-cast-cards.json", "discover-cards.json",
            "cascade-cards.json", "state-based-destination-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Resolving graveyard cast", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _announce(self, session, source, target, *, mana=None):
        offer = bound.BoundEffectProgramRuntimeTests.ready(self, session, source, mana or {"U": 4, "B": 4, "C": 4})
        self.checkpoint(session)
        command = {"action_id": offer["id"], "pay": "auto"}
        if offer.get("cost_options"):
            command["cost_option"] = offer["cost_options"][0]["id"]
        if target is not None:
            command["targets"] = [target.ref]
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def _choice(self, session):
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("pilot:A",), tuple(session.pending_principals()))
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        return session.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]

    def _accept(self, session, **fields):
        result = session.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def _sword_scene(self, seed):
        session = self.session(seed)
        engine = session.engine
        equipment = self.add(engine, "Sword of Once and Future")
        creature = self.add(engine, "Grizzly Bears")
        creature.acquired_control_turn_count = -1
        old = self.add(engine, "Lightning Bolt", zone="graveyard", ref="sword-old")
        large = self.add(engine, "Divination", zone="graveyard", ref="sword-too-large")
        foreign = self.add(engine, "Lightning Bolt", seat="B", zone="graveyard", ref="sword-foreign")
        keep = self.add(engine, "Generic Bound Plains", zone="library", ref="sword-keep-private")
        top = self.add(engine, "Sign in Blood", zone="library", ref="sword-sorcery-top")
        for card in (equipment, top, old):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 2})
        self.checkpoint(session)
        equip, = [
            action for action in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if action["id"].startswith(f"activate:{equipment.ref}:")
        ]
        result = session.act("pilot:A", {"action_id": equip["id"], "targets": [creature.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        attached = session.state.cards[equipment.object_id]
        self.assertEqual(creature.object_id, attached.attached_to)
        self.assertEqual((4, 4), tuple(engine._numeric_stat(creature.object_id, stat)
                                      for stat in ("power", "toughness")))
        self.attack(session, [(creature, "B")])
        self.until(session, lambda current: any(item.source_object_id == equipment.object_id
                                                for item in current.state.stack))
        trigger, = session.state.stack
        self.assertEqual([], trigger.targets)
        self.assertEqual(36, session.state.players["B"].life)
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("ordered_partition", schema["shape"])
        for actor in "BCD":
            packet = json.dumps(session.packet(f"pilot:{actor}", full=True))
            self.assertNotIn(top.ref, packet)
            self.assertNotIn(keep.ref, packet)
        return session, top, keep, old, large, foreign

    def test_sword_native_combat_surveil_then_untargeted_free_cast_and_exile_replay(self):
        session, top, keep, old, large, foreign = self._sword_scene(702573001)
        session = self.replay(session, load=True)
        self._accept(session, cards={"top": [keep.ref], "graveyard": [top.ref]})
        options = self._choice(session)
        chosen = session.state.cards[top.object_id]
        self.assertEqual({old.ref, chosen.ref}, {row["card"] for row in options})
        self.assertNotIn(keep.ref, json.dumps(options))
        selected, = [row for row in options if row["card"] == chosen.ref]
        for actor in "BCD":
            self.assertNotIn(keep.ref, json.dumps(session.packet(f"pilot:{actor}", full=True)))
        malicious = {"action_id": "choose", "choice": "cast", "cast": {
            "cost_option": selected["id"], "card": foreign.ref, "targets": ["B"],
        }}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", malicious).ok)
        self.assertFalse(session.act("pilot:B", malicious).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self._accept(session, choice="cast", cast={
            "cost_option": selected["id"], "card": chosen.ref, "targets": ["B"],
        })
        spell, = session.state.stack
        self.assertEqual(chosen.object_id, spell.card_object_id)
        cast_card = session.state.cards[chosen.object_id]
        rider = CastGrantRiderBinding.from_dict(cast_card.annotations[CAST_GRANT_RIDER_FIELD])
        self.assertEqual(("A", "A", cast_card.logical_object_id), (rider.caster, rider.owner, rider.logical_object_id))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(34, session.state.players["B"].life)
        self.assertEqual("exile", session.state.cards[chosen.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[large.object_id].zone)
        self.assertEqual("library", session.state.cards[keep.object_id].zone)
        self.replay(session, load=True)

    def test_sword_native_decline_preserves_surveil_and_graveyard(self):
        session, top, keep, old, _large, _foreign = self._sword_scene(702573002)
        self._accept(session, cards={"top": [top.ref, keep.ref], "graveyard": []})
        option, = self._choice(session)
        self.assertEqual(old.ref, option["card"])
        self._accept(session, choice="decline")
        self.assertEqual("library", session.state.cards[top.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[old.object_id].zone)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_sword_requires_surveil_then_exact_equipped_combat_scope_and_cast_bound(self):
        record = self.db.lookup("Sword of Once and Future", fuzzy=False)

        def compile_current(text):
            return compile_oracle_card(replace(record, oracle_text=text),
                                       capability_registry=self.registry, capability_profile="commander_review")

        def assert_exact():
            ir = compile_current(record.oracle_text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = [node for face in ir.faces for node in face.nodes if node.event == "damage.dealt"]
            self.assertEqual(surveil_graveyard_cast_effects(), tuple(node.effects))
            self.assertIsNone(node.target_schema)
            return node

        node = assert_exact()
        for original, substitute in (
            ("combat damage to a player", "combat damage to a planeswalker"),
            ("surveil 2", "surveil 1"),
            ("mana value 2", "mana value 3"),
            ("exile it instead", "return it to your hand instead"),
        ):
            with self.subTest(original=original):
                self.assertNotEqual("exact", compile_current(record.oracle_text.replace(original, substitute)).status)
        with patch("quorune.compiler.targeted_graveyard_cast_templates._SURVEIL_AND_CAST") as regex:
            regex.fullmatch.return_value = None
            with self.assertRaises(AssertionError):
                assert_exact()
        with self.assertRaises(ValueError):
            validate_graveyard_cast_effect({**node.effects[-1], GRAVEYARD_CAST_MAXIMUM: 2.0})
        altered = SimpleNamespace(
            effects=tuple(reversed(node.effects)), target_schema=None, event="damage.dealt", active_zone="battlefield",
            coverage=node.mechanics, cost_schema=None, handlers=(), event_condition=EQUIPPED_PLAYER_COMBAT_DAMAGE,
        )
        with self.assertRaises(ValueError):
            targeted_graveyard_cast_runtime_dependencies(altered)

    def test_original_memory_plunder_binds_the_borrowed_card_and_resumes_before_priority(self):
        for index, mode in enumerate(("cast", "decline", "unpayable")):
            with self.subTest(mode=mode):
                session = self.session(33886610 + index)
                engine = session.engine
                source = self.add(engine, "Memory Plunder", zone="hand")
                target = self.add(engine, "Tormenting Voice" if mode == "unpayable" else "Lightning Bolt",
                                  seat="B", zone="graveyard", ref="plunder-target")
                self.add(engine, "Lightning Bolt", zone="graveyard", ref="own-ineligible")
                self.assert_closed(session, source)
                self.assert_closed(session, target)
                if mode == "unpayable":
                    for object_id in tuple(session.state.players["A"].zones["hand"]):
                        if object_id != source.object_id:
                            engine.move_card(object_id, "exile", log=False)
                self._announce(session, source, target)
                if mode == "unpayable":
                    self.assertIsNone(self.resolve(session))
                else:
                    options = self._choice(session)
                    self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
                    command = {"action_id": "choose", "choice": "cast",
                               "cast": {"cost_option": options[0]["id"], "targets": ["C"]}}
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act("pilot:B", command).ok)
                    self.assertFalse(session.act("pilot:A", {**command, "cast": {**command["cast"], "card": "own-ineligible"}}).ok)
                    self.assertFalse(session.act("pilot:A", {**command, "cast": {**command["cast"], "x": 1}}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    session = self.replay(session, load=True)
                    if mode == "cast":
                        from quorune.semantic_choices.targeted_graveyard_cast import targeted_graveyard_cast_options

                        parent, = session.state.stack
                        snapshots = parent.context["target_snapshots"]
                        incomplete = {key: value for key, value in snapshots[target.ref].items()
                                      if key != "zone_change_counter"}
                        with patch.dict(snapshots, {target.ref: incomplete}), self.assertRaises(GameRuleError):
                            targeted_graveyard_cast_options(
                                session.engine, item=parent, actor="A",
                                effect=session.state.pending_decision.continuation["effect"],
                            )
                        life = session.state.players["C"].life
                        self._accept(session, choice="cast", cast=command["cast"])
                        spell, = session.state.stack
                        self.assertEqual(("A", target.object_id), (spell.controller, spell.card_object_id))
                        self.assertEqual("B", session.state.cards[target.object_id].owner)
                        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                        self.assertIsNone(self.resolve(session))
                        self.assertEqual(life - 3, session.state.players["C"].life)
                    else:
                        self._accept(session, choice="decline")
                self.assertIn(target.object_id, session.state.players["B"].zones["graveyard"])
                self.replay(session, load=True)

    def test_original_arcane_heist_encodes_before_priority_and_exiles_the_borrowed_spell(self):
        session = self.session(33886620)
        engine = session.engine
        source = self.add(engine, "Arcane Heist", zone="hand")
        target = self.add(engine, "Lightning Bolt", seat="B", zone="graveyard")
        next_target = self.add(engine, "Lightning Bolt", seat="B", zone="graveyard", ref="heist-copy-target")
        carrier = self.add(engine, "Generic Bound Body")
        carrier.acquired_control_turn_count = -1
        self.assert_closed(session, source)
        self._announce(session, source, target, mana={"U": 2, "C": 2})
        option, = self._choice(session)
        self._accept(session, choice="cast", cast={"cost_option": option["id"], "targets": ["C"]})
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        self.assertEqual(target.object_id, session.state.stack[-1].card_object_id)
        borrowed = session.state.cards[target.object_id]
        rider = CastGrantRiderBinding.from_dict(borrowed.annotations[CAST_GRANT_RIDER_FIELD])
        self.assertEqual(("A", "B", borrowed.logical_object_id), (rider.caster, rider.owner, rider.logical_object_id))
        self.assertEqual("stack", session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self._accept(session, cards=[carrier.ref])
        self.assertEqual("exile", session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id, session.state.players["B"].zones["exile"])
        self.assertNotIn(CAST_GRANT_RIDER_FIELD, session.state.cards[target.object_id].annotations)
        self.attack(session, [(carrier, "B")])
        self.until(session, lambda current: any(str(item.semantic_key).startswith("builtin:cipher:")
                                                for item in current.state.stack))
        self.assertEqual("selection.exile_cast", self.resolve(session).kind)
        accepted = session.act("pilot:A", {"action_id": "cast", "choice": "cast",
                                           "cast": {"targets": [next_target.ref]}})
        self.assertTrue(accepted.ok, accepted.summary)
        option, = self._choice(session)
        self._accept(session, choice="cast", cast={"cost_option": option["id"], "targets": ["C"]})
        self.assertIsNone(self.resolve(session))
        self.assertIn(next_target.object_id, session.state.players["B"].zones["exile"])
        self.assertEqual("exile", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_original_graveyard_cast_entry_triggers_preserve_scope_and_destination(self):
        for index, (name, owner, color, destination) in enumerate((
            ("Impulsivity", "A", "R", "exile"), ("Chancellor of the Spires", "B", "U", "graveyard"),
        )):
            with self.subTest(source=name):
                session = self.session(33886630 + index)
                source = self.add(session.engine, name, zone="hand")
                target = self.add(session.engine, "Lightning Bolt", seat=owner, zone="graveyard")
                self.assert_closed(session, source)
                self._announce(session, source, None, mana={"C": 9, color: 4})
                self.assertEqual("semantic.target", self.resolve(session).kind)
                self._accept(session, targets=[target.ref])
                option, = self._choice(session)
                self._accept(session, choice="cast", cast={"cost_option": option["id"], "targets": ["C"]})
                self.assertIsNone(self.resolve(session))
                self.assertEqual(destination, session.state.cards[target.object_id].zone)
                self.replay(session, load=True)

    def test_borrowed_cast_cost_replacement_resumes_the_same_parent_and_pays_once(self):
        session = self.session(33886641)
        engine = session.engine
        source = self.add(engine, "Memory Plunder", zone="hand")
        target = self.add(engine, "Cascade Trial", seat="B", zone="graveyard")
        artifact = self.add(engine, "Generic Bound Mentor", ref="plunder-sacrifice")
        victim = self.add(engine, "Generic Bound Body", seat="C")
        for index in range(2):
            self.add(engine, "Dauthi Voidwalker", seat="B", ref=f"plunder-replace-{index}")
        self.assert_closed(session, source)
        self.assert_closed(session, target)
        self._announce(session, source, target)
        option, = self._choice(session)
        self.assertIn(artifact.ref, option["choice_schema"]["sacrifice_cards"]["legal_refs"])
        self._accept(session, choice="cast", cast={"cost_option": option["id"],
                     "targets": [victim.ref], "sacrifice_cards": [artifact.ref]})
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[artifact.object_id].zone)
        session = self.replay(session, load=True)
        for _ in range(2):
            self.assertEqual("replacement.order", session.state.pending_decision.kind)
            option = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
            self._accept(session, replacement=option)
        self.assertEqual("exile", session.state.cards[artifact.object_id].zone)
        self.assertEqual("stack", session.state.cards[target.object_id].zone)
        self.assertEqual(1, sum(event.code == "cost.sacrifice" and event.details.get("object") == artifact.ref
                                for event in session.state.events))
        self.assertEqual("replacement.order", self.resolve(session).kind)
        self.assertEqual(("pilot:C",), tuple(session.pending_principals()))
        option = session.packet("pilot:C", full=True)["decision"]["ctx"]["options"][0]["id"]
        selected = session.act("pilot:C", {"action_id": "choose", "replacement": option})
        self.assertTrue(selected.ok, selected.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[victim.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)


CONDUIT = "Conduit of Worlds"
CONDUIT_PROFILE = "commander_review"


class FirstPaidGraveyardCastTests(InheritedNativeFixture, unittest.TestCase):
    _choice = TargetedGraveyardCastRuntimeTests._choice
    _accept = TargetedGraveyardCastRuntimeTests._accept

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "first-paid-graveyard-cast.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/first-paid-graveyard-cast-cards.json",
            ROOT / "tests/fixtures/opponent-spell-or-permanent-return-cards.json",
            ROOT / "tests/fixtures/ability-source-counter-cards.json",
            ROOT / "tests/fixtures/activation-restriction-material-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("First paid graveyard cast", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def offer(self, game, source, actor="A"):
        self.priority(game, actor)
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"].startswith(f"activate:{source.ref}:")), None)

    def activate(self, game, source, target):
        offer = self.offer(game, source)
        self.assertIsNotNone(offer)
        command = {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        return game.state.stack[-1]

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def actions(self, game, actor="A"):
        self.priority(game, actor)
        return game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]

    def restrictions(self, game, actor="A"):
        from quorune.rules.temporary_cast_restrictions import active_temporary_cast_restrictions

        return active_temporary_cast_restrictions(game.engine, actor)

    def test_original_requires_its_exact_sorcery_timing_target_and_history_scope(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
        from quorune.targeted_graveyard_cast_model import (
            FIRST_PAID_GRAVEYARD_CAST_CAPABILITY, FIRST_PAID_GRAVEYARD_CAST_FIELD,
        )

        game = self.session(57300)
        source = self.add(game.engine, CONDUIT)
        self.assert_closed(game, source)
        record = self.db.lookup(CONDUIT, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=CONDUIT_PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        activation, = (node for node in ir.faces[0].nodes if node.event == "activate")
        self.assertIn(FIRST_PAID_GRAVEYARD_CAST_CAPABILITY, activation.capability_dependencies)
        self.assertIn(PUBLIC_HISTORY_QUERY_CAPABILITY, activation.capability_dependencies)
        self.assertEqual(["land"], activation.target_schema["types_none"])
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if row.event == "activate")
        for changed in (
            replace(program, effects=[{**program.effects[0], FIRST_PAID_GRAVEYARD_CAST_FIELD: True}]),
            replace(program, target_schema={**program.target_schema, "owner": "opponent"}),
        ):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                           profile=CONDUIT_PROFILE)["strict"])
        remaining = tuple(dep for dep in program.capability_dependencies if dep != PUBLIC_HISTORY_QUERY_CAPABILITY)
        closure = self.registry.closure(remaining, profile=CONDUIT_PROFILE).to_dict()
        invalid = replace(program, capability_dependencies=remaining, capability_closure=closure,
                          provenance={**program.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_HISTORY_QUERY_CAPABILITY,
                      bind_semantic_program_runtime(invalid, capability_registry=self.registry,
                                                    profile=CONDUIT_PROFILE)["blockers"])
        for text in (
            record.oracle_text.replace(" Activate only as a sorcery.", ""),
            record.oracle_text.replace("nonland permanent", "permanent"),
            record.oracle_text.replace("If you do, you can't cast additional spells this turn. ", ""),
        ):
            changed = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                          capability_profile=CONDUIT_PROFILE)
            self.assertNotEqual("exact", changed.status)
        with patch("quorune.compiler.loot_transform_graveyard_templates.first_paid_graveyard_source_ability_is_closed",
                   return_value=False):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry,
                                                              capability_profile=CONDUIT_PROFILE).status)

    def test_actual_cast_pays_costs_locks_only_controller_and_leaves_land_play(self):
        game = self.session(57310)
        source = self.add(game.engine, CONDUIT)
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        foreign = self.add(game.engine, "Grizzly Bears", seat="D", zone="graveyard", ref="foreign-bear")
        land = self.add(game.engine, "Plains", zone="graveyard", ref="own-graveyard-land")
        other_land = self.add(game.engine, "Plains", zone="graveyard", ref="second-graveyard-land")
        instant = self.add(game.engine, "Unsummon", zone="graveyard", ref="invalid-instant-target")
        followup = self.add(game.engine, "Unsummon", zone="hand", ref="conduit-followup-spell")
        body = self.add(game.engine, "Grizzly Bears", seat="C", ref="native-bounce-target")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.add(game.engine, "Island")
        self.add(game.engine, "Island", seat="B", ref="counter-island-one")
        self.add(game.engine, "Island", seat="B", ref="counter-island-two")
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            if object_id != followup.object_id:
                game.engine.move_card(object_id, "library", log=False)
        self.prepare(game, mana={"G": 1, "C": 1})
        self.checkpoint(game)
        offer = self.offer(game, source)
        self.assertEqual([target.ref], offer["target_schema"]["legal_refs"])
        for invalid in (foreign, land, instant):
            self.reject(game, "A", {"action_id": offer["id"], "targets": [invalid.ref], "pay": "auto"})
        ability = self.activate(game, source, target)
        options = self._choice(game)
        self.assertEqual(1, len(options))
        self.assertEqual(1, options[0]["requirements"]["G"])
        self.assertEqual(1, options[0]["requirements"]["GENERIC"])
        command = {"action_id": "choose", "choice": "cast", "cast": {"cost_option": options[0]["id"]}}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "cast": {**command["cast"], "card": foreign.ref}})
        self.assertFalse(self.restrictions(game))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        spell, = game.state.stack
        self.assertEqual(("spell", target.object_id, "A"), (spell.kind, spell.card_object_id, spell.controller))
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        restriction, = self.restrictions(game)
        self.assertEqual((ability.ref, "A"), (restriction.source_id, restriction.target_player))
        self.assertFalse(self.restrictions(game, "B"))
        self.cast(game, "B", counter, [spell.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertNotIn("cast:" + followup.ref, {row["id"] for row in self.actions(game)})
        self.reject(game, "A", {"action_id": "cast:" + followup.ref, "targets": [body.ref], "pay": "auto"})
        played = next(row for row in self.actions(game) if row.get("card") == land.ref and row["action"] == "play_land")
        self.assertTrue(game.act("pilot:A", {"action_id": played["id"]}).ok)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.assertFalse(any(row.get("card") == other_land.ref and row["action"] == "play_land" for row in self.actions(game)))
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertFalse(self.restrictions(game))
        self.cast(game, "A", followup, [body.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[body.object_id].zone)
        self.replay(game, load=True)

    def test_actual_decline_and_unpayable_cast_leave_no_permission_or_spell_lock(self):
        for payable in (False, True):
            with self.subTest(payable=payable):
                game = self.session(57320 + payable)
                source = self.add(game.engine, CONDUIT)
                target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
                later = self.add(game.engine, "Grizzly Bears", zone="hand", ref="normal-hand-cast")
                self.prepare(game, mana={"G": 1, "C": 1} if payable else {})
                self.checkpoint(game)
                self.activate(game, source, target)
                if payable:
                    self._choice(game)
                    game = self.replay(game, load=True)
                    self._accept(game, choice="decline")
                else:
                    self.assertIsNone(self.resolve(game))
                self.assertFalse(self.restrictions(game))
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.actions(game)
                self.reject(game, "A", {"action_id": "cast:" + target.ref, "from": "graveyard", "pay": "auto"})
                if payable:
                    self.cast(game, "A", later)
                    self.resolve_top(game)
                    self.assertEqual("battlefield", game.state.cards[later.object_id].zone)
                self.replay(game, load=True)

    def test_actual_cast_history_rechecks_after_a_prior_counter_or_a_response(self):
        for prior in (False, True):
            with self.subTest(prior=prior):
                game = self.session(57330 + prior)
                source = self.add(game.engine, CONDUIT)
                target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
                body = self.add(game.engine, "Grizzly Bears", seat="C", ref="history-bounce-target")
                earlier = self.add(game.engine, "Unsummon", zone="hand")
                counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
                self.prepare(game, mana={"G": 1, "C": 1, "U": 1})
                game.state.players["B"].mana_pool["U"] = 2
                self.checkpoint(game)
                if not prior:
                    self.activate(game, source, target)
                self.cast(game, "A", earlier, [body.ref])
                spell = game.state.stack[-1]
                self.cast(game, "B", counter, [spell.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[earlier.object_id].zone)
                if prior:
                    self.activate(game, source, target)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(self.restrictions(game))
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.assertEqual(1, len([event for event in game.engine._current_turn_history("spell_cast") if event.actor == "A"]))
                self.replay(game, load=True)

    def test_actual_sorcery_timing_rejects_another_turn_or_nonempty_stack(self):
        for active in ("A", "B"):
            with self.subTest(active=active):
                game = self.session(57340 + (active == "B"))
                source = self.add(game.engine, CONDUIT)
                target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
                body = self.add(game.engine, "Grizzly Bears", seat="C", ref="timing-bounce-target")
                response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game, active=active)
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                ability, = game.engine._activated_abilities(game.state.cards[source.object_id])
                if active == "A":
                    self.assertIsNotNone(self.offer(game, source))
                    self.cast(game, "B", response, [body.ref])
                self.assertIsNone(self.offer(game, source))
                self.reject(game, "A", {"action_id": f"activate:{source.ref}:{ability.ability_id}",
                                        "targets": [target.ref], "pay": "auto"})
                self.assertFalse(game.state.cards[source.object_id].tapped)
                self.replay(game, load=True)
