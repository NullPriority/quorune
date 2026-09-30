from __future__ import annotations

"""Independent CR 608/611/613/702.16 contracts for temporary target effects.

Choices occur on resolution; conditions read the current target only once;
layer-6 and layer-7c results lock that incarnation and expire at cleanup.
Cost-X is bound by the spell's mana cost, never inferred from an undefined X.
Protection must affect targeting, blocking, damage and attachments through DEBT.
"""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import protection_specs
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.fixed_target_effect_sequences import (
    fixed_target_characteristics_effect_template,
)
from quorune.compiler.temporary_target_interactions import (
    TEMPORARY_TARGET_INTERACTION_CAPABILITY as CAPABILITY,
    temporary_target_interaction_effect_template,
)
from quorune.continuous_effect_state import (
    create_resolution_continuous_effect, expire_end_of_turn_continuous_effects,
    resolution_effect_source,
)
from quorune.continuous_effects import ContinuousOperation, Layer
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.protection import ProtectionVerdict, protection_verdict_for_ref
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.temporary_target_interaction_shapes import (
    temporary_target_interaction_node_capabilities,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


FIXTURE_PATH = ROOT / "tests/fixtures/temporary-target-interaction-cards.json"
REGISTRY_PATH = ROOT / "quorune/rules/capability-registry.json"


def record(text: str, *, type_line="Instant", mana_cost="{G}") -> CardRecord:
    return CardRecord(
        oracle_id="fixture:temporary-target-compiler", name="Generic Compiler Fixture",
        mana_cost=mana_cost, mana_value=1, type_line=type_line, oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="6" if "Creature" in type_line else None,
        loyalty=None, defense=None, colors=("G",), color_identity=("G",),
        keywords=(), produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class TemporaryTargetInteractionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, *, type_line="Instant", mana_cost="{G}", registry=None):
        return compile_oracle_card(
            record(text, type_line=type_line, mana_cost=mana_cost),
            capability_registry=registry or self.registry,
            capability_profile="commander_review",
        )

    def test_temporary_interactions_compile_across_contexts(self):
        bodies = (
            "Target creature gets +1/+1 and gains flying, first strike, and trample until end of turn.",
            "Target creature gains horsemanship until end of turn.",
            "Target creature gains protection from artifacts until end of turn.",
            "Target creature you control gains protection from the color of your choice until end of turn.",
            "Target creature gains protection from the color of its controller's choice until end of turn.",
            "Target creature gets +1/+1 and gains your choice of deathtouch or lifelink until end of turn.",
            "Target creature gets +2/+2 until end of turn. If it has a counter on it, it also gains flying and lifelink until end of turn.",
            "Target creature gets +2/+2 until end of turn. If it's an enchanted creature or enchantment creature, it also gains hexproof and indestructible until end of turn.",
            *("Target creature gets +2/+2 until end of turn. If " + condition + ", it also gains trample until end of turn."
              for condition in ("it's legendary", "it's an artifact creature", "it's a Goblin or Orc", "it's a Vampire", "it's a Spirit")),
            "Target creature gets +2/+0 until end of turn. Regenerate it.",
            "Another target creature you control with power 2 or less gains lifelink until end of turn and can't be blocked this turn.",
            *(f"Target creature gains {land}walk until end of turn."
              for land in ("plains", "island", "swamp", "mountain", "forest")),
        )
        for body in bodies:
            for text, type_line in (
                (body, "Instant"),
                ("{1}, {T}: " + body, "Artifact"),
                ("When this creature enters, " + body, "Creature — Test"),
                ("Choose one —\n• " + body + "\n• Draw a card.", "Sorcery"),
            ):
                with self.subTest(text=text):
                    ir = self.compile(text, type_line=type_line)
                    self.assertEqual("exact", ir.status, ir.material_residuals)
                    nodes = [n for f in ir.faces for n in f.nodes]
                    self.assertTrue(any(CAPABILITY in n.capability_dependencies for n in nodes))
                    self.assertTrue(all(n.text == text[n.span.start:n.span.end] for n in nodes))
        for power, toughness in (("+X", "+0"), ("-X", "-X"), ("+X", "+X")):
            text = f"Target creature gets {power}/{toughness} until end of turn."
            self.assertIsNone(fixed_target_characteristics_effect_template(text))
            ir = self.compile(text, mana_cost="{X}{G}")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertIn(CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)

    def test_temporary_interaction_exclusions_and_shape_mutations(self):
        excluded = (
            "Target creature gets +X/+X until end of turn, where X is its power.",
            "Target creature gains protection from the card type of your choice until end of turn.",
            "Target creature gains your choice of flying or banding until end of turn.",
            "Target creature gets +2/+2 until end of turn. If you attacked this turn, it also gains trample until end of turn.",
            "Target creature gets +2/+2 until end of turn and must be blocked this turn if able.",
            "Target creature gains forestwalk until your next turn.",
            "Target creature gains forestwalk until end of turn. Sacrifice it.",
            "Target creature gains flying,, trample until end of turn.",
            "Target creature gets +X/+X until end of turn.",
            "{1}: Target creature gets +X/+X until end of turn.",
            "{X}: Target creature gets +X/+X until end of turn.",
            "When this creature enters, target creature gets +X/+X until end of turn.",
        )
        for text in excluded:
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Artifact" if ":" in text else "Creature — Test" if text.startswith("When") else "Instant")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
        template = temporary_target_interaction_effect_template(
            "Target creature you control gains protection from the color of your choice until end of turn."
        )
        self.assertIsNotNone(template)
        _, effects, schema, mechanics = template.compiled()
        self.assertIn(CAPABILITY, temporary_target_interaction_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        mutants = []
        wrong_color = deepcopy(effects[0])
        wrong_color["then_by_choice"]["R"][0]["ability_fragment"]["value"]["quality"] = "G"
        mutants.append(wrong_color)
        unknown_branch = deepcopy(effects[0])
        unknown_branch["then_by_choice"]["R"][0]["card"] = "$source"
        mutants.append(unknown_branch)
        mutants.append({**effects[0], "player": "$owner"})
        for mutant in mutants:
            self.assertEqual((), temporary_target_interaction_node_capabilities(
                effects=(mutant,), target_schema=schema, mechanic_ids=mechanics,
            ))
        for value in (True, 1.5, [], {}, "$unknown"):
            self.assertEqual((), temporary_target_interaction_node_capabilities(
                effects=({"op": "modify_stats_until_end_of_turn", "card": "$target.0", "power": value, "toughness": 1},),
                target_schema=schema, mechanic_ids=mechanics,
            ))

    def test_temporary_interaction_dependencies_and_compiler_mutation(self):
        text = "Target creature gains protection from artifacts until end of turn."
        self.assertEqual("exact", self.compile(text).status)
        for capability in (CAPABILITY, "protection.typed.debt", "target.revalidate_resolution"):
            raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            row = next(r for r in raw["capabilities"] if r["id"] == capability)
            row.update(status="blocked", blockers=["focused dependency mutation"])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            self.assertNotEqual("exact", self.compile(text, registry=registry).status)
        with patch("quorune.compiler.resolution_effect_templates.temporary_target_interaction_effect_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)


class TemporaryTargetInteractionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-target.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/scryfall-exact-lists.json", FIXTURE_PATH], path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / "examples/mishra-eminent-one.txt", commander="Mishra, Eminent One")
        cls.zimone = loader.load(ROOT / "examples/zimone-and-dina.txt", commander="Zimone and Dina")
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=seed)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        records = tuple(self.db.by_oracle_id(row["oracle_id"]) for row in json.loads(FIXTURE_PATH.read_text())["cards"] if row["oracle_text"])
        register_generated_programs(
            self.db, engine.semantics, records, trust_level="trusted", capability_registry=self.registry,
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        for row in records:
            self.assertTrue(all(engine.semantic_program_is_current_trusted(p)
                                for p in engine.semantics.programs_for_oracle(row.oracle_id)))
        return session

    def add(self, engine, name, *, seat="A", zone="battlefield", ref=None):
        row = self.db.lookup(name)
        identity = ref or name.lower().replace(" ", "-")
        card = CardInstance(
            object_id="fixture:" + identity, ref=identity, oracle_id=row.oracle_id,
            printed_name=row.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats) if zone == "battlefield" else [seat],
            revealed_to=list(engine.seats) if zone == "battlefield" else [seat],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self, session, card, *, mana=None):
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        if mana:
            engine.state.players["A"].mana_pool.update(mana)
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(a for a in actions if a.get("card") == card.ref or a["id"].startswith("activate:" + card.ref + ":"))

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

    def resolve(self, session):
        for _ in range(48):
            decision = session.state.pending_decision
            if decision and decision.kind != "priority":
                return decision
            if not session.state.stack:
                return None
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Temporary effect did not finish resolution")

    def replay(self, session, *, load_pending=False):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temporary-target-record"
            session.save(path)
            replay = replay_record(path, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(expected, replay["final_state_hash"])
            if load_pending:
                loaded = CommanderSession.load(self.db, path)
                self.assertEqual(expected, authoritative_state_hash(loaded.state))
                return loaded

    def cast(self, session, source, target, *, mana, x=None, checkpoint=False):
        action = self.ready(session, source, mana=mana)
        if checkpoint:
            self.checkpoint(session)
        response = {"action_id": action["id"], "targets": [target.ref], "pay": "auto"}
        if x is not None:
            response["x"] = x
        result = session.act("pilot:A", response)
        self.assertTrue(result.ok, result.summary)
        return result

    def test_trusted_x_cast_uses_selected_cost_and_expires(self):
        session = self.session(227610)
        engine = session.engine
        target = self.add(engine, "Generic Temporary Red Body")
        source = self.add(engine, "Generic Temporary X Growth", zone="hand")
        action = self.ready(session, source, mana={"G": 1, "C": 3})
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "x": 3, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, engine.state.stack[-1].x_value)
        self.resolve(session)
        self.assertEqual(5, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(9, engine._numeric_stat(target.object_id, "toughness"))
        self.assertIn("hexproof", engine._combat_keywords(target))
        self.assertIn("indestructible", engine._combat_keywords(target))
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
        self.replay(session)
        self.assertGreater(expire_end_of_turn_continuous_effects(engine.state), 0)
        self.assertEqual(2, engine._numeric_stat(target.object_id, "power"))
        self.assertNotIn("hexproof", engine._combat_keywords(target))
        shrink = self.add(engine, "Generic Temporary X Shrink", zone="hand")
        self.cast(session, shrink, target, mana={"B": 1, "C": 2}, x=2, checkpoint=True)
        self.resolve(session)
        self.assertEqual(0, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(4, engine._numeric_stat(target.object_id, "toughness"))
        self.replay(session)

    def test_stale_target_and_payment_rejection_roll_back(self):
        session = self.session(227611)
        engine = session.engine
        target = self.add(engine, "Generic Temporary Red Body")
        enemy = self.add(engine, "Generic Temporary Red Body", seat="B", ref="enemy")
        source = self.add(engine, "Generic Temporary X Growth", zone="hand")
        action = self.ready(session, source, mana={"G": 1, "C": 1})
        for targets, x in (([enemy.ref], 1), ([target.ref], 3)):
            before = authoritative_state_hash(engine.state)
            rejected = session.act("pilot:A", {"action_id": action["id"], "targets": targets, "x": x, "pay": "auto"})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "x": 1, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        engine.move_card(target.object_id, "exile", log=False)
        engine.move_card(target.object_id, "battlefield", controller="A", log=False)
        self.resolve(session)
        self.assertEqual(2, engine._numeric_stat(target.object_id, "power"))
        self.assertNotIn("hexproof", engine._combat_keywords(target))
        self.assertFalse(engine.state.continuous_effects)

    def test_temporary_protection_uses_debt_and_current_chooser(self):
        session = self.session(227612)
        engine = session.engine
        source = self.add(engine, "Generic Temporary Recipient Shield")
        target = self.add(engine, "Generic Temporary Red Body", seat="B", ref="recipient")
        blocker = self.add(engine, "Generic Temporary Red Body", seat="A", ref="red-blocker")
        action = self.ready(session, source, mana={"C": 1})
        self.checkpoint(session)
        activated = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["B"], list(pending.actors))
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        loaded = self.replay(session, load_pending=True)
        before = authoritative_state_hash(loaded.state)
        rejected = loaded.act("pilot:A", {"action_id": "choose", "choice": "R"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(loaded.state))
        chosen = loaded.act("pilot:B", {"action_id": "choose", "choice": "R"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.resolve(loaded)
        loaded_target = loaded.state.cards[target.object_id]
        loaded_blocker = loaded.state.cards[blocker.object_id]
        self.assertEqual("R", protection_specs(loaded.engine._effective_ability_fragments(loaded_target))[0].quality)
        self.assertEqual(ProtectionVerdict.BLOCKED, protection_verdict_for_ref(
            loaded.engine, loaded.engine._effective_card_data(loaded_target), loaded_blocker.ref,
        ))
        self.assertFalse(loaded.engine._can_block(loaded_target, loaded_blocker)[0])
        self.replay(loaded)
        schema = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}
        self.assertNotIn(loaded_target.ref, loaded.engine._semantic_target_options("A", schema, source_ref=loaded_blocker.ref))
        loaded.engine.apply_effect({"op": "damage", "source": loaded_blocker.ref, "target": loaded_target.ref, "amount": 2}, actor="A")
        self.assertEqual(0, loaded_target.marked_damage)
        expire_end_of_turn_continuous_effects(loaded.state)
        self.assertEqual(ProtectionVerdict.ALLOWED, protection_verdict_for_ref(
            loaded.engine, loaded.engine._effective_card_data(loaded_target), loaded_blocker.ref,
        ))
        # A separate late-control-change probe checks resolution-time chooser
        # identity without claiming those diagnostic mutations are commands.
        session = loaded
        engine = loaded.engine
        source = engine.state.cards[source.object_id]
        target = loaded_target
        source.tapped = False
        action = self.ready(session, source, mana={"C": 1})
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        engine.change_control(target.object_id, "C")
        pending = self.resolve(session)
        self.assertEqual(["C"], list(pending.actors))

    def test_trusted_keyword_choice_and_unblockable_activation(self):
        session = self.session(227614)
        engine = session.engine
        target = self.add(engine, "Generic Temporary Red Body")
        choice = self.add(engine, "Generic Temporary Keyword Choice", zone="hand")
        self.cast(session, choice, target, mana={"B": 1}, checkpoint=True)
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(3, engine._numeric_stat(target.object_id, "power"))
        accepted = session.act("pilot:A", {"action_id": "choose", "choice": "Lifelink"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertIn("lifelink", engine._combat_keywords(target))
        self.assertNotIn("deathtouch", engine._combat_keywords(target))
        self.replay(session)
        expire_end_of_turn_continuous_effects(engine.state)
        mentor = self.add(engine, "Generic Temporary Crossing Mentor")
        enemy = self.add(engine, "Generic Temporary Red Body", seat="B", ref="enemy-blocker")
        action = self.ready(session, mentor, mana={"C": 1})
        legal = set(action["target_schema"]["legal_refs"])
        self.assertIn(target.ref, legal)
        self.assertNotIn(mentor.ref, legal)
        self.assertNotIn(enemy.ref, legal)
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertTrue(mentor.tapped)
        self.assertIn("lifelink", engine._combat_keywords(target))
        self.assertFalse(engine._can_block(target, enemy)[0])
        self.replay(session)
        expire_end_of_turn_continuous_effects(engine.state)
        self.assertTrue(engine._can_block(target, enemy)[0])

    def test_current_target_conditions_and_regeneration(self):
        session = self.session(227616)
        engine = session.engine
        plain = self.add(engine, "Generic Temporary Red Body")
        legend = self.add(engine, "Generic Temporary Legendary Body")
        for index, target in enumerate((plain, legend)):
            source = self.add(engine, "Generic Temporary Legendary Growth", zone="hand", ref=f"legendary-growth-{index}")
            self.cast(session, source, target, mana={"G": 1})
            self.resolve(session)
            self.assertEqual(4, engine._numeric_stat(target.object_id, "power"))
            self.assertEqual(target is legend, "trample" in engine._combat_keywords(target))
        expire_end_of_turn_continuous_effects(engine.state)
        source = self.add(engine, "Generic Temporary Legendary Growth", zone="hand", ref="late-legendary-growth")
        self.cast(session, source, legend, mana={"G": 1})
        create_resolution_continuous_effect(
            engine, source=resolution_effect_source(engine, {}, fallback_card=plain),
            targets=(legend,), layer=Layer.TYPE, sublayer="4",
            operations=(ContinuousOperation("remove_types", ["legendary"], field="supertypes"),),
        )
        self.resolve(session)
        self.assertNotIn("trample", engine._combat_keywords(legend))
        self.assertEqual(4, engine._numeric_stat(legend.object_id, "power"))
        source = self.add(engine, "Generic Temporary Counter Growth", zone="hand")
        self.cast(session, source, plain, mana={"G": 1})
        self.resolve(session)
        self.assertNotIn("flying", engine._combat_keywords(plain))
        engine.apply_effect({"op": "place_counters", "card": plain.ref, "counter": "test", "amount": 1, "source": legend.ref}, actor="A")
        source = self.add(engine, "Generic Temporary Counter Growth", zone="hand", ref="positive-counter-growth")
        self.cast(session, source, plain, mana={"G": 1})
        self.resolve(session)
        self.assertIn("flying", engine._combat_keywords(plain))
        engine.apply_effect({"op": "remove_counters", "card": plain.ref, "counter": "test", "amount": 1, "source": legend.ref}, actor="A")
        self.assertIn("flying", engine._combat_keywords(plain))
        regeneration = self.add(engine, "Generic Temporary Regeneration Growth", zone="hand")
        self.cast(session, regeneration, plain, mana={"B": 1})
        self.resolve(session)
        self.assertEqual(1, plain.regeneration_shields)
        before = engine._numeric_stat(plain.object_id, "power")
        engine.apply_effect({"op": "destroy", "card": plain.ref}, actor="A")
        self.assertEqual("battlefield", plain.zone)
        self.assertEqual(0, plain.regeneration_shields)
        self.assertTrue(plain.tapped)
        self.assertEqual(before, engine._numeric_stat(plain.object_id, "power"))


if __name__ == "__main__":
    unittest.main()
