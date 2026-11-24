"""CR 120.4 and 608.2c: the committed damage amount determines life gained."""

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
from quorune.fixed_damage_set_model import DEALT_LIFE_GAIN_CAPABILITY as CAPABILITY, DEALT_LIFE_GAIN_FIELD
from quorune.model import TurnEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAMES = ("Creeping Bloodsucker", "Syphon Soul")
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class FixedDamageDealtLifeGainTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "fixed-damage-dealt-life.sqlite3"
        build_fixture_database([basic, FIXTURES / "fixed-damage-dealt-life-gain-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed dealt-damage life", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies)
        return card, program

    def test_complete_originals_own_one_fixed_noncontroller_batch_and_life_result(self):
        for i, name in enumerate(NAMES):
            with self.subTest(name=name):
                card, program = self.program(name)
                self.assertIsNone(program.target_schema)
                self.assertEqual("step.begin" if i == 0 else "resolve", program.event)
                self.assertEqual(({
                    "op": "damage_fixed_set", "source": "$source", "amount": i + 1,
                    "groups": [{"kind": "players", "relation": "opponents"}], DEALT_LIFE_GAIN_FIELD: 1,
                },), tuple(program.effects))
                self.assertIn("life.change.effect", program.capability_dependencies)
                self.assertEqual("exact", compile_oracle_card(
                    card, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        card, _ = self.program(NAMES[1])
        for text in (
            card.oracle_text.replace("other player", "player"),
            card.oracle_text.replace("damage dealt this way", "damage prevented this way"),
            card.oracle_text.replace("other player", "target player"),
        ):
            with self.subTest(text=text):
                value = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in value.faces for n in f.nodes))

        from quorune.rules.effect_shape_walkers import nested_effects

        combined = compile_oracle_card(replace(card, oracle_text=card.oracle_text + " Draw a card."),
                                       capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(
            any(effect["op"] == "draw" for face in combined.faces for node in face.nodes
                for effect in nested_effects(node.effects))
            or any("Draw a card." in item.text for item in combined.material_residuals)
        )

    def test_raw_result_cannot_widen_batch_or_omit_its_independently_required_owner(self):
        _, program = self.program(NAMES[1])
        effect, = program.effects
        for changed in (
            replace(program, active_zone="graveyard"),
            replace(program, effects=({**effect, DEALT_LIFE_GAIN_FIELD: True},)),
            replace(program, effects=({**effect, "groups": [{"kind": "players", "relation": "all"}]},)),
            replace(program, effects=({**effect, "amount": 0},)),
            replace(program, effects=(effect, {"op": "life", "player": "$controller", "delta": 2})),
        ):
            with self.subTest(changed=changed.to_dict()):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        dependencies = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def assert_life(self, game, life):
        self.assertEqual(life, [game.state.players[seat].life for seat in "ABCD"])

    def test_actual_syphon_gains_committed_damage_after_choice_and_redirect(self):
        game = self.session(120406041)
        spell = self.add(game.engine, NAMES[1], zone="hand")
        palm = self.add(game.engine, "Deflecting Palm", seat="B", zone="hand")
        passage = self.add(game.engine, "Safe Passage", seat="B", zone="hand")
        angel = self.add(game.engine, "Empyrial Archangel", seat="C")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="damage-result-private")
        for card in (spell, palm, passage, angel):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 1})
        game.state.players["B"].mana_pool.update({"C": 2, "W": 2, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        damage_source = game.state.stack[-1].ref
        self.cast_for(game, "B", palm)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, list(pending.actors)))
        chosen = game.act("pilot:B", {"action_id": "choose", "source": damage_source})
        self.assertTrue(chosen.ok, chosen.summary)
        self.cast_for(game, "B", passage)
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, list(pending.actors)))
        self.assert_life(game, [40, 40, 40, 40])
        self.assertEqual(0, game.state.cards[angel.object_id].marked_damage)
        game = self.replay(game, load=True)
        ctx = game.packet("pilot:B", full=True)["decision"]["ctx"]
        palm_option, = [row for row in ctx["options"] if "deflecting palm" in str(row).casefold()]
        command = {"action_id": "choose", "replacement": palm_option["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assert_life(game, [42, 40, 40, 38])
        self.assertEqual(2, game.state.cards[angel.object_id].marked_damage)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_upkeep_keeps_departed_source_and_current_opponent_set(self):
        game = self.session(120406042)
        source = self.add(game.engine, NAMES[0])
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, source)
        self.prepare(game)
        game.engine._begin_turn(TurnEntry(turn_id="dealt-life-upkeep", player="A",
                                          created_sequence=game.state.turn_sequence))
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.engine.pump()
        self.assertEqual("upkeep", game.state.step)
        self.assertEqual(1, self.trigger_count(game, source))
        self.checkpoint(game)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.priority(game, "D")
        departed = game.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn("D", game.engine.active_seats)
        self.assertEqual([42, 39, 39], [game.state.players[seat].life for seat in "ABC"])
        self.replay(game)

    def test_actual_syphon_witness_kills_printed_total_mutation(self):
        from quorune.rules import committed_quantity_followups

        bind = committed_quantity_followups.quantity_followup_from_count

        def printed(effect, count):
            return bind(effect, 6 if effect.get("op") == "life" else count)

        with patch.object(committed_quantity_followups, "quantity_followup_from_count", side_effect=printed):
            with self.assertRaises(AssertionError):
                self.test_actual_syphon_gains_committed_damage_after_choice_and_redirect()

    def test_verified_withdrawal_keeps_independent_damage_and_life_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Actual dealt damage life authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in (*NAMES, "Pestilence Demon", "Soul's Grace"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name not in NAMES, result["strict_capability_ready"], result["blockers"])
