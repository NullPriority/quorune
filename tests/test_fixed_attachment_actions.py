from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import (
    ProtectionQualityKind,
    ProtectionSpec,
    ability_fragment_to_dict,
)
from quorune.abilities import ActivationLimit, parse_activated_abilities
from quorune.attachments import AttachmentTransition, attach_objects
from quorune.carddb import CardRecord
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry, DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.attachment_action_capability_shapes import is_closed_fixed_source_equipment_activation_program
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "fixed-attachment-actions-cards.json"
PRINTED_FIXTURE = ROOT / "tests" / "fixtures" / "printed-source-attachment-cards.json"


class EventCounterThresholdTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "event-counter-threshold.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/attached-occurrence-cards.json",
            ROOT / "tests/fixtures/manifest-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Event counter threshold", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_attached_event_counter_threshold_is_closed_and_keeps_departed_incarnation(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.event_counter_reference import EVENT_COUNTER_CONTEXT
        from quorune.model import StackItem
        from quorune.rules.event_counter_threshold import (
            EVENT_COUNTER_THRESHOLD_CAPABILITY, EVENT_COUNTER_THRESHOLD_CONTEXT,
            event_counter_threshold_count, initial_event_counter_threshold, pin_event_counter_threshold_departures,
        )
        from quorune.semantic_choices.event_counter_threshold import EventCounterThresholdHandler
        from quorune.semantics import SemanticProgram
        from quorune.trigger_batches import create_pending_trigger_batch

        card = self.db.lookup("Ordeal of Thassa")
        options = dict(capability_registry=self.registry, capability_profile="commander_review")
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = next(node for node in ir.faces[0].nodes if node.event == "creature.attacks")
        self.assertIn(EVENT_COUNTER_THRESHOLD_CAPABILITY, node.capability_dependencies)
        self.assertEqual(("place_counters", "sacrifice_source_if_event_counter_threshold"),
                         tuple(effect["op"] for effect in node.effects))
        with mock.patch("quorune.compiler.event_counter_results.event_counter_threshold_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)
        deps = tuple(cap for cap in node.capability_dependencies if cap != EVENT_COUNTER_THRESHOLD_CAPABILITY)
        forged = SemanticProgram("fixture:event-counter-threshold", "Event threshold", effects=list(node.effects),
            event=node.event, active_zone=node.active_zone, event_condition=node.event_condition,
            capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + EVENT_COUNTER_THRESHOLD_CAPABILITY, binding["blockers"])

        card = CardInstance(object_id="attacker", ref="bear", oracle_id="test", printed_name="Bear",
                            owner="A", controller="A", zone="battlefield", counters={"+1/+1": 3})
        observation = initial_event_counter_threshold(node.effects)
        observation[EVENT_COUNTER_CONTEXT] = {"reference": card.ref, "logical_object_id": card.logical_object_id}
        item = SimpleNamespace(context=observation)
        waiting = StackItem(stack_id="pending-counter", ref="S2", kind="triggered_ability", controller="A",
                            label="Counter threshold", source_object_id="aura", semantic_key=forged.key,
                            context=dict(observation))
        batch = create_pending_trigger_batch(batch_id="pending", ref="TB1", items=(waiting.to_dict(),),
                                             apnap_order=("A", "B"), turn_sequence=1, priority_epoch=1)
        host = SimpleNamespace(state=SimpleNamespace(stack=[item], pending_trigger_batches=[batch], cards={card.object_id: card}))
        pin_event_counter_threshold_departures(host, (card,))
        self.assertEqual(3, item.context[EVENT_COUNTER_THRESHOLD_CONTEXT]["departure_count"])
        pending, = host.state.pending_trigger_batches[0].items
        self.assertEqual(3, pending.event_facts[EVENT_COUNTER_THRESHOLD_CONTEXT]["departure_count"])
        card.zone_change_counter += 1
        card.counters.clear()
        self.assertEqual(3, event_counter_threshold_count(host, item))

        resolved = {**node.effects[1], "player": "A", "source": "ordeal", "observed": 3}
        aura = SimpleNamespace(ref="ordeal", controller="A", phased_out=False, logical_object_id="aura@1")
        context = SimpleNamespace(actor="A", stack_controller="A", source_logical_object_id="aura@1",
                                  query=SimpleNamespace(object=lambda *args, **kwargs: aura))
        handler = EventCounterThresholdHandler()
        result = handler.prepare(resolved, context)
        self.assertEqual(({"op": "sacrifice", "card": "ordeal"},), result.auto_continue.prepend_effects)
        aura.controller = "B"
        self.assertFalse(handler.prepare(resolved, context).auto_continue.prepend_effects)
        aura.controller = "A"
        aura.logical_object_id = "aura@2"
        self.assertFalse(handler.prepare(resolved, context).auto_continue.prepend_effects)

    def test_original_ordeal_checks_after_replacements_and_sacrifices_only_its_original_source(self):
        from quorune.rules.event_counter_threshold import EVENT_COUNTER_THRESHOLD_CONTEXT

        for index, scenario in enumerate(("replacements", "below", "source_removed", "attacker_removed")):
            with self.subTest(scenario=scenario):
                session = self.session(608210 + index)
                aura = self.add(session.engine, "Ordeal of Thassa", zone="hand")
                attacker = self.add(session.engine, "Grizzly Bears")
                attacker.acquired_control_turn_count = -1
                attacker.counters["+1/+1"] = {"source_removed": 2, "attacker_removed": 3}.get(scenario, 1)
                if scenario == "replacements":
                    scales = self.add(session.engine, "Hardened Scales")
                    self.add(session.engine, "Branching Evolution")
                    self.add(session.engine, "Dauthi Voidwalker", seat="B", ref="ordeal-replacement-b")
                    self.add(session.engine, "Dauthi Voidwalker", seat="C", ref="ordeal-replacement-c")
                if scenario == "source_removed":
                    remove = self.add(session.engine, "Disenchant", seat="B", zone="hand")
                    for land_index in range(2):
                        self.add(session.engine, "Generic Bound Plains", seat="B", ref=f"ordeal-removal-mana-{land_index}")
                if scenario == "attacker_removed":
                    bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                    self.add(session.engine, "Island", seat="B")
                self.assert_closed(session, aura)
                self.prepare(session, mana={"C": 1, "U": 1})
                self.checkpoint(session)
                hand = len(session.state.players["A"].zones["hand"]) - 1
                self.cast_for(session, "A", aura, [attacker.ref])
                self.assertIsNone(self.resolve(session))
                self.attack(session, [(attacker, "D")])
                trigger, = session.state.stack
                self.assertEqual(aura.object_id, trigger.source_object_id)
                if scenario == "source_removed":
                    self.cast_for(session, "B", remove, [aura.ref])
                    self.resolve_top(session)
                    self.assertEqual("graveyard", session.state.cards[aura.object_id].zone)
                if scenario == "attacker_removed":
                    self.cast_for(session, "B", bounce, [attacker.ref])
                    self.resolve_top(session)
                    self.assertEqual("hand", session.state.cards[attacker.object_id].zone)
                    self.assertEqual(3, session.state.stack[-1].context[EVENT_COUNTER_THRESHOLD_CONTEXT]["departure_count"])
                    session = self.replay(session, load=True)
                if scenario == "replacements":
                    self.assertEqual("replacement.order", self.resolve(session).kind)
                    packet = session.packet("pilot:A", full=True)["decision"]
                    option = next(row for row in packet["ctx"]["options"] if row.get("source") == scales.ref)
                    before = authoritative_state_hash(session.state)
                    command = {"action_id": "choose", "replacement": option["id"]}
                    self.assertFalse(session.act("pilot:B", command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    session = self.replay(session, load=True)
                    result = session.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("replacement.order", session.state.pending_decision.kind)
                    self.assertEqual(5, session.state.cards[attacker.object_id].counters["+1/+1"])
                    self.assertEqual("battlefield", session.state.cards[aura.object_id].zone)
                    session = self.replay(session, load=True)
                    packet = session.packet("pilot:A", full=True)["decision"]
                    result = session.act("pilot:A", {"action_id": "choose", "replacement": packet["ctx"]["options"][0]["id"]})
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                destination = {"replacements": "exile", "below": "battlefield"}.get(scenario, "graveyard")
                self.assertEqual(destination, session.state.cards[aura.object_id].zone)
                expected_count = {"replacements": 5, "below": 2, "source_removed": 3}.get(scenario, 0)
                self.assertEqual(expected_count, session.state.cards[attacker.object_id].counters.get("+1/+1", 0))
                draws = 2 if scenario == "replacements" else 0
                returned = 1 if scenario == "attacker_removed" else 0
                self.assertEqual(hand + draws + returned, len(session.state.players["A"].zones["hand"]))
                self.replay(session)


ENTRY_EQUIPMENT_ORIGINALS = (
    "Dredging Claw", "Stormrider Rig", "Cloak and Dagger", "Doc Ock's Tentacles",
    "Veteran's Armaments", "Diviner's Wand", "Obsidian Battle-Axe",
    "Hero's Blade", "Sai of the Shinobi", "Inventor's Goggles", "Ronin Warclub",
)


class EntryEquipmentAttachmentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-equipment-attachment.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/entry-equipment-attachment-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry equipment attachment", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_entry_equipment_originals_bind(self):
        for index, name in enumerate(ENTRY_EQUIPMENT_ORIGINALS):
            with self.subTest(name=name):
                session = self.session(70103000 + index)
                self.assert_closed(session, self.add(session.engine, name))

    def test_optional_entry_equipment_moves_to_the_opponents_hexproof_creature(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                session = self.session(70103012 + apply)
                equipment = self.add(session.engine, "Cloak and Dagger")
                old_bearer = self.add(session.engine, "Generic Bound Body")
                entrant = self.add(session.engine, "Invisible Stalker", seat="B", zone="hand")
                self.add(session.engine, "Island", seat="B", ref="entry-attachment-blue")
                self.add(session.engine, "Generic Bound Plains", seat="B", ref="entry-attachment-generic")
                self.assert_closed(session, equipment)
                self.prepare(session, mana={"C": 3})
                self.checkpoint(session)
                rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                equip, = [row for row in rows if row["id"].startswith("activate:" + equipment.ref + ":")]
                result = session.act("pilot:A", {"action_id": equip["id"], "targets": [old_bearer.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(old_bearer.object_id, session.state.cards[equipment.object_id].attached_to)
                self.until(session, lambda current: current.state.active_player == "B"
                           and current.state.phase == "precombat_main"
                           and current.pending_principals() == ["pilot:B"])
                self.cast_for(session, "B", entrant)
                decision = self.resolve(session)
                self.assertEqual("semantic.choice", decision.kind)
                self.assertEqual(["A"], decision.actors)
                self.assertFalse(session.state.stack[-1].targets)
                command = {"action_id": "choose", "choice": "apply" if apply else "decline"}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                result = session.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[equipment.object_id]
                self.assertEqual(entrant.object_id if apply else old_bearer.object_id, current.attached_to)
                self.assertEqual(("A", "B"), (current.controller, session.state.cards[entrant.object_id].controller))
                self.replay(session)

    def test_protected_entry_skips_the_optional_attachment_choice(self):
        session = self.session(70103014)
        equipment = self.add(session.engine, "Stormrider Rig")
        entrant = self.add(session.engine, "Tel-Jilad Chosen", zone="hand")
        self.assert_closed(session, equipment)
        self.prepare(session, mana={"C": 1, "G": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", entrant)
        self.resolve_top(session)
        self.assertEqual(equipment.object_id, session.state.stack[-1].source_object_id)
        self.assertIsNone(self.resolve(session))
        self.assertIsNone(session.state.cards[equipment.object_id].attached_to)
        self.replay(session)

    def test_mandatory_entry_attachment_resolves_without_a_choice(self):
        session = self.session(70103015)
        equipment = self.add(session.engine, "Ronin Warclub")
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        self.assert_closed(session, equipment)
        self.prepare(session, mana={"C": 1, "G": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", entrant)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(entrant.object_id, session.state.cards[equipment.object_id].attached_to)
        self.replay(session)

    def test_entry_attachment_does_not_follow_an_entrant_that_returns(self):
        session = self.session(70103016)
        equipment = self.add(session.engine, "Dredging Claw")
        entrant = self.add(session.engine, "Grizzly Bears", zone="graveyard")
        revive = self.add(session.engine, "Zombify", zone="hand")
        blink = self.add(session.engine, "Ephemerate", zone="hand")
        self.assert_closed(session, equipment)
        self.assert_closed(session, revive)
        self.prepare(session, mana={"C": 3, "B": 1, "W": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", revive, [entrant.ref], reserve={"W": 1})
        self.resolve_top(session)
        self.assertEqual(equipment.object_id, session.state.stack[-1].source_object_id)
        original = session.state.cards[entrant.object_id].logical_object_id
        self.cast_for(session, "A", blink, [entrant.ref])
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(original, session.state.cards[entrant.object_id].logical_object_id)
        self.assertIsNone(session.state.cards[equipment.object_id].attached_to)
        self.replay(session)

    def test_entry_attachment_runtime_scope_rejects_the_wrong_occurrence(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.attachment_actions import ENTRY_ATTACHMENT_CAPABILITY

        session = self.session(70103017)
        equipment = self.add(session.engine, "Stormrider Rig")
        program, = [value for value in session.engine.semantics.programs_for_oracle(equipment.oracle_id)
                    if ENTRY_ATTACHMENT_CAPABILITY in value.capability_dependencies]
        wrong = replace(program, event="creature.attacks")
        binding = bind_semantic_program_runtime(wrong, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertTrue(any(value.startswith("runtime_occurrence_object:invalid:") for value in binding["blockers"]))

    def test_entry_attachment_legality_omission_is_killed(self):
        with mock.patch("quorune.rules.attachment_actions.equipment_attachment_is_legal", return_value=True):
            with self.assertRaises(AssertionError):
                self.test_protected_entry_skips_the_optional_attachment_choice()


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-attachment-actions.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "fixed-token-production-cards.json",
            FIXTURE,
        ],
        database,
    )
    return CardDatabase(database)


EXPECTED_ATTACHMENT_BEHAVIOR = (
    {
        "authority": ("301.5c", "701.3", "603.3d"),
        "oracle": (
            "When this Equipment enters, attach it to target creature you "
            "control."
        ),
        "legal_options": "one current controlled creature",
        "committed_result": "the same Equipment incarnation becomes attached",
        "counterexample": "an illegal target leaves the Equipment unattached",
    },
    {
        "authority": ("701.3", "702.6a"),
        "oracle": "Equip Human {1}",
        "legal_options": "one current controlled Human creature",
        "committed_result": "the resolving Equipment becomes attached",
        "counterexample": "a controlled non-Human creature is not offered",
    },
    {
        "authority": ("701.3", "702.92a"),
        "oracle": "Living weapon",
        "legal_options": "no target; create one fixed Phyrexian Germ",
        "committed_result": "attach the Equipment to one created Germ",
        "counterexample": "a departed or new Equipment incarnation is not attached",
    },
    {
        "authority": ("701.3", "702.163a"),
        "oracle": "For Mirrodin!",
        "legal_options": "no target; create one fixed red Rebel",
        "committed_result": "attach the Equipment to one created Rebel",
        "counterexample": "an independently added replacement token remains unattached",
    },
)


def record(
    name: str,
    oracle_text: str,
    *,
    type_line: str = "Artifact \N{EM DASH} Equipment",
    keywords: tuple[str, ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedAttachmentActionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, fixture: CardRecord):
        return compile_oracle_card(
            fixture,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_expectations_are_independent_and_distinguish_wrong_results(self):
        self.assertEqual(4, len(EXPECTED_ATTACHMENT_BEHAVIOR))
        for row in EXPECTED_ATTACHMENT_BEHAVIOR:
            with self.subTest(oracle=row["oracle"]):
                self.assertTrue(row["authority"])
                self.assertTrue(row["legal_options"])
                self.assertTrue(row["committed_result"])
                self.assertTrue(row["counterexample"])

    def test_fixture_type_lines_compile_without_platform_encoding_repair(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for row in payload["cards"]:
            with self.subTest(card=row["name"]):
                compiled = self.compile(
                    record(
                        row["name"],
                        row["oracle_text"],
                        type_line=row["type_line"],
                        keywords=tuple(row["keywords"]),
                    )
                )
                self.assertEqual("exact", compiled.status)

    def test_targeted_equipment_roles_reject_wrong_bindings_and_cardinality(self):
        from quorune.compiler.fixed_attachment_templates import targeted_equipment_attachment_template
        from quorune.rules.attachment_action_capability_shapes import (
            targeted_equipment_attachment_node_capabilities, targeted_equipment_attachment_runtime_dependencies,
        )
        from quorune.rules.attachment_actions import TARGETED_EQUIPMENT_CAPABILITY, TARGETED_EQUIPMENT_VERSION

        text = "Attach up to one target Equipment you control to target Rebel you control."
        _, effects, schema, mechanics = targeted_equipment_attachment_template(
            text, card_name="Equipment Transfer", source_is_permanent=False,
        )
        first, second = schema["groups"]
        self.assertEqual(1, first["up_to"])
        self.assertEqual(["equipment"], first["subtypes_any"])
        self.assertEqual(1, second["count"])
        self.assertEqual(["rebel"], second["subtypes_any"])
        self.assertNotIn("types_any", second)
        self.assertNotIn("globally_distinct", schema)
        self.assertIn(TARGETED_EQUIPMENT_CAPABILITY, targeted_equipment_attachment_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        for bad_effect in ({**effects[0], TARGETED_EQUIPMENT_VERSION: True},
                           {**effects[0], "source": "$target_group.target_1"},
                           {**effects[0], "target": "$target_group.target_0"}):
            self.assertFalse(targeted_equipment_attachment_node_capabilities(
                effects=(bad_effect,), target_schema=schema, mechanic_ids=mechanics,
            ))
        for bad_source in ({**first, "up_to": 2}, {**first, "subtypes_any": ["treasure"]}):
            self.assertFalse(targeted_equipment_attachment_node_capabilities(
                effects=effects, target_schema={"groups": [bad_source, second]}, mechanic_ids=mechanics,
            ))
        legacy = {"op": "attach", "attachment_kind": "equipment", "source": "$source.zone_object", "target": "$target.0"}
        program = SimpleNamespace(effects=(legacy,), target_schema=schema, coverage=mechanics)
        self.assertEqual((), targeted_equipment_attachment_runtime_dependencies(program))
        for source_role, recipient_role in (("$target_group.target_1", "$source.zone_object"),
                                            ("$target.0", "$target.1"),
                                            ("$source.zone_object", "$target_group.target_1")):
            program.effects = ({**legacy, "source": source_role, "target": recipient_role},)
            with self.assertRaisesRegex(ValueError, "independently closed attachment and recipient roles"):
                targeted_equipment_attachment_runtime_dependencies(program)

        source = record("Equipment Transfer", "Attach target Equipment to target creature.", type_line="Instant")
        self.assertEqual("exact", self.compile(source).status)
        with mock.patch("quorune.compiler.resolution_effect_templates.targeted_equipment_attachment_template", return_value=None):
            self.assertNotEqual("exact", self.compile(source).status)
        for body in ("Attach target Equipment to it.", "Attach target Equipment to target enchantment."):
            self.assertIsNone(targeted_equipment_attachment_template(
                body, card_name="Equipment Transfer", source_is_permanent=True,
            ))

    def test_source_equipment_entry_attach_is_typed_and_targeted(self):
        compiled = self.compile(
            record(
                "Entry Harness",
                "When this Equipment enters, attach it to target creature you control.",
            )
        )
        node = compiled.faces[0].nodes[0]

        self.assertTrue(node.exact)
        self.assertEqual("triggered_ability", node.kind)
        self.assertEqual("permanent.enter.self", node.event)
        self.assertEqual("fixed-source-equipment-attach-trigger-v1", node.template_id)
        self.assertEqual(
            (
                {
                    "op": "attach",
                    "attachment_kind": "equipment",
                    "source": "$source.zone_object",
                    "target": "$target.0",
                },
            ),
            node.effects,
        )
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "controller": "you",
                "creature": True,
                "count": 1,
            },
            node.target_schema,
        )
        self.assertIn(
            "attachment.action.fixed_source",
            node.capability_dependencies,
        )

    def test_source_aura_attachment_separates_target_and_enchant_legality(self):
        for source, predicate in (
            (
                "Attach this Aura to target creature.",
                None,
            ),
            (
                "Attach this Aura to target creature other than enchanted creature.",
                "not_source_attachment",
            ),
        ):
            with self.subTest(source=source):
                compiled = self.compile(
                    record(
                        "Moving Aura",
                        f"Enchant creature you control\n{{1}}{{U}}: {source}",
                        type_line="Enchantment \N{EM DASH} Aura",
                        keywords=("Enchant",),
                    )
                )
                node = next(
                    candidate
                    for candidate in compiled.faces[0].nodes
                    if any(
                        effect.get("op") == "attach"
                        for effect in candidate.effects
                    )
                )
                self.assertTrue(node.exact)
                self.assertEqual(predicate, node.target_schema.get("predicate"))
                self.assertIn(
                    "attachment.action.fixed_source",
                    node.capability_dependencies,
                )

    def test_restricted_equip_variants_share_one_activation_contract(self):
        cases = {
            "Equip creature token {1}": {"token": True},
            "Equip commander {2}": {"commander": True},
            "Equip legendary creature {3}": {
                "supertypes_any": ("legendary",),
            },
            "Equip Human {1}": {"subtypes_any": ("human",)},
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                ability = parse_activated_abilities(
                    card_name="Restricted Harness",
                    oracle_text=source,
                    keywords=("Equip",),
                )[0]
                self.assertTrue(ability.compiled_cost)
                self.assertTrue(ability.sorcery_speed)
                self.assertEqual("builtin:equip", ability.builtin_semantic_key)
                self.assertEqual("you", ability.target_schema["controller"])
                self.assertIs(True, ability.target_schema["creature"])
                for field, value in expected.items():
                    self.assertEqual(value, ability.target_schema[field])

                node = self.compile(
                    record(
                        "Restricted Harness",
                        source,
                        keywords=("Equip",),
                    )
                ).faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(
                    ("attachment.equip.fixed_restricted",),
                    node.capability_dependencies,
                )

        limited = parse_activated_abilities(
            card_name="Limited Harness",
            oracle_text="Equip {0}. Activate only once each turn.",
            keywords=("Equip",),
        )[0]
        self.assertEqual(ActivationLimit.ONCE_PER_TURN, limited.activation_limit)
        limited_node = self.compile(
            record(
                "Limited Harness",
                "Equip {0}. Activate only once each turn.",
                keywords=("Equip",),
            )
        ).faces[0].nodes[0]
        self.assertTrue(limited_node.exact)
        self.assertEqual(
            ("attachment.equip.fixed_restricted",),
            limited_node.capability_dependencies,
        )

    def test_living_weapon_and_for_mirrodin_lower_distinct_fixed_tokens(self):
        cases = (
            (
                "Living weapon",
                ("Equip", "Living weapon"),
                "Phyrexian Germ Token",
                "Token Creature \N{EM DASH} Phyrexian Germ",
                "0",
                "0",
                ["B"],
                "trigger.keyword.living_weapon",
            ),
            (
                "For Mirrodin!",
                ("Equip", "For Mirrodin!"),
                "Rebel Token",
                "Token Creature \N{EM DASH} Rebel",
                "2",
                "2",
                ["R"],
                "trigger.keyword.for_mirrodin",
            ),
        )
        for source, keywords, token, type_line, power, toughness, colors, capability in cases:
            with self.subTest(source=source):
                node = self.compile(
                    record("Token Harness", source, keywords=keywords)
                ).faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual("triggered_ability", node.kind)
                self.assertEqual("permanent.enter.self", node.event)
                self.assertEqual("create-attached-equipment-token-v2", node.template_id)
                effect = node.effects[0]
                self.assertEqual("create_token_then_attach_source", effect["op"])
                self.assertEqual("$source.zone_object", effect["source"])
                self.assertEqual(token, effect["token"]["name"])
                characteristics = effect["token"]["characteristics"]
                self.assertEqual(type_line, characteristics["type_line"])
                self.assertEqual(power, characteristics["power"])
                self.assertEqual(toughness, characteristics["toughness"])
                self.assertEqual(colors, characteristics["colors"])
                self.assertIn(capability, node.capability_dependencies)

    def test_created_attachment_payload_rejects_uncommitted_or_malformed_results(self):
        from quorune.rules.attachment_actions import (
            ATTACH_CREATED_TOKEN,
            created_token_attachment_payload_dependencies,
        )

        node = self.compile(record("Token Harness", "Living weapon", keywords=("Living weapon",))).faces[0].nodes[0]
        effect = node.effects[0]
        self.assertIn("attachment.action.fixed_source", created_token_attachment_payload_dependencies(effect))
        for malformed in (
            {**effect, "schema_version": True},
            {**effect, "source": "$target.0"},
            {**effect, "token": {**effect["token"], "quantity": True}},
            {"op": ATTACH_CREATED_TOKEN, "source": "$source.zone_object", "cards": ["T1"]},
        ):
            with self.subTest(effect=malformed), self.assertRaises(ValueError):
                created_token_attachment_payload_dependencies(malformed)

    def test_printed_token_attachment_closes_direct_and_paid_entry_bodies(self):
        from quorune.compiler.optional_payment_templates import (
            FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
            OPTIONAL_MANA_PAYMENT_OPERATION,
        )
        from quorune.rules.attachment_actions import CREATE_AND_ATTACH

        fixtures = {row["name"]: row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["cards"]}
        costs = {
            "Barbed Spike": None,
            "Auxiliary Boosters": None,
            "Dwarven Hammer": (2, 0),
            "Valkyrie's Sword": (4, 1),
        }
        for name, cost in costs.items():
            with self.subTest(card=name):
                row = fixtures[name]
                source = record(
                    name, row["oracle_text"], type_line=row["type_line"],
                    keywords=tuple(row["keywords"]),
                )
                ir = self.compile(source)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual("permanent.enter.self", node.event)
                self.assertEqual(source.oracle_text.splitlines()[0],
                                 source.oracle_text[node.span.start:node.span.end])
                self.assertIsNone(node.target_schema)
                self.assertIn("attachment.action.fixed_source", node.capability_dependencies)
                self.assertIn("token.creation.fixed_definition", node.capability_dependencies)
                effect, = node.effects
                if cost is not None:
                    self.assertEqual(OPTIONAL_MANA_PAYMENT_OPERATION, effect["op"])
                    self.assertEqual("$controller", effect["player"])
                    self.assertEqual(cost, (effect["cost"]["GENERIC"], effect["cost"]["W"]))
                    self.assertIn(FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY, node.capability_dependencies)
                    effect, = effect["effects"]
                self.assertEqual(CREATE_AND_ATTACH, effect["op"])
                self.assertEqual("$source.zone_object", effect["source"])
                self.assertEqual("$controller", effect["controller"])
                self.assertEqual(1, effect["token"]["quantity"])

    def test_printed_token_attachment_requires_one_definition_and_the_source_equipment(self):
        text = "When this Equipment enters, create a 1/1 white Soldier creature token, then attach this Equipment to it."
        base = record("Boundary Harness", text)
        for candidate in (
            replace(base, oracle_text=text.replace("attach this Equipment", "attach that Equipment")),
            replace(base, oracle_text=text.replace("a 1/1 white Soldier creature token",
                                                   "two 1/1 white Soldier creature tokens")),
            replace(base, oracle_text=text + " Untap it."),
            replace(base, type_line="Artifact"),
        ):
            with self.subTest(text=candidate.oracle_text, type_line=candidate.type_line):
                self.assertTrue(self.compile(candidate).material_residuals)

    def test_adjacent_attachment_grammar_remains_fail_closed(self):
        base = record("Boundary Harness", "Equip {2}", keywords=("Equip",))
        for oracle_text in (
            "Equip Human {W/U}",
            "Equip Human\N{EM DASH}Discard a card",
            "When this Equipment enters, attach it to target creature an opponent controls.",
            "Living Weapon \N{EM DASH} Create two Germ tokens.",
            "For Mirrodin \N{EM DASH} Create a Rebel token.",
        ):
            with self.subTest(oracle_text=oracle_text):
                compiled = self.compile(
                    replace(base, oracle_text=oracle_text)
                )
                self.assertNotEqual("exact", compiled.status)

    def test_printed_source_equipment_activations_share_closed_effect_admission(self):
        for subject, price in (("you control", "{B}{B}"), ("an opponent controls", "{1}")):
            with self.subTest(subject=subject):
                fixture = record("Printed Harness", f"{price}: Attach this Equipment to target creature {subject}.")
                ir = self.compile(fixture)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual("activate", node.event)
                self.assertEqual(({"op": "attach", "attachment_kind": "equipment",
                    "source": "$source.zone_object", "target": "$target.0"},), node.effects)
                self.assertEqual("you" if subject == "you control" else "opponent",
                    node.target_schema.get("controller", node.target_schema.get("controller_relation")))
                program = dict(event=node.event, active_zone=node.active_zone, cost_schema=node.cost,
                    effects=node.effects, target_schema=node.target_schema, coverage=node.mechanics,
                    capability_dependencies=node.capability_dependencies)
                self.assertTrue(is_closed_fixed_source_equipment_activation_program(SimpleNamespace(**program)))
                for changed in (
                    {"cost_schema": None},
                    {"cost_schema": {**node.cost, "uncompiled_costs": ["unrepresented cost"]}},
                    {"active_zone": "graveyard"},
                    {"effects": ({**node.effects[0], "source": "$target.0"},)},
                    {"target_schema": {**node.target_schema, "count": True}},
                    {"capability_dependencies": tuple(dep for dep in node.capability_dependencies
                        if dep != "target.revalidate_resolution")},
                ):
                    with self.subTest(change=tuple(changed)):
                        self.assertFalse(is_closed_fixed_source_equipment_activation_program(
                            SimpleNamespace(**{**program, **changed})))

        for fixture in (
            record("Open Harness", "{1}: Attach this Equipment to target artifact an opponent controls."),
            record("False Harness", "{1}: Attach this Equipment to target creature you control.", type_line="Artifact"),
        ):
            with self.subTest(oracle=fixture.oracle_text, type_line=fixture.type_line):
                self.assertTrue(self.compile(fixture).material_residuals)


class PrintedSourceAttachmentNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixtures = ROOT / "tests" / "fixtures"
        database = Path(cls.temporary.name) / "printed-source-attachment.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "changeling-characteristics-cards.json",
            fixtures / "classic-flash-cards.json",
            fixtures / "attached-exile-activation-cards.json",
            fixtures / "additional-conditional-modifier-cards.json",
            fixtures / "nonmodal-face-mana-value-cards.json",
            PRINTED_FIXTURE,
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Printed attachment", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    def attachment_offers(self, session, source):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [action for action in actions if action["id"].startswith(f"activate:{source.ref}:")]

    def attach(self, session, source, target):
        attach_objects(session.state.cards, source, target, source_timestamp=source.zone_timestamp,
            players=session.state.players)

    def assert_attached(self, session, source, target):
        self.assertEqual(target.object_id, session.state.cards[source.object_id].attached_to)
        self.assertIn(source.object_id, session.state.cards[target.object_id].attachments)

    def submit_attachment_targets(self, session, seat, action_id, equipment=None, recipient=None):
        targets = {"target_0": [equipment.ref] if equipment is not None else []}
        if recipient is not None:
            targets["target_1"] = [recipient.ref]
        command = {"action_id": action_id, "targets": targets}
        if action_id != "choose":
            command["pay"] = "auto"
            offers = session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
            self.assertIn(action_id, {row["id"] for row in offers})
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:" + seat, command)
        self.assertTrue(accepted.ok, accepted.summary)

    def choose_attachment_targets(self, session, equipment=None, recipient=None):
        self.until(session, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "semantic.target")
        self.submit_attachment_targets(session, "A", "choose", equipment, recipient)

    def cast_attachment_witness(self, session, card, payment, targets=()):
        self.priority(session, "A")
        action_id = "cast:" + card.ref
        offers = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {row["id"] for row in offers},
                      (session.state.phase, session.state.players["A"].mana_pool))
        accepted = session.act("pilot:A", {"action_id": action_id, "targets": list(targets),
                                            "pay": "manual", "payment": payment})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_targeted_equipment_moves_with_its_controller_and_keeps_revalidated_roles(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.attachment_actions import TARGETED_EQUIPMENT_CAPABILITY

        session = self.session(701_300_070)
        engine = session.engine
        squire = self.add(engine, "Brass Squire")
        squire.acquired_control_turn_count = -1
        collector = self.add(engine, "Kazuul's Toll Collector")
        barret = self.add(engine, "Barret, Avalanche Leader")
        recipient = self.add(engine, "Universal Automaton")
        old = self.add(engine, "Generic Bound Body", ref="attachment-old")
        foreign_old = self.add(engine, "Generic Bound Body", seat="B", ref="attachment-foreign-old")
        blinked = self.add(engine, "Generic Bound Body", seat="B", ref="attachment-blinked")
        equipment = self.add(engine, "Cranial Plating", ref="attachment-our-equipment")
        foreign = self.add(engine, "Cranial Plating", seat="B", ref="attachment-their-equipment")
        theft = self.add(engine, "Magnetic Theft", zone="hand", ref="attachment-our-theft")
        their_theft = self.add(engine, "Magnetic Theft", seat="B", zone="hand", ref="attachment-their-theft")
        blink = self.add(engine, "Flicker of Fate", zone="hand")
        private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="attachment-private")
        self.attach(session, equipment, old)
        self.attach(session, foreign, foreign_old)
        for card in (squire, collector, barret, theft, blink):
            self.assert_closed(session, card)
        program = next(program for program in engine.semantics.programs_for_oracle(theft.oracle_id)
                       if TARGETED_EQUIPMENT_CAPABILITY in program.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != TARGETED_EQUIPMENT_CAPABILITY)
        stripped = replace(program, capability_dependencies=dependencies,
                           capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + TARGETED_EQUIPMENT_CAPABILITY, binding["blockers"])
        session.state.players["B"].mana_pool["R"] = 1
        self.prepare(session, mana={"C": 1, "W": 1, "R": 1})
        self.checkpoint(session)

        offer, = self.attachment_offers(session, squire)
        wrong = {"action_id": offer["id"], "pay": "auto",
                 "targets": {"target_0": [foreign.ref], "target_1": [recipient.ref]}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", wrong).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.submit_attachment_targets(session, "A", offer["id"], equipment, recipient)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, recipient)

        offer, = self.attachment_offers(session, collector)
        self.submit_attachment_targets(session, "A", offer["id"], equipment)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, collector)

        self.submit_attachment_targets(session, "A", "cast:" + theft.ref, foreign, recipient)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, foreign, recipient)
        self.assertEqual("B", session.state.cards[foreign.object_id].controller)
        self.priority(session, "B")
        self.submit_attachment_targets(session, "B", "cast:" + their_theft.ref, foreign, blinked)
        incarnation = blinked.logical_object_id
        self.cast(session, "A", blink, [blinked.ref])
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(incarnation, session.state.cards[blinked.object_id].logical_object_id)
        self.assert_attached(session, foreign, recipient)

        self.choose_attachment_targets(session, recipient=recipient)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, collector)
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        self.replay(session)

    def test_targeted_equipment_entry_selects_the_entrant_and_defers_the_optional_choice(self):
        from quorune.session import CommanderSession

        session = self.session(701_300_071)
        engine = session.engine
        kemba = self.add(engine, "Kemba, Kha Enduring", zone="hand")
        cat = self.add(engine, "Universal Automaton", zone="hand")
        outfitter = self.add(engine, "Kor Outfitter", zone="hand")
        blink = self.add(engine, "Flicker of Fate", zone="hand")
        equipment = self.add(engine, "Cranial Plating")
        old = self.add(engine, "Generic Bound Body", ref="entry-transfer-old")
        self.attach(session, equipment, old)
        for card in (kemba, cat, outfitter, blink):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "W": 4})
        self.checkpoint(session)

        self.cast_attachment_witness(session, kemba, {"C": 1, "W": 1})
        self.choose_attachment_targets(session)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, old)
        self.cast_attachment_witness(session, cat, {"C": 1})
        self.choose_attachment_targets(session, equipment)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, cat)

        self.cast_attachment_witness(session, outfitter, {"W": 2})
        self.choose_attachment_targets(session, equipment, kemba)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        self.assert_attached(session, equipment, cat)
        declined = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, cat)

        self.cast_attachment_witness(session, blink, {"C": 1, "W": 1}, [outfitter.ref])
        self.choose_attachment_targets(session, equipment, kemba)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "optional-transfer"
            session.save(path)
            resumed = CommanderSession.load(self.db, path)
            self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(resumed.state))
        session = resumed
        applied = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(applied.ok, applied.summary)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, kemba)
        self.replay(session)

    def test_targeted_equipment_reflexive_payment_creates_the_targeted_child_after_payment(self):
        session = self.session(701_300_072)
        engine = session.engine
        vendor = self.add(engine, "Weapons Vendor", zone="hand")
        equipment = self.add(engine, "Cranial Plating")
        old = self.add(engine, "Generic Bound Body", ref="vendor-old-recipient")
        recipient = self.add(engine, "Generic Bound Body", ref="vendor-new-recipient")
        land = self.add(engine, "Generic Bound Plains", ref="vendor-payment-land")
        self.attach(session, equipment, old)
        self.assert_closed(session, vendor)
        self.prepare(session, mana={"C": 3, "W": 1})
        self.checkpoint(session)
        self.cast_attachment_witness(session, vendor, {"C": 3, "W": 1})
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[land.object_id].tapped)
        self.until(session, lambda current: any(item.source_object_id == vendor.object_id
                                               for item in current.state.stack))
        parent = session.state.stack[-1]
        self.assertFalse(parent.targets)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        self.assert_attached(session, equipment, old)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        paid = session.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        self.assertEqual(parent.ref, session.state.stack[-1].context["reflexive_parent"])
        self.choose_attachment_targets(session, equipment, recipient)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, equipment, recipient)
        self.replay(session)

    def test_actual_plating_uses_priority_and_revalidates_the_controlled_target(self):
        for stolen in (False, True):
            with self.subTest(stolen=stolen):
                session = self.session(701_300_051 + int(stolen))
                engine = session.engine
                plating = self.add(engine, "Cranial Plating")
                old = self.add(engine, "Generic Bound Body", ref="plating-old-recipient")
                selected = self.add(engine, "Generic Bound Body", ref="plating-new-recipient")
                opponent = self.add(engine, "Generic Bound Body", seat="B", ref="plating-opponent-creature")
                self.attach(session, plating, old)
                self.assert_closed(session, plating)
                if stolen:
                    theft = self.add(engine, "Confiscate", seat="B", zone="hand")
                    flash = self.add(engine, "Vedalken Orrery", seat="B")
                    for card in (theft, flash):
                        self.assert_closed(session, card)
                    session.state.players["B"].mana_pool.update({"C": 8, "U": 4})
                self.prepare(session, active="B", mana={"B": 2})
                self.checkpoint(session)
                self.priority(session, "A")
                self.assertEqual("B", session.state.active_player)
                offer, = self.attachment_offers(session, plating)
                command = {"action_id": offer["id"], "targets": [opponent.ref], "pay": "auto"}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                result = session.act("pilot:A", {**command, "targets": [selected.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(0, session.state.players["A"].mana_pool["B"])
                if stolen:
                    self.cast(session, "B", theft, [selected.ref])
                    self.resolve_top(session)
                    self.assertEqual("B", session.state.cards[selected.object_id].controller)
                self.assertIsNone(self.resolve(session))
                self.assert_attached(session, plating, old if stolen else selected)
                self.replay(session, load=True)

    def test_actual_bloodthirsty_blade_moves_the_live_goad_at_sorcery_speed(self):
        for active in ("A", "B"):
            with self.subTest(active=active):
                session = self.session(701_300_054 + (active == "B"))
                engine = session.engine
                blade = self.add(engine, "Bloodthirsty Blade")
                first = self.add(engine, "Generic Bound Body", seat="B", ref="blade-first-recipient")
                second = self.add(engine, "Generic Bound Body", seat="C", ref="blade-second-recipient")
                own = self.add(engine, "Generic Bound Body", ref="blade-controller-creature")
                self.attach(session, blade, first)
                self.assert_closed(session, blade)
                self.prepare(session, active=active, mana={"C": 2})
                self.checkpoint(session)
                self.priority(session, "A")
                offers = self.attachment_offers(session, blade)
                if active == "B":
                    self.assertEqual([], offers)
                    self.replay(session, load=True)
                    continue
                offer, = offers
                command = {"action_id": offer["id"], "targets": [own.ref], "pay": "auto"}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                result = session.act("pilot:A", {**command, "targets": [second.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assert_attached(session, blade, second)
                self.assertEqual("A", session.state.cards[blade.object_id].controller)
                packet = session.packet("spectator", full=True)["state"]["players"]
                old_recipient = next(obj for obj in packet["B"]["bf"] if obj["id"] == first.ref)
                new_recipient = next(obj for obj in packet["C"]["bf"] if obj["id"] == second.ref)
                self.assertNotIn("goad", old_recipient)
                self.assertEqual(["A"], new_recipient["goad"])
                self.replay(session, load=True)

    def test_actual_mantle_attaches_on_second_draw_and_ignores_later_draws(self):
        session = self.session(701_300_056)
        engine = session.engine
        mantle = self.add(engine, "Mantle of Tides")
        recipient = self.add(engine, "Generic Bound Body", ref="mantle-controller-creature")
        opponent = self.add(engine, "Generic Bound Body", seat="B", ref="mantle-opponent-creature")
        draws = [self.add(engine, "Divination", zone="hand", ref=f"mantle-draw-{index}") for index in (1, 2)]
        for card in (mantle, *draws):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 8, "U": 4})
        drawn_refs = [session.state.cards[object_id].ref for object_id in session.state.players["A"].zones["library"][-2:]]
        self.checkpoint(session)
        self.cast(session, "A", draws[0])
        decision = self.resolve(session)
        self.assertEqual("semantic.target", decision.kind)
        self.assertEqual(["A"], decision.actors)
        for seat in "BCD":
            packet = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(ref not in packet for ref in drawn_refs))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": [opponent.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.target(session, "A", [recipient.ref])
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, mantle, recipient)
        self.cast(session, "A", draws[1])
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, mantle, recipient)
        self.replay(session, load=True)

    def test_actual_forebear_and_hylderblade_use_the_departure_at_their_own_times(self):
        session = self.session(701_300_057)
        engine = session.engine
        forebear = self.add(engine, "Forebear's Blade")
        hylderblade = self.add(engine, "Hylderblade")
        dying = self.add(engine, "Generic Bound Body", ref="forebear-departing-creature")
        recipient = self.add(engine, "Generic Bound Body", ref="forebear-new-recipient")
        murder = self.add(engine, "Murder", zone="hand")
        self.attach(session, forebear, dying)
        for card in (forebear, hylderblade, murder):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 6, "B": 4})
        self.checkpoint(session)
        self.cast(session, "A", murder, [dying.ref])
        decision = self.resolve(session)
        self.assertEqual("semantic.target", decision.kind)
        self.assertEqual("graveyard", session.state.cards[dying.object_id].zone)
        self.target(session, "A", [recipient.ref])
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, forebear, recipient)
        self.assertIsNone(session.state.cards[hylderblade.object_id].attached_to)
        self.target(session, "A", [recipient.ref])
        self.assertEqual("end_step", session.state.step)
        self.assertIsNone(self.resolve(session))
        self.assert_attached(session, hylderblade, recipient)
        self.replay(session, load=True)


class AttachedTokenChoiceNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-token-choice.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            FIXTURE,
            fixtures / "additional-token-definition-cards.json",
            fixtures / "attachment-damage-cards.json",
            fixtures / "empty-permanent-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Created attachment choices",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def _open_entry_trigger(self, session, equipment):
        self.cast(session, "A", equipment)
        self.resolve_top(session)
        trigger = session.state.stack[-1]
        self.assertEqual(("triggered_ability", "A"), (trigger.kind, trigger.controller))
        self.assertEqual(equipment.object_id, trigger.source_object_id)
        return trigger.ref

    def _tokens(self, session, name):
        return [card for card in session.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.printed_name == name]

    def test_actual_current_equipment_controller_selects_other_seats_rebel_after_load(self):
        session = self.session(713001)
        session.state.config.semantic_policy = "trusted_only"
        engine = session.engine
        equipment = self.add(engine, "Hexgold Halberd", zone="hand")
        lives = self.add(engine, "Parallel Lives")
        aura = self.add(engine, "Steal Artifact", seat="B", zone="hand")
        orrery = self.add(engine, "Vedalken Orrery", seat="B")
        for card in (equipment, lives, aura, orrery):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1, "R": 1})
        session.state.players["B"].mana_pool.update({"C": 2, "U": 2})
        self.checkpoint(session)

        trigger_ref = self._open_entry_trigger(session, equipment)
        self.cast(session, "B", aura, [equipment.ref])
        self.resolve_top(session)
        equipment = session.state.cards[equipment.object_id]
        self.assertEqual("B", equipment.controller)
        self.assertEqual(trigger_ref, session.state.stack[-1].ref)

        choice = self.resolve(session)
        self.assertEqual("semantic.choice", choice.kind)
        self.assertEqual(["B"], list(choice.actors))
        self.assertIsNone(equipment.attached_to)
        rebels = self._tokens(session, "Rebel Token")
        self.assertEqual(2, len(rebels))
        self.assertTrue(all(card.owner == "A" and card.controller == "A" for card in rebels))
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        legal = choice.payload_by_actor["B"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertEqual([card.ref for card in rebels], list(legal))

        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        for seat, reference in (("A", legal[1]), ("B", equipment.ref)):
            rejected = session.act("pilot:" + seat, {"action_id": "choose", "objects": [reference]})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:B", {"action_id": "choose", "objects": [legal[1]]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(rebels[1].object_id, session.state.cards[equipment.object_id].attached_to)
        self.assertEqual(2, len(self._tokens(session, "Rebel Token")))
        self.replay(session, load=True)

    def test_actual_doubled_living_weapon_attaches_before_zero_toughness_state_actions(self):
        session = self.session(713002)
        session.state.config.semantic_policy = "trusted_only"
        source = self.add(session.engine, "Flayer Husk", zone="hand")
        self.add(session.engine, "Parallel Lives")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        choice = self.resolve(session)
        self.assertEqual("semantic.choice", choice.kind)
        germs = self._tokens(session, "Phyrexian Germ Token")
        self.assertEqual(2, len(germs))
        self.assertTrue(all(session.engine._numeric_stat(card.object_id, "toughness") == 0 for card in germs))

        accepted = session.act("pilot:A", {"action_id": "choose", "objects": [germs[1].ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        survivor, = self._tokens(session, "Phyrexian Germ Token")
        self.assertEqual(germs[1].object_id, survivor.object_id)
        self.assertEqual(survivor.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual(1, session.engine._numeric_stat(survivor.object_id, "toughness"))
        self.replay(session, load=True)

    def test_actual_departed_equipment_creates_rebels_without_an_attachment_choice(self):
        session = self.session(713003)
        session.state.config.semantic_policy = "trusted_only"
        source = self.add(session.engine, "Hexgold Halberd", zone="hand")
        self.add(session.engine, "Parallel Lives")
        destroy = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        self.assert_closed(session, source)
        self.assert_closed(session, destroy)
        self.prepare(session, mana={"C": 1, "R": 1})
        session.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(session)

        self._open_entry_trigger(session, source)
        self.cast(session, "B", destroy, [source.ref])
        self.resolve_top(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertEqual(2, len(self._tokens(session, "Rebel Token")))
        self.replay(session, load=True)

    def test_actual_barbed_spike_attaches_to_the_chosen_created_thopter(self):
        session = self.session(714001)
        session.state.config.semantic_policy = "trusted_only"
        source = self.add(session.engine, "Barbed Spike", zone="hand")
        doubling = self.add(session.engine, "Parallel Lives")
        for card in (source, doubling):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1, "W": 1})
        self.checkpoint(session)

        self._open_entry_trigger(session, source)
        choice = self.resolve(session)
        self.assertEqual("semantic.choice", choice.kind)
        self.assertEqual(["A"], list(choice.actors))
        thopters = self._tokens(session, "Thopter Token")
        self.assertEqual(2, len(thopters))
        legal = choice.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertEqual([card.ref for card in thopters], list(legal))
        self.assertEqual([1, 1], [session.engine._numeric_stat(card.object_id, "power") for card in thopters])
        self.assertIsNone(session.state.cards[source.object_id].attached_to)

        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "objects": [legal[1]]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(thopters[1].object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual([1, 2], [session.engine._numeric_stat(card.object_id, "power") for card in thopters])
        self.replay(session, load=True)

    def test_actual_dwarven_hammer_payment_decline_and_departed_source(self):
        for mode in ("pay", "decline", "destroyed"):
            with self.subTest(mode=mode):
                session = self.session(714002)
                session.state.config.semantic_policy = "trusted_only"
                source = self.add(session.engine, "Dwarven Hammer", zone="hand")
                destroy = self.add(session.engine, "Disenchant", seat="B", zone="hand")
                for card in (source, destroy):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"C": 4, "R": 1})
                session.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(session)

                self._open_entry_trigger(session, source)
                if mode == "destroyed":
                    self.cast(session, "B", destroy, [source.ref])
                    self.resolve_top(session)
                choice = self.resolve(session)
                self.assertEqual("semantic.choice", choice.kind)
                self.assertEqual(["A"], list(choice.actors))
                self.assertEqual(2, session.state.players["A"].mana_pool["C"])
                self.assertFalse(self._tokens(session, "Dwarf Berserker Token"))
                self.assertIsNone(session.packet("pilot:B", full=True)["decision"])

                session = self.replay(session, load=True)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                pay = mode != "decline"
                result = session.act("pilot:A", {"action_id": "choose", "pay": pay})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(0 if pay else 2, session.state.players["A"].mana_pool.get("C", 0))
                equipment = session.state.cards[source.object_id]
                tokens = self._tokens(session, "Dwarf Berserker Token")
                if pay:
                    token, = tokens
                    self.assertEqual(("A", "A"), (token.owner, token.controller))
                    self.assertEqual(5 if mode == "pay" else 2, session.engine._numeric_stat(token.object_id, "power"))
                    self.assertEqual(token.object_id if mode == "pay" else None, equipment.attached_to)
                else:
                    self.assertFalse(tokens)
                    self.assertIsNone(equipment.attached_to)
                self.assertEqual("graveyard" if mode == "destroyed" else "battlefield", equipment.zone)
                self.replay(session, load=True)


class FixedAttachmentActionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        card = CardInstance(
            object_id=f"attachment-action:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=(
                [seat] if zone in {"hand", "library"} else list(engine.seats)
            ),
            revealed_to=(
                [] if zone in {"hand", "library"} else list(engine.seats)
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def deck_card(engine, seat: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def pass_until_empty(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            seat = session.engine.state.priority_player
            if seat is None:
                session.engine._grant_priority(session.engine.state.active_player)
                seat = session.engine.state.priority_player
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Attachment stack did not resolve")

    def _battlefield_creature(self, engine, seat: str, name: str) -> CardInstance:
        card = self.deck_card(engine, seat, name)
        engine.move_card(card.object_id, "battlefield", controller=seat, log=False)
        return card

    def test_equipment_entry_trigger_attaches_and_revalidates_target(self):
        session = self.session(7013001)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="entry-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        power_before = engine._numeric_stat(target.object_id, "power")

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attachment entry witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, source.attached_to)
        self.assertEqual(
            power_before + 1,
            engine._numeric_stat(target.object_id, "power"),
        )

        stale = self.session(7013002)
        stale_engine = stale.engine
        stale_source = self.add_card(
            stale_engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="stale-entry-harness",
            zone="hand",
        )
        stale_target = self._battlefield_creature(
            stale_engine, "A", "Mishra, Eminent One"
        )
        stale_engine.move_card(
            stale_source.object_id,
            "battlefield",
            controller="A",
            reason="stale attachment entry witness",
            semantic_events=True,
        )
        stale_engine._stabilize()
        chosen = stale.act(
            "pilot:A",
            {"action_id": "choose", "targets": [stale_target.ref]},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        stale_engine.move_card(stale_target.object_id, "graveyard", log=False)
        self.resolve_top(stale_engine)
        self.assertIsNone(stale_source.attached_to)

    def test_entry_attachment_composes_with_destination_replacement(self):
        session = self.session(7013023, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Destination Harness Fixture",
            ref="destination-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine,
            "A",
            "Mishra, Eminent One",
        )
        victim = self.deck_card(engine, "B", "Sol Ring")
        engine.move_card(victim.object_id, "hand", log=False)

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attachment and destination replacement witness",
            semantic_events=True,
        )
        engine._stabilize()
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, source.attached_to)

        engine.move_card(
            victim.object_id,
            "graveyard",
            reason="destination replacement while attached",
            semantic_events=True,
        )

        self.assertEqual("exile", victim.zone)
        self.assertEqual(1, victim.counters.get("void"))
        self.assertEqual(target.object_id, source.attached_to)
        self.assertIn(source.object_id, target.attachments)

    def test_entry_attachment_and_turn_gated_characteristics_compose(self):
        session = self.session(7013021)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Stateful Entry Harness Fixture",
            ref="stateful-entry-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        base_power = engine._numeric_stat(target.object_id, "power")
        engine.state.active_player = "A"

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="turn-gated attachment composition",
            semantic_events=True,
        )
        engine._stabilize()
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.resolve_top(engine)

        active = engine._effective_card_data(target)
        self.assertEqual(str(base_power + 2), active["power"])
        self.assertIn("First Strike", active["keywords"])
        self.assertEqual(target.object_id, source.attached_to)

        engine.state.active_player = "B"
        inactive = engine._effective_card_data(target)
        self.assertEqual(str(base_power), inactive["power"])
        self.assertNotIn("First Strike", inactive["keywords"])
        self.assertEqual(target.object_id, source.attached_to)

    def test_entry_attachment_and_granted_draw_compose(self):
        session = self.session(7013022)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Drawing Entry Harness Fixture",
            ref="drawing-entry-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        engine.state.active_player = "A"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attachment draw composition",
            semantic_events=True,
        )
        engine._stabilize()
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, source.attached_to)
        self.assertIn("Trample", engine._effective_card_data(target)["keywords"])

        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        target.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={target.object_id: "B"},
            defending_players=["B"],
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        life_before = engine.state.players["B"].life

        engine._begin_combat_damage()
        self.assertLess(engine.state.players["B"].life, life_before)
        self.assertTrue(
            engine.state.stack,
            {
                "pending_trigger_batches": [
                    batch.to_dict()
                    for batch in engine.state.pending_trigger_batches
                ],
                "recent_events": [
                    event.to_dict() for event in engine.state.events[-8:]
                ],
                "target_keywords": engine._effective_card_data(target)[
                    "keywords"
                ],
            },
        )
        self.resolve_top(engine)

        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(target.object_id, source.attached_to)

    def test_legacy_equipment_attach_event_shape_is_preserved(self):
        session = self.session(7013017)
        engine = session.engine
        equipment = self.add_card(
            engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="legacy-event-harness",
            zone="battlefield",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )

        engine.apply_effect(
            {
                "op": "attach",
                "equipment": equipment.ref,
                "creature": target.ref,
                "reason": "legacy attachment witness",
            },
            actor="A",
        )

        event = next(
            value
            for value in reversed(engine.state.events)
            if value.code == "attachment.attach"
        )
        self.assertEqual(
            {
                "equipment": equipment.ref,
                "creature": target.ref,
                "reason": "legacy attachment witness",
            },
            event.details,
        )
        self.assertEqual(target.object_id, equipment.attached_to)

    def test_departed_or_reentered_source_does_not_attach(self):
        session = self.session(7013003)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Entry Harness Fixture",
            ref="incarnation-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="source incarnation witness",
            semantic_events=True,
        )
        engine._stabilize()
        old_identity = source.logical_object_id
        chosen = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertNotEqual(old_identity, source.logical_object_id)

        self.resolve_top(engine)

        self.assertIsNone(source.attached_to)
        self.assertNotIn(source.object_id, target.attachments)

        for seed, source_name in (
            (7013013, "Living Harness Fixture"),
            (7013014, "Mirrodin Harness Fixture"),
        ):
            with self.subTest(source=source_name):
                token_session = self.session(seed)
                token_engine = token_session.engine
                token_source = self.add_card(
                    token_engine,
                    seat="A",
                    name=source_name,
                    ref=f"stale-token-source-{seed}",
                    zone="hand",
                )
                token_engine.move_card(
                    token_source.object_id,
                    "battlefield",
                    controller="A",
                    reason="stale token attachment source entered",
                    semantic_events=True,
                )
                token_engine._stabilize()
                old_token_identity = token_source.logical_object_id
                token_engine.move_card(
                    token_source.object_id, "graveyard", log=False
                )
                token_engine.move_card(
                    token_source.object_id,
                    "battlefield",
                    controller="A",
                    log=False,
                )
                self.assertNotEqual(
                    old_token_identity,
                    token_source.logical_object_id,
                )
                self.resolve_top(token_engine)
                self.assertIsNone(token_source.attached_to)

    def test_restricted_equip_offer_commit_and_current_target_revalidation(self):
        session = self.session(7013004)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Restricted Harness Fixture",
            ref="restricted-harness",
            zone="battlefield",
        )
        human_ref = engine.create_token(
            "A",
            name="Human witness",
            characteristics={
                "type_line": "Token Creature \N{EM DASH} Human",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        insect_ref = engine.create_token(
            "A",
            name="Insect witness",
            characteristics={
                "type_line": "Token Creature \N{EM DASH} Insect",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        human = engine._resolve_object("A", human_ref)
        insect = engine._resolve_object("A", insect_ref)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["C"] = 3

        with self.assertRaisesRegex(Exception, "target"):
            engine._activate(
                "A",
                {
                    "source": source.ref,
                    "ability": "ab2",
                    "targets": [insect.ref],
                },
            )
        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": "ab2",
                "targets": [human.ref],
            },
        )
        human.annotations["token_characteristics"]["type_line"] = (
            "Token Creature \N{EM DASH} Insect"
        )
        human.annotations["copy_overrides"]["type_line"] = (
            "Token Creature \N{EM DASH} Insect"
        )
        self.resolve_top(engine)
        self.assertIsNone(source.attached_to)

    def test_once_per_turn_equip_limit_is_shared_by_offer_and_commit(self):
        session = self.session(7013015)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Limited Harness Fixture",
            ref="limited-harness",
            zone="battlefield",
        )
        target = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        ability = next(
            ability
            for ability in engine._activated_abilities(source)
            if ability.ability_id == "ab2"
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("A", source, ability),
        )
        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": [target.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(target.object_id, source.attached_to)
        self.assertEqual(
            ("unavailable", "already_activated_this_turn"),
            engine._ability_availability("A", source, ability),
        )

    def test_aura_reattachment_rechecks_live_enchant_legality(self):
        session = self.session(7013005)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Exclusive Moving Aura Fixture",
            ref="moving-aura",
            zone="battlefield",
        )
        first_ref = engine.create_token(
            "A",
            name="First creature",
            characteristics={
                "type_line": "Token Creature \N{EM DASH} Human",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        second_ref = engine.create_token(
            "B",
            name="Second creature",
            characteristics={
                "type_line": "Token Creature \N{EM DASH} Insect",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        first = engine._resolve_object("A", first_ref)
        second = engine._resolve_object("A", second_ref)
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 3

        with self.assertRaisesRegex(Exception, "target"):
            engine._activate(
                "A",
                {
                    "source": aura.ref,
                    "ability": "ab3",
                    "targets": [first.ref],
                },
            )
        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [second.ref],
            },
        )
        second.annotations["token_characteristics"]["type_line"] = (
            "Token Artifact"
        )
        second.annotations["copy_overrides"]["type_line"] = "Token Artifact"
        self.resolve_top(engine)
        self.assertEqual(first.object_id, aura.attached_to)

    def test_aura_reattachment_uses_current_source_controller(self):
        session = self.session(7013020)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Controlled Moving Aura Fixture",
            ref="controlled-moving-aura",
            zone="battlefield",
        )
        first = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        second = engine._resolve_object(
            "A",
            engine.create_token(
                "A",
                name="Second controlled creature",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Human",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        opposing = engine._resolve_object(
            "B",
            engine.create_token(
                "B",
                name="Opposing creature",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Insect",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 4

        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [opposing.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(first.object_id, aura.attached_to)

        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [second.ref],
            },
        )
        engine.change_control(first.object_id, "B", reason="Aura control witness")
        engine.change_control(second.object_id, "B", reason="Aura control witness")
        engine.change_control(aura.object_id, "B", reason="Aura control witness")

        self.resolve_top(engine)

        self.assertEqual(second.object_id, aura.attached_to)
        self.assertEqual("B", aura.controller)
        self.assertEqual("B", second.controller)

    def test_aura_reattachment_keeps_stack_controller_targeting_after_control_change(self):
        session = self.session(7013021)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Exclusive Moving Aura Fixture",
            ref="hexproof-moving-aura",
            zone="battlefield",
        )
        first = engine._resolve_object(
            "A",
            engine.create_token(
                "A",
                name="First attachment recipient",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Human",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        target = engine._resolve_object(
            "A",
            engine.create_token(
                "A",
                name="Controlled Hexproof recipient",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Human",
                    "power": "2",
                    "toughness": "2",
                    "keywords": ["Hexproof"],
                },
            )[0],
        )
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 3
        engine._issue_priority("A")

        activated = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{aura.ref}:ab3",
                "targets": [target.ref],
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("A", engine.state.stack[-1].controller)
        engine.change_control(aura.object_id, "B", reason="resolution witness")
        self.assertEqual("B", aura.controller)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.pass_until_empty(session)

        self.assertEqual(target.object_id, aura.attached_to)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "aura-controller-change-replay"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_aura_reattachment_rejects_opponent_hexproof_targeting(self):
        session = self.session(7013022)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Exclusive Moving Aura Fixture",
            ref="opponent-hexproof-moving-aura",
            zone="battlefield",
        )
        first = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        target = engine._resolve_object(
            "B",
            engine.create_token(
                "B",
                name="Opposing Hexproof recipient",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Insect",
                    "power": "2",
                    "toughness": "2",
                    "keywords": ["Hexproof"],
                },
            )[0],
        )
        engine.create_token(
            "A",
            name="Alternative legal recipient",
            characteristics={
                "type_line": "Token Creature \N{EM DASH} Human",
                "power": "2",
                "toughness": "2",
            },
        )
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 3
        engine._issue_priority("A")

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{aura.ref}:ab3",
                "targets": [target.ref],
            },
        )
        self.assertFalse(rejected.ok)
        self.assertIn("target", rejected.summary.casefold())
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(first.object_id, aura.attached_to)

    def test_aura_reattachment_uses_current_controller_enchant_restriction(self):
        session = self.session(7013023)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Controlled Moving Aura Fixture",
            ref="restricted-controller-moving-aura",
            zone="battlefield",
        )
        first = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        target = engine._resolve_object(
            "A",
            engine.create_token(
                "A",
                name="Old-controller recipient",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Human",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 2

        engine._activate(
            "A",
            {
                "source": aura.ref,
                "ability": "ab3",
                "targets": [target.ref],
            },
        )
        engine.change_control(first.object_id, "B", reason="enchant witness")
        engine.change_control(aura.object_id, "B", reason="enchant witness")
        self.resolve_top(engine)

        self.assertEqual(first.object_id, aura.attached_to)
        self.assertEqual("B", first.controller)
        self.assertEqual("B", aura.controller)
        self.assertEqual("A", target.controller)

    def test_nontargeted_aura_attachment_respects_protection(self):
        session = self.session(7013024)
        engine = session.engine
        aura = self.add_card(
            engine,
            seat="A",
            name="Exclusive Moving Aura Fixture",
            ref="protected-recipient-moving-aura",
            zone="battlefield",
        )
        first = self._battlefield_creature(
            engine, "A", "Mishra, Eminent One"
        )
        target = engine._resolve_object(
            "A",
            engine.create_token(
                "A",
                name="Protected attachment recipient",
                characteristics={
                    "type_line": "Token Creature \N{EM DASH} Human",
                    "power": "2",
                    "toughness": "2",
                },
            )[0],
        )
        target.annotations["copy_overrides"] = {
            "keywords": ["Protection"],
            "ability_fragments": [
                ability_fragment_to_dict(
                    ProtectionSpec(ProtectionQualityKind.COLOR, "U")
                )
            ],
        }
        attach_objects(
            engine.state.cards,
            aura,
            first,
            source_timestamp=engine._next_zone_timestamp(),
        )

        engine.apply_effect(
            {
                "op": "attach",
                "attachment_kind": "aura",
                "source": aura.ref,
                "target": target.ref,
            },
            actor="A",
        )

        self.assertEqual(first.object_id, aura.attached_to)

    def test_living_weapon_creates_germ_and_attaches_before_state_actions(self):
        session = self.session(7013006)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Living Harness Fixture",
            ref="living-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="living weapon witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        germs = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Phyrexian Germ Token"
        ]
        self.assertEqual(1, len(germs))
        self.assertEqual(germs[0].object_id, source.attached_to)
        self.assertEqual(1, engine._numeric_stat(germs[0].object_id, "toughness"))

    def test_for_mirrodin_creates_rebel_and_attaches_source(self):
        session = self.session(7013007)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Mirrodin Harness Fixture",
            ref="mirrodin-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="for mirrodin witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        rebels = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Rebel Token"
        ]
        self.assertEqual(1, len(rebels))
        self.assertEqual(rebels[0].object_id, source.attached_to)
        self.assertEqual(["R"], engine._effective_card_data(rebels[0])["colors"])

    def test_token_attachment_uses_trigger_controller_after_control_change(self):
        session = self.session(7013016)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Mirrodin Harness Fixture",
            ref="controlled-mirrodin-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attachment control-change witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual("A", engine.state.stack[-1].controller)
        engine.change_control(
            source.object_id,
            "B",
            reason="attachment control-change witness",
        )
        self.resolve_top(engine)
        rebel = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Rebel Token"
        )
        self.assertEqual("A", rebel.controller)
        self.assertEqual("B", source.controller)
        self.assertEqual(rebel.object_id, source.attached_to)

    def test_token_replacement_attaches_exactly_one_created_token(self):
        session = self.session(7013008)
        engine = session.engine
        replacement = self.add_card(
            engine,
            seat="A",
            name="Token Echo Fixture",
            ref="token-echo",
            zone="battlefield",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Living Harness Fixture",
            ref="replacement-living-harness",
            zone="hand",
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            reason="attached token replacement witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        created = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.printed_name in {"Food Token", "Phyrexian Germ Token"}
        ]
        self.assertEqual({"Food Token", "Phyrexian Germ Token"}, {c.printed_name for c in created})
        germ = next(c for c in created if c.printed_name == "Phyrexian Germ Token")
        food = next(c for c in created if c.printed_name == "Food Token")
        self.assertEqual(germ.object_id, source.attached_to)
        self.assertNotEqual(food.object_id, source.attached_to)

    def test_malformed_attached_token_effect_fails_before_mutation(self):
        session = self.session(7013019)
        engine = session.engine
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "closed fixed shape"):
            engine.apply_effect(
                {
                    "op": "create_attached_token",
                    "controller": "A",
                    "name": "Phyrexian Germ",
                    "quantity": 1,
                    "characteristics": {
                        "type_line": "Token Creature \N{EM DASH} Phyrexian Germ",
                        "power": "0",
                        "toughness": "0",
                    },
                },
                actor="A",
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(
            any(
                card.is_token and card.printed_name == "Phyrexian Germ"
                for card in engine.state.cards.values()
            )
        )

    def test_four_player_attachment_target_projection_and_replay(self):
        session = self.session(7013009, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Entry Harness Fixture",
            ref="replay-entry-harness",
            zone="hand",
        )
        target = self._battlefield_creature(
            engine, "C", "Mishra, Eminent One"
        )
        engine.state.active_player = "C"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="C",
            reason="four-player attachment witness",
            semantic_events=True,
        )
        engine._stabilize()
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:C"))
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.assertNotIn(
            source.object_id,
            json.dumps(projector._decision("pilot:C"), sort_keys=True),
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selected = session.act(
            "pilot:C",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        self.pass_until_empty(session)
        self.assertEqual(target.object_id, source.attached_to)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "attachment-replay"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_four_player_restricted_equip_projection_and_replay(self):
        session = self.session(7013010, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Restricted Harness Fixture",
            ref="replay-restricted-harness",
            zone="battlefield",
        )
        target_ref = engine.create_token(
            "C",
            name="Replay Human",
            characteristics={
                "type_line": "Token Creature \N{EM DASH} Human",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        target = engine._resolve_object("C", target_ref)
        engine.state.active_player = "C"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "C"
        engine.state.players["C"].mana_pool["C"] = 2
        engine._issue_priority("C")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        activated = session.act(
            "pilot:C",
            {
                "action_id": f"activate:{source.ref}:ab2",
                "targets": [target.ref],
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        self.pass_until_empty(session)
        self.assertEqual(target.object_id, source.attached_to)
        for seat in ("A", "B", "C", "D"):
            packet = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            self.assertNotIn(source.object_id, json.dumps(packet, sort_keys=True))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "restricted-equip-replay"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_four_player_token_attachment_triggers_replay(self):
        for seed, source_name, token_name in (
            (7013011, "Living Harness Fixture", "Phyrexian Germ Token"),
            (7013012, "Mirrodin Harness Fixture", "Rebel Token"),
        ):
            with self.subTest(source=source_name):
                session = self.session(seed, players=4)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="C",
                    name=source_name,
                    ref=f"replay-token-harness-{seed}",
                    zone="hand",
                )
                engine.state.active_player = "C"
                engine.move_card(
                    source.object_id,
                    "battlefield",
                    controller="C",
                    reason="four-player token attachment witness",
                    semantic_events=True,
                )
                engine._stabilize()
                self.assertTrue(engine.state.stack)
                engine._grant_priority("C")
                engine._issue_priority("C")
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()

                self.pass_until_empty(session)
                token = next(
                    card
                    for card in engine.state.cards.values()
                    if card.is_token and card.printed_name == token_name
                )
                self.assertEqual(token.object_id, source.attached_to)
                for seat in ("A", "B", "C", "D"):
                    packet = StateProjector(self.db, engine.state)._snapshot(
                        f"pilot:{seat}"
                    )
                    self.assertNotIn(
                        token.object_id,
                        json.dumps(packet, sort_keys=True),
                    )
                expected_hash = authoritative_state_hash(engine.state)
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / f"token-attachment-{seed}"
                    session.save(directory)
                    replay = replay_record(directory, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_attachment_action_compiler_and_runtime_mutants_are_killed(self):
        fixture = record(
            "Mutation Harness",
            "When this Equipment enters, attach it to target creature you control.",
        )

        def assert_compiles() -> None:
            compiled = compile_oracle_card(
                fixture,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertTrue(compiled.faces[0].nodes[0].exact)

        assert_compiles()
        with mock.patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_source_attachment_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiles()

        def assert_runtime_attaches(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name="Entry Harness Fixture",
                ref=f"mutation-source-{seed}",
                zone="battlefield",
            )
            target = self._battlefield_creature(
                engine, "A", "Mishra, Eminent One"
            )
            engine.apply_effect(
                {
                    "op": "attach",
                    "attachment_kind": "equipment",
                    "source": source.ref,
                    "target": target.ref,
                },
                actor="A",
            )
            self.assertEqual(target.object_id, source.attached_to)

        assert_runtime_attaches(7013017)
        def omit_attachment(cards, source, target, *, source_timestamp, players):
            return AttachmentTransition(source.object_id, source.attached_to,
                source.attached_to, False, source_timestamp)

        with mock.patch(
            "quorune.effect_runtime.zones_and_attachments.attach_objects",
            side_effect=omit_attachment,
        ):
            with self.assertRaises(AssertionError):
                assert_runtime_attaches(7013018)


if __name__ == "__main__":
    unittest.main()
