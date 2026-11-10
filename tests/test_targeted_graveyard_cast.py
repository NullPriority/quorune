from __future__ import annotations

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_grant_riders import CAST_GRANT_RIDER_FIELD, CastGrantRiderBinding
from quorune.rules.graveyard_card_targets import targeted_graveyard_cast_runtime_dependencies
from quorune.targeted_graveyard_cast_model import GRAVEYARD_CAST_CAPABILITY
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
