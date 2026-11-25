"""Existing attack requirements can bind a qualified target or an enchanted creature."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachment_references import ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceKind, AttachmentReferenceSpec
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.temporary_attack_requirement_model import TEMPORARY_ATTACK_REQUIREMENT_KIND, TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SINGER = "Shipwreck Singer"
CURSE = "Nettling Curse"
CAPABILITY = TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


def _attack_requirement(program):
    return any(effect.get("restriction", {}).get("kind") == TEMPORARY_ATTACK_REQUIREMENT_KIND
               for effect in program.effects if isinstance(effect.get("restriction"), dict))


class QualifiedAttackRequirementResolutionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "qualified-attack-requirements.sqlite3"
        build_fixture_database([basic, FIXTURES / "qualified-attack-requirement-resolution-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Qualified resolved attack requirements", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        record = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        requirement, = [part for part in programs if CAPABILITY in part.capability_dependencies]
        return record, programs, requirement

    def test_complete_originals_keep_their_independent_siblings_and_subject_forms(self):
        singer, programs, target = self.program(SINGER)
        self.assertEqual(2, sum(part.event == "activate" for part in programs))
        self.assertTrue(any("combat.block.flying" in part.capability_dependencies for part in programs))
        self.assertEqual("opponent", target.target_schema["controller_relation"])
        self.assertEqual(2, target.effects[0]["restriction"]["schema_version"])
        curse, programs, attaching = self.program(CURSE)
        self.assertTrue(any("attachment.aura.simple_object" in part.capability_dependencies for part in programs))
        self.assertTrue(any("life.change.effect" in part.capability_dependencies for part in programs))
        self.assertIsNone(attaching.target_schema)
        self.assertEqual(3, attaching.effects[0]["restriction"]["schema_version"])
        self.assertEqual(AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2).to_dict(),
                         attaching.effects[0]["card"])
        for record, source, replacement in (
            (singer, "an opponent controls", "you own"),
            (curse, "Enchanted creature attacks this turn if able", "Equipped creature attacks this turn if able"),
        ):
            changed = replace(record, oracle_text=record.oracle_text.replace(source, replacement))
            compilation = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in compilation.faces for node in face.nodes))

    def test_raw_attack_requirements_keep_the_original_subject_and_independent_authority(self):
        _, _, target = self.program(SINGER)
        _, _, attaching = self.program(CURSE)
        naked = {key: value for key, value in target.target_schema.items() if key != "controller_relation"}
        payload, = attaching.effects
        with self.assertRaisesRegex(ValueError, "Runtime handler event must match"):
            replace(attaching, event="resolve")
        for candidate in (
            replace(target, target_schema=naked),
            replace(attaching, effects=({**payload, "card": "$source.zone_object"},)),
        ):
            binding = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)
        for program, capability in ((target, CAPABILITY), (attaching, ATTACHMENT_INFORMATION_CAPABILITY)):
            caps = [value for value in program.capability_dependencies if value != capability]
            candidate = replace(program, capability_dependencies=caps,
                                capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"], bound)
            self.assertIn("capability:undeclared_runtime_dependency:" + capability, bound["blockers"])

    def activation_offer(self, game, source, *, requirement=True):
        self.priority(game, "A")
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id, event="activate")
        exact, = [program for program in programs if _attack_requirement(program) is requirement]
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in offers if row["id"] == f'activate:{source.ref}:{exact.ability_id.removeprefix("ability:")}')

    def activate(self, game, source, *, targets=(), reserve=None, requirement=True):
        offer = self.activation_offer(game, source, requirement=requirement)
        answer = game.act("pilot:A", {"action_id": offer["id"], "targets": list(targets),
                                      "pay": "auto", "reserve": reserve or {}})
        self.assertTrue(answer.ok, answer.summary)

    def require_attack(self, game, subject, other):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        before = authoritative_state_hash(game.state)
        ignored = game.act("pilot:B", {"action_id": "attack", "attackers": {other.ref: "C"}})
        self.assertFalse(ignored.ok, "Required attacker cannot be omitted")
        self.assertEqual(before, authoritative_state_hash(game.state))
        selected = game.act("pilot:B", {"action_id": "attack", "attackers": {subject.ref: "C"}})
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual("C", game.state.combat.attackers[subject.object_id])

    def test_actual_singer_requires_only_the_opponent_target_and_keeps_its_separate_reduction(self):
        game = self.session(508010701)
        engine = game.engine
        singer = self.add(engine, SINGER)
        own = self.add(engine, "Wind Drake")
        subject = self.add(engine, "Grizzly Bears", seat="B", ref="singer-required-creature")
        other = self.add(engine, "Grizzly Bears", seat="B", ref="singer-optional-creature")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="singer-unrevealed")
        for card in (singer, subject, other):
            card.acquired_control_turn_count = -1
        for index in range(2):
            self.add(engine, "Swamp", ref=f"singer-weakening-mana-{index}")
        self.assert_closed(game, singer)
        self.prepare(game, actor="A", active="B", mana={"C": 1, "U": 1})
        self.checkpoint(game)
        offer = self.activation_offer(game, singer)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": offer["id"], "targets": [own.ref], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.activate(game, singer, targets=[subject.ref])
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)
        self.require_attack(game, subject, other)
        self.activate(game, singer, requirement=False)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.engine._numeric_stat(subject.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(other.object_id, "power"))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_singer_target_that_becomes_yours_does_not_get_the_requirement(self):
        game = self.session(608020271)
        engine = game.engine
        singer = self.add(engine, SINGER)
        subject = self.add(engine, "Grizzly Bears", seat="B")
        seize = self.add(engine, "Word of Seizing", zone="hand")
        self.prepare(game, mana={"C": 4, "U": 1, "R": 2})
        self.checkpoint(game)
        self.activate(game, singer, targets=[subject.ref], reserve={"C": 3, "R": 2})
        self.cast_for(game, "A", seize, [subject.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[subject.object_id].controller)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        accepted = game.act("pilot:A", {"action_id": "attack", "attackers": {}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(game, load=True)

    def test_actual_curse_applies_current_or_departed_attachment_and_keeps_life_loss_sibling(self):
        for departure in (False, True):
            with self.subTest(departure=departure):
                game = self.session(303040701 + departure)
                engine = game.engine
                curse = self.add(engine, CURSE, zone="hand")
                self.add(engine, "Vedalken Orrery")
                subject = self.add(engine, "Grizzly Bears", seat="B", ref="curse-required-creature")
                other = self.add(engine, "Grizzly Bears", seat="B", ref="curse-optional-creature")
                for card in (subject, other):
                    card.acquired_control_turn_count = -1
                remove = self.add(engine, "Disenchant", zone="hand") if departure else None
                self.assert_closed(game, curse)
                self.prepare(game, actor="A", active="B", mana={"C": 4, "B": 1, "R": 1, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", curse, [subject.ref], reserve={"C": 2, "R": 1, "W": 1})
                self.assertIsNone(self.resolve(game))
                self.assertEqual(subject.object_id, game.state.cards[curse.object_id].attached_to)
                self.activate(game, curse, reserve={"C": 1, "W": 1})
                if departure:
                    self.cast_for(game, "A", remove, [curse.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[curse.object_id].zone)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.require_attack(game, subject, other)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40 if departure else 37, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_actual_singer_witness_kills_omitting_the_resolved_attack_requirement(self):
        from quorune.effect_runtime.declaration_effects import HANDLERS

        with patch.dict(HANDLERS, {"grant_declaration_restriction_until_end_of_turn": lambda *args, **kwargs: []}):
            with self.assertRaisesRegex(AssertionError, "Required attacker cannot be omitted"):
                self.test_actual_singer_requires_only_the_opponent_target_and_keeps_its_separate_reduction()
