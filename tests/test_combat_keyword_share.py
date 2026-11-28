"""Exact scheduled keyword sharing, resolution snapshots and saved replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.rules.combat_keyword_share import CAPABILITY, CONDITION, VERSION, combat_keyword_share_effect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Odric, Lunarch Marshal"
PROFILE = "commander_review"


class CombatKeywordShareTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        supports = {
            "activation-public-state-cards.json": {"Isamaru, Hound of Konda", "Grizzly Bears"},
            "ability-label-composition-cards.json": {"Plains", "Island", "Unsummon"},
            "activated-self-transform-cards.json": {"Swamp"},
            "qualified-hexproof-cards.json": {"Knight of Grace", "Knight of Malice", "Murder"},
            "opponent-keyword-prohibition-cards.json": {"Leap"},
            "graveyard-mixed-target-cards.json": {"Spectral Sailor"},
        }
        fixture_paths = [fixtures / "combat-keyword-share-cards.json"]
        for filename, names in supports.items():
            data = json.loads((fixtures / filename).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            data["rulings"] = []
            prepared = root / filename
            prepared.write_text(json.dumps(data), encoding="utf-8")
            fixture_paths.append(prepared)
        db_path = root / "combat-keyword-share.sqlite3"
        build_fixture_database(fixture_paths, db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Combat keyword share",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def test_closed_schedule_and_result_reject_mutations_and_withdrawn_authority(self):
        record = self.db.lookup(CARD, fuzzy=False)
        compiler_options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(record, **compiler_options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(CONDITION, FrozenMap(node.event_condition))
        self.assertEqual((combat_keyword_share_effect(),), tuple(node.effects))
        for old, new in (("each combat", "combat on your turn"), ("first strike", "menace"),
                         ("and vigilance.", "and ward."), ("until end of turn", "until your next turn")):
            with self.subTest(fragment=old):
                mutant = replace(record, oracle_text=record.oracle_text.replace(old, new, 1))
                self.assertNotEqual("exact", compile_oracle_card(mutant, **compiler_options).status)
        game = self.session(8440400)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = (item for item in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if any(VERSION in effect for effect in item.effects))
        self.assertIn("target.protection.hexproof_qualified", program.capability_dependencies)
        for change in (
            {"event_condition": {"all": [{"field": "step", "op": "eq", "value": "end_step"}]}},
            {"effects": (FrozenMap({**combat_keyword_share_effect(), VERSION: True}),)},
        ):
            with self.subTest(field=tuple(change)):
                result = bind_semantic_program_runtime(replace(program, **change), capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
        without = replace(program, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(without, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def keywords(self, game, card):
        return set(game.engine._combat_keywords(game.state.cards[card.object_id]))

    def start_queued_combat(self, game, source):
        self.until(game, lambda item: item.state.step == "beginning_combat" and self.trigger_count(item, source) == 1)
        self.assertEqual("B", game.state.active_player)
        self.assertEqual("A", game.state.stack[-1].controller)

    def test_opponent_combat_snapshots_live_grants_and_exact_variants_then_expires(self):
        game = self.session(8440401)
        source = self.add(game.engine, CARD)
        knight = self.add(game.engine, "Knight of Grace")
        recipient = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Knight of Malice", seat="B")
        leap = self.add(game.engine, "Leap", zone="hand")
        late = self.add(game.engine, "Spectral Sailor", zone="hand")
        removal = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        for idx in range(2):
            self.add(game.engine, "Island", ref=f"odric-island-{idx}")
        self.add(game.engine, "Island", seat="C")
        for idx in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"odric-swamp-{idx}")
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.start_queued_combat(game, source)
        self.assertEqual(set(), self.keywords(game, recipient))
        foreign_keywords = self.keywords(game, foreign)
        self.assertIn("hexproof from white", foreign_keywords)
        self.cast(game, "A", leap, [recipient.ref])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual({"flying"}, self.keywords(game, recipient))
        self.resolve_top(game)
        shared = {"first strike", "flying", "hexproof from black"}
        self.assertEqual(shared, self.keywords(game, source))
        self.assertEqual(shared, self.keywords(game, recipient))
        self.assertEqual(foreign_keywords, self.keywords(game, foreign))
        for principal in ("pilot:C", "spectator"):
            view = json.dumps(game.packet(principal, full=True))
            self.assertNotIn(late.ref, view)
            self.assertNotIn(murder.ref, view)
        self.priority(game, "B")
        hash_before = authoritative_state_hash(game.state)
        result = game.act("pilot:B", {"action_id": "cast:" + murder.ref, "targets": [recipient.ref], "pay": "auto"})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(hash_before, authoritative_state_hash(game.state))
        self.cast(game, "A", late)
        self.resolve_top(game)
        self.assertEqual({"flash", "flying"}, self.keywords(game, late))
        self.cast(game, "C", removal, [knight.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[knight.object_id].zone)
        self.assertEqual(shared, self.keywords(game, recipient))
        current_turn = game.state.turn_sequence
        self.finish_turn(game)
        self.assertGreater(game.state.turn_sequence, current_turn)
        self.assertEqual(set(), self.keywords(game, recipient))
        self.assertEqual({"flash", "flying"}, self.keywords(game, late))
        self.replay(game, load=True)

    def test_empty_controlled_keywords_still_trigger_and_only_current_donors_count(self):
        game = self.session(8440402)
        source = self.add(game.engine, CARD)
        recipient = self.add(game.engine, "Grizzly Bears")
        flying = self.add(game.engine, "Spectral Sailor", seat="B")
        donor = self.add(game.engine, "Knight of Grace")
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.start_queued_combat(game, source)
        self.cast(game, "B", removal, [donor.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[donor.object_id].zone)
        self.assertEqual(set(), self.keywords(game, recipient))
        self.assertEqual({"flash", "flying"}, self.keywords(game, flying))
        self.replay(game, load=True)
