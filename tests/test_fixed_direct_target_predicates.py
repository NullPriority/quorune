from __future__ import annotations

from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.destruction_templates import (
    targeted_destruction_effect_template,
)
from quorune.compiler.direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from quorune.compiler.exile_templates import targeted_exile_effect_template
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import StackItem, CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


def record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Instant",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Fixed Direct Target {suffix}",
        mana_cost="{1}{B}",
        mana_value=2.0,
        oracle_text=text,
        type_line=type_line,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedDirectTargetCompilerTests(unittest.TestCase):
    def test_token_status_keeps_card_type_and_public_state_independent(self):
        cases = (
            ("target token you control", (), "you", False),
            ("target artifact token", ("artifact",), "any", False),
            ("target creature token", ("creature",), "any", False),
            ("target token an opponent controls that entered this turn", (), "opponent", True),
        )
        for text, types, controller, entered_this_turn in cases:
            with self.subTest(text=text):
                spec = direct_permanent_target_spec(text)
                self.assertIsNotNone(spec)
                self.assertIs(spec.token, True)
                self.assertEqual(types, spec.types_any)
                self.assertEqual(controller, spec.controller_relation)
                self.assertEqual(entered_this_turn, bool(spec.state_predicate and spec.state_predicate.entered_this_turn))
                self.assertEqual(spec, DirectPermanentTargetSpec.from_target_schema(spec.to_target_schema()))
        self.assertIsNone(direct_permanent_target_spec("target token created by an opponent"))
        self.assertIsNone(direct_permanent_target_spec("target nontoken token"))
        with self.assertRaises(ValueError):
            DirectPermanentTargetSpec(token=1)

    def test_control_and_keyword_subject_orders_share_one_target_contract(self):
        for control in ('you control','an opponent controls',"you don't control"):
            for quality in ('with flying','without flying','with defender'):
                with self.subTest(control=control,quality=quality):
                    expected=direct_permanent_target_spec('target creature '+quality+' '+control)
                    actual=direct_permanent_target_spec('target creature '+control+' '+quality)
                    self.assertIsNotNone(expected);self.assertEqual(expected,actual)
        for subject in ('target creature you control with an unknown ability',
                'target creature you control without flying and vigilance',
                'target creature you control without flying except one'):
            self.assertIsNone(direct_permanent_target_spec(subject))

    def test_controlled_keyword_normalization_omission_is_killed(self):
        self.test_control_and_keyword_subject_orders_share_one_target_contract()
        with patch('quorune.compiler.direct_target._normalize_controlled_keyword_subject',side_effect=lambda value:value):
            result=unittest.TestResult()
            FixedDirectTargetCompilerTests('test_control_and_keyword_subject_orders_share_one_target_contract').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_pinned_noncreature_subtype_targets_preserve_bare_domain(self):
        for subtype in ('equipment','aura','shrine','island','mountain','spacecraft','treasure','room'):
            spec=direct_permanent_target_spec('target '+subtype)
            self.assertIsNotNone(spec,subtype)
            self.assertEqual((subtype,),spec.subtypes_any)
            self.assertEqual((),spec.types_all);self.assertEqual((),spec.types_any)
            self.assertEqual(spec,DirectPermanentTargetSpec.from_target_schema(spec.to_target_schema()))
        for text in ('target fictional-subtype','target creature with Equipment','target Equipment with an unknown mark'):
            self.assertIsNone(direct_permanent_target_spec(text))

    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, suffix: int, type_line: str = "Instant"):
        return compile_oracle_card(
            record(text, suffix=suffix, type_line=type_line),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_shared_characteristic_grammar_covers_the_bounded_shapes(self):
        cases = (
            ("target nonbasic land", {"types_any": ["land"], "supertypes_none": ["basic"]}),
            ("target creature without flying", {"types_all": ["creature"], "keywords_none": ["flying"]}),
            ("target creature with defender", {"types_all": ["creature"], "keywords_all": ["defender"]}),
            ("target creature with horsemanship", {"types_all": ["creature"], "keywords_all": ["horsemanship"]}),
            ("target creature with islandwalk", {"types_all": ["creature"], "keywords_all": ["islandwalk"]}),
            ("target creature with shadow", {"types_all": ["creature"], "keywords_all": ["shadow"]}),
            ("target green or white creature", {"types_any": ["creature"], "colors_any": ["G", "W"]}),
            ("target blue or black creature with flying", {"types_all": ["creature"], "colors_any": ["B", "U"], "keywords_all": ["flying"]}),
            ("target nonwhite permanent", {"colors_none": ["W"]}),
            ("target legendary creature", {"types_any": ["creature"], "supertypes_any": ["legendary"]}),
            ("target nonlegendary creature", {"types_any": ["creature"], "supertypes_none": ["legendary"]}),
            ("target snow land", {"types_any": ["land"], "supertypes_any": ["snow"]}),
            ("target nonsnow creature", {"types_any": ["creature"], "supertypes_none": ["snow"]}),
            ("target artifact creature", {"types_all": ["artifact", "creature"]}),
            ("target nonartifact creature", {"types_any": ["creature"], "types_none": ["artifact"]}),
            ("target nonenchantment creature", {"types_any": ["creature"], "types_none": ["enchantment"]}),
            ("target noncreature permanent", {"types_none": ["creature"]}),
            ("target noncreature artifact", {"types_any": ["artifact"], "types_none": ["creature"]}),
            (
                "target noncreature artifact or noncreature enchantment",
                {"types_any": ["artifact", "enchantment"], "types_none": ["creature"]},
            ),
            ("target creature token", {"types_any": ["creature"], "token": True}),
            ("target monocolored permanent", {"color_count_equal": 1}),
            ("target monocolored creature", {"types_any": ["creature"], "color_count_equal": 1}),
            (
                "target multicolored creature or multicolored enchantment",
                {"types_any": ["creature", "enchantment"], "color_count_min": 2},
            ),
            ("target permanent that's one or more colors", {"color_count_min": 1}),
            (
                "target non-outlaw creature",
                {
                    "types_any": ["creature"],
                    "subtypes_none": ["assassin", "mercenary", "pirate", "rogue", "warlock"],
                },
            ),
            (
                "target legendary permanent that's an artifact, creature, or enchantment",
                {
                    "types_any": ["artifact", "creature", "enchantment"],
                    "supertypes_any": ["legendary"],
                },
            ),
            (
                "target untapped creature an opponent controls",
                {
                    "types_any": ["creature"],
                    "controller_relation": "opponent",
                    "state_predicate": {
                        "entered_this_turn": False,
                        "tapped": False,
                        "counter_name": None,
                        "minimum_counter_count": None,
                    },
                },
            ),
            (
                "target attacking creature",
                {
                    "types_any": ["creature"],
                    "combat_state": "attacking",
                },
            ),
            (
                "target blocking creature you control",
                {
                    "types_any": ["creature"],
                    "controller_relation": "you",
                    "combat_state": "blocking",
                },
            ),
            (
                "target attacking or blocking creature",
                {
                    "types_any": ["creature"],
                    "combat_state": "attacking_or_blocking",
                },
            ),
            (
                "target attacking creature with flying",
                {
                    "types_all": ["creature"],
                    "keywords_all": ["flying"],
                    "combat_state": "attacking",
                },
            ),
        )
        header = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "count": 1,
        }
        for subject, expected in cases:
            with self.subTest(subject=subject):
                spec = direct_permanent_target_spec(subject)
                self.assertIsNotNone(spec)
                assert spec is not None
                schema = spec.to_target_schema()
                self.assertEqual({**header, **expected}, schema)
                self.assertEqual(
                    spec,
                    DirectPermanentTargetSpec.from_target_schema(schema),
                )
                self.assertTrue(
                    spec.uses_compound_characteristics or spec.uses_public_state
                )

    def test_shared_grammar_compiles_across_effect_owners_and_contexts(self):
        cases = (
            (
                "Destroy target non-outlaw creature.",
                "Instant",
                "spell_ability",
                "destroy-target-creature-non-assassin-and-mercenary-and-pirate-and-rogue-and-warlock-v2",
            ),
            (
                "{T}: Destroy target legendary permanent that's an artifact, creature, or enchantment.",
                "Creature — Human Soldier",
                "activated_ability",
                "destroy-target-legendary-artifact-or-creature-or-enchantment-v2",
            ),
            (
                "Exile target multicolored creature or multicolored enchantment.",
                "Sorcery",
                "spell_ability",
                "exile-target-creature-or-enchantment-at-least-2-colors-v2",
            ),
            (
                "{1}, {T}: Tap target nonenchantment creature.",
                "Artifact Creature — Construct",
                "activated_ability",
                "tap-target-creature-non-enchantment-v3",
            ),
            (
                "{1}, {T}: Untap target legendary creature you control.",
                "Artifact",
                "activated_ability",
                "untap-target-legendary-creature-you-v3",
            ),
            (
                "Destroy target attacking creature.",
                "Instant",
                "spell_ability",
                "destroy-target-creature-attacking-v2",
            ),
            (
                "{T}: This creature deals 1 damage to target attacking or blocking creature.",
                "Creature — Archer",
                "activated_ability",
                "damage-creature-attacking-or-blocking-v1",
            ),
            (
                "Exile target blocking creature.",
                "Instant",
                "spell_ability",
                "exile-target-creature-blocking-v2",
            ),
            (
                "Return target attacking creature to its owner's hand.",
                "Instant",
                "spell_ability",
                "return-target-creature-attacking-v2",
            ),
            (
                "Target blocking creature gets +2/+2 until end of turn.",
                "Instant",
                "spell_ability",
                "fixed-target-characteristics-until-end-of-turn-v1",
            ),
        )
        for suffix, (text, type_line, kind, template_id) in enumerate(cases, 1):
            with self.subTest(text=text):
                ir = self.compile(text, suffix=suffix, type_line=type_line)
                self.assertEqual("exact", ir.status)
                self.assertFalse(ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertIn(
                    (
                        "state_query.permanent.public_state_predicate"
                        if "attacking" in text or "blocking" in text
                        else "target.permanent.characteristic_predicate"
                    ),
                    node.capability_dependencies,
                )

    def test_scoped_dynamic_and_ability_presence_predicates_fail_closed(self):
        unsupported = (
            "target artifact, enchantment, or creature with flying",
            "target artifact creature or black creature",
            "target creature with power X or greater",
            "target creature that was dealt damage by a Goblin this turn",
            "target creature with a counter on it that entered last turn",
            "target creature that has an activated ability with {T} in its cost",
            "target permanent with the same name as another permanent",
        )
        for subject in unsupported:
            with self.subTest(subject=subject):
                self.assertIsNone(direct_permanent_target_spec(subject))

        any_counter = direct_permanent_target_spec("target creature with a counter on it")
        self.assertIsNotNone(any_counter)
        self.assertEqual(1, any_counter.state_predicate.minimum_total_counter_count)

        valid = direct_permanent_target_spec("target monocolored creature")
        assert valid is not None
        schema = valid.to_target_schema()
        malformed = (
            {**schema, "color_count_min": 2},
            {**schema, "color_count_equal": True},
            {**schema, "color_count_equal": 6},
            {**schema, "keywords_none": ["vigilance"]},
            {**schema, "supertypes_none": ["world"]},
            {**schema, "combat_state": "combatant"},
            {**schema, "combat_state": True},
            {**schema, "combat_state": ["attacking"]},
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    DirectPermanentTargetSpec.from_target_schema(value)
        with self.assertRaises(ValueError):
            TargetGroup.from_mapping({**schema, "color_count_equal": 1.0})

    def test_shared_parser_mutation_demotes_each_effect_owner(self):
        cases = (
            "Destroy target nonlegendary creature.",
            "Destroy target token an opponent controls.",
            "Exile target monocolored permanent.",
            "{1}, {T}: Tap target nonenchantment creature.",
        )
        for suffix, text in enumerate(cases, 101):
            with self.subTest(text=text):
                self.assertEqual("exact", self.compile(text, suffix=suffix).status)
                with patch(
                    "quorune.compiler.direct_target._closed_direct_characteristic_fields",
                    return_value=None,
                ):
                    self.assertNotEqual(
                        "exact",
                        self.compile(text, suffix=suffix).status,
                    )


class FixedDirectTargetRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-direct-target.sqlite3"
        build_fixture_database(
            [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
            database,
        )
        cls.db = CardDatabase(database)
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

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_current_effective_color_count_governs_offer_command_and_resolution(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=613_105_001,
        )
        keep_all(session)
        engine = session.engine
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Mishra, Eminent One"
        )
        engine.move_card(target.object_id, "battlefield", controller="A")
        template = targeted_exile_effect_template(
            "Exile target multicolored creature or multicolored enchantment."
        )
        assert template is not None
        schema = dict(template.target_schema)
        public = engine._public_target_schema("B", schema, source_ref=None)
        self.assertIsNotNone(public)
        assert public is not None
        self.assertIn(target.ref, public["legal_refs"])
        selected, grouped = engine._validate_semantic_targets(
            "B",
            None,
            [target.ref],
            source_ref=None,
            target_schema=schema,
        )

        program = SemanticProgram(
            key="fixture:fixed-direct-color-count",
            label="Fixed direct color count",
            effects=[dict(template.effects[0])],
            target_schema=schema,
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-direct-color-count",
            ref="S-fixed-direct-color-count",
            kind="triggered_ability",
            controller="B",
            label=program.label,
            semantic_key=program.key,
            targets=selected,
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    target.ref: engine._target_snapshot(target.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)

        target.annotations["copy_overrides"] = {"colors": ["B"]}
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "B",
                None,
                [target.ref],
                source_ref=None,
                target_schema=schema,
            )
        self.assertFalse(engine._revalidate_resolution_targets(item))
        self.assertNotIn(item, engine.state.stack)
        self.assertEqual("battlefield", target.zone)

        target.annotations.pop("copy_overrides")
        outlaw = targeted_destruction_effect_template(
            "Destroy target non-outlaw creature."
        )
        assert outlaw is not None
        outlaw_group = TargetGroup.from_mapping(outlaw.target_schema)
        self.assertIn(
            target.ref,
            engine._target_candidates("B", outlaw_group, source_ref=None),
        )
        target.annotations["continuous_add_subtypes"] = ["Pirate"]
        self.assertNotIn(
            target.ref,
            engine._target_candidates("B", outlaw_group, source_ref=None),
        )

    def test_combat_state_target_offers_and_submission_share_one_or_legality(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=613_105_003,
        )
        keep_all(session)
        engine = session.engine
        creatures = [
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and "Creature" in self.db.lookup(card.printed_name).type_line
        ][:3]
        self.assertEqual(3, len(creatures))
        attacker, blocker, bystander = creatures
        for card in creatures:
            engine.move_card(card.object_id, "battlefield", controller="A")
        attacker.attacking = "B"
        blocker.blocking = attacker.object_id
        engine.state.combat = CombatState(
            attackers={attacker.object_id:'B'},
            blockers={attacker.object_id:[blocker.object_id]},
            attackers_declared=True,blockers_declared=True)

        expected = {
            "attacking": {attacker.ref},
            "blocking": {blocker.ref},
            "attacking_or_blocking": {attacker.ref, blocker.ref},
            "attacking_actor": {attacker.ref},
        }
        for combat_state, legal in expected.items():
            with self.subTest(combat_state=combat_state):
                spec = DirectPermanentTargetSpec(
                    types_any=("creature",),
                    combat_state=combat_state,
                )
                schema = spec.to_target_schema()
                public = engine._public_target_schema(
                    "B", schema, source_ref=None
                )
                self.assertIsNotNone(public)
                assert public is not None
                offered = set(public["legal_refs"])
                self.assertTrue(legal.issubset(offered))
                self.assertNotIn(bystander.ref, offered)
                for ref in legal:
                    selected, _grouped = engine._validate_semantic_targets(
                        "B",
                        None,
                        [ref],
                        source_ref=None,
                        target_schema=schema,
                    )
                    self.assertEqual([ref], selected)
                with self.assertRaises(GameRuleError):
                    engine._validate_semantic_targets(
                        "B",
                        None,
                        [bystander.ref],
                        source_ref=None,
                        target_schema=schema,
                    )

        relative_cases = (
            ("blocking_source", attacker.ref, blocker.ref),
            ("blocked_by_source", blocker.ref, attacker.ref),
        )
        for combat_state, source_ref, legal_ref in relative_cases:
            with self.subTest(combat_state=combat_state):
                schema = DirectPermanentTargetSpec(
                    types_any=("creature",),
                    combat_state=combat_state,
                ).to_target_schema()
                public = engine._public_target_schema(
                    "B", schema, source_ref=source_ref
                )
                self.assertIsNotNone(public)
                assert public is not None
                self.assertIn(legal_ref, public["legal_refs"])
                self.assertNotIn(bystander.ref, public["legal_refs"])
                selected, _grouped = engine._validate_semantic_targets(
                    "B",
                    None,
                    [legal_ref],
                    source_ref=source_ref,
                    target_schema=schema,
                )
                self.assertEqual([legal_ref], selected)

    def test_characteristic_target_exile_uses_destination_replacement(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=613_105_002,
        )
        keep_all(session)
        engine = session.engine
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Arcane Signet"
        )
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Zimone and Dina"
        )
        engine.move_card(target.object_id, "battlefield", controller="A")
        target.annotations["copy_overrides"] = {"colors": ["U", "B"]}
        engine.move_card(source.object_id, "battlefield", controller="B")

        template = targeted_exile_effect_template(
            "Exile target multicolored permanent."
        )
        self.assertIsNotNone(template)
        assert template is not None
        schema = dict(template.target_schema)
        selected, grouped = engine._validate_semantic_targets(
            "B",
            None,
            [target.ref],
            source_ref=None,
            target_schema=schema,
        )
        engine.semantics.put(
            SemanticProgram(
                key="fixture:characteristic-target-exile-replacement",
                label="Replace characteristic-target exile destination",
                oracle_id=source.oracle_id,
                ability_id="static:front:characteristic-target-exile",
                active_zone="battlefield",
                event="zone.change",
                trust_level="provisional",
                handlers=[
                    {
                        "handler_id": "replacement.zone.destination.v1",
                        "schema_version": 1,
                        "event": "zone.change",
                        "condition": {
                            "destination": "exile",
                            "object_kind": "card",
                            "owner_relation": "opponent",
                        },
                        "destination": "graveyard",
                        "counters": {"exile-replacement": 1},
                    }
                ],
            )
        )
        item = StackItem(
            stack_id="characteristic-target-exile-replacement",
            ref="S-characteristic-target-exile-replacement",
            kind="triggered_ability",
            controller="B",
            label="Characteristic-target exile replacement",
            targets=selected,
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    target.ref: engine._target_snapshot(target.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_resolve_item(
                item,
                template.effects,
                None,
                note="Characteristic-target destination replacement witness",
            )

        self.assertEqual("graveyard", target.zone)
        self.assertEqual(1, target.counters["exile-replacement"])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "permanent.exile"
        )
        self.assertEqual("graveyard", event.details["destination"])
        self.assertEqual("exile", event.details["requested_destination"])


if __name__ == "__main__":
    unittest.main()


class NoncreatureSubtypeTargetRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition,DeckEntry
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'subtype-target.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/noncreature-subtype-targets.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Subtype target',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Witnesses
    session=_Witnesses.session
    add=_Witnesses.add
    ready=_Witnesses.ready
    checkpoint=_Witnesses.checkpoint
    replay=_Witnesses.replay
    resolve=_Witnesses.resolve

    def test_original_acid_web_spider_targets_only_current_equipment_then_optional_destruction_and_replay(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s=self.session(363001);e=s.engine;source=self.add(e,'Acid Web Spider',zone='hand')
        target=self.add(e,'Manriki-Gusari',seat='B');nonmatch=self.add(e,'Sol Ring',seat='B')
        p=compile_best_available_card_program(self.db,self.db.lookup('Acid Web Spider'),semantic_registry=e.semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        action=self.ready(s,source,{'G':2,'C':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(s);self.assertEqual('semantic.target',decision.kind)
        before=e.state.to_dict();denied=s.act('pilot:A',{'action_id':'choose','targets':[nonmatch.ref]})
        self.assertFalse(denied.ok);self.assertEqual(before,e.state.to_dict())
        denied=s.act('pilot:C',{'action_id':'choose','targets':[target.ref]});self.assertFalse(denied.ok);self.assertEqual(before,e.state.to_dict())
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind);self.replay(s,load=True)
        accepted=s.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('graveyard',e.state.cards[target.object_id].zone);self.assertEqual('battlefield',e.state.cards[nonmatch.object_id].zone)
        projection=json.dumps(s.projector._snapshot('pilot:D'),sort_keys=True)
        for seat in 'ABC':
            for oid in e.state.players[seat].zones['hand']:self.assertNotIn(e.state.cards[oid].ref,projection)
        self.replay(s,load=True)

    def test_original_exonerator_aura_target_sacrifices_source_and_replays(self):
        from quorune.attachments import attach_objects
        s=self.session(363002);e=s.engine;source=self.add(e,'Haazda Exonerator');source.acquired_control_turn_count=-1
        target=self.add(e,'Holy Strength',seat='B');body=self.add(e,'Grizzly Bears',seat='B')
        attach_objects(e.state.cards,target,body,source_timestamp=e._next_zone_timestamp())
        nonmatch=self.add(e,'Manriki-Gusari',seat='C');action=self.ready(s,source,{})
        self.checkpoint(s);before=e.state.to_dict();denied=s.act('pilot:A',{'action_id':action['id'],'targets':[nonmatch.ref]})
        self.assertFalse(denied.ok);self.assertEqual(before,e.state.to_dict())
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',e.state.cards[source.object_id].zone);self.resolve(s)
        self.assertEqual('graveyard',e.state.cards[target.object_id].zone);self.assertEqual('battlefield',e.state.cards[body.object_id].zone)
        self.replay(s,load=True)

    def test_original_flash_flood_mountain_target_preserves_modal_predicate_and_replay(self):
        s=self.session(363003);e=s.engine;source=self.add(e,'Flash Flood',zone='hand')
        island=self.add(e,'Mountain',seat='B');forest=self.add(e,'Forest',seat='B')
        action=self.ready(s,source,{'U':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'modes':['mode_2'],'targets':{'target_0':[island.ref]},'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('hand',e.state.cards[island.object_id].zone);self.assertEqual('battlefield',e.state.cards[forest.object_id].zone)
        self.replay(s,load=True)

    def test_original_spider_selected_equipment_departure_invalidates_trigger_target(self):
        s=self.session(363004);e=s.engine;source=self.add(e,'Acid Web Spider',zone='hand')
        target=self.add(e,'Manriki-Gusari',seat='B');bounce=self.add(e,'Disperse',zone='hand')
        action=self.ready(s,source,{'G':2,'C':4,'U':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(s);accepted=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary)
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==bounce.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(s);self.assertEqual('hand',e.state.cards[target.object_id].zone)
        self.assertFalse(e.state.stack);self.replay(s,load=True)

    def test_noncreature_subtype_vocabulary_omission_mutant_is_killed(self):
        from quorune.compiler import direct_target
        with patch.object(direct_target,'DIRECT_NONCREATURE_SUBTYPES',frozenset({'forest','gate','vehicle'})):
            with self.assertRaises(AssertionError):
                self.test_original_acid_web_spider_targets_only_current_equipment_then_optional_destruction_and_replay()


class DirectTokenTargetRuntimeTests(unittest.TestCase):
    from test_life_gain_prohibitions import LifeGainProhibitionRuntimeTests as _Witnesses

    register_fixture_cards_on_add = True
    session = _Witnesses.session
    add = _Witnesses.add
    prepare = _Witnesses.prepare
    checkpoint = _Witnesses.checkpoint
    resolve = _Witnesses.resolve
    replay = _Witnesses.replay
    resume = _Witnesses.resume
    priority = _Witnesses.priority
    actions = _Witnesses.actions
    binding = _Witnesses.binding
    reject = _Witnesses.reject
    def pay_cast(self, session, source, targets=(), *, payment, seat="A", **fields):
        from test_mixed_permanent_targets import MixedPermanentTargetRuntimeTests

        MixedPermanentTargetRuntimeTests.pay_cast(
            self, session, source, targets, payment=payment, seat=seat, **fields,
        )

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "direct-token-targets.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "direct-token-target-cards.json",
            "copied-aura-token-entry-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Direct token targets", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                  DeckEntry("Generic Bound Plains", 20)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _choose(self, session, **fields):
        result = session.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def _trigger_targets(self, session):
        self.assertEqual("semantic.target", self.resolve(session).kind)
        return session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]["legal_refs"]

    def test_actual_kraul_offers_opposing_artifact_and_creature_tokens(self):
        session = self.session(745101)
        engine = session.engine
        source = self.add(engine, "Kraul Whipcracker", zone="hand")
        nontoken = self.add(engine, "Generic Bound Body", seat="B")
        own, = engine.create_token("A", name="Clue")
        opposing, = engine.create_token("B", name="Clue")
        creature, = engine.create_token("C", name="Goblin", characteristics={
            "type_line": "Token Creature — Goblin", "power": "1", "toughness": "1", "colors": ["R"],
        })
        victim = engine._resolve_object("B", opposing, zones={"battlefield"})
        private = session.state.cards[session.state.players["D"].zones["hand"][0]]
        self.prepare(session, mana={"B": 1, "G": 1})
        self.checkpoint(session)
        self.pay_cast(session, source, payment={"B": 1, "G": 1})
        self.assertEqual({opposing, creature}, set(self._trigger_targets(session)))
        self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        for ref in (own, nontoken.ref):
            self.reject(session, {"action_id": "choose", "targets": [ref]}, seat="A")
        self.reject(session, {"action_id": "choose", "targets": [opposing]}, seat="B")
        session = self.resume(session)
        self._choose(session, targets=[opposing])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("outside", session.state.cards[victim.object_id].zone)
        self.assertTrue(session.engine._resolve_object("A", own, zones={"battlefield"}).is_token)
        self.assertTrue(session.engine._resolve_object("C", creature, zones={"battlefield"}).is_token)
        self.replay(session, load=True)

    def test_actual_specimen_collector_copies_a_controlled_aura_token(self):
        session = self.session(745102)
        engine = session.engine
        collector = self.add(engine, "Specimen Collector")
        rites = self.add(engine, "Village Rites", zone="hand")
        aura = self.add(engine, "Mind Control", zone="hand")
        original_host = self.add(engine, "Generic Bound Body", seat="B", ref="original-aura-token-host")
        new_host = self.add(engine, "Generic Bound Body", seat="C", ref="new-aura-token-host")
        own_host = self.add(engine, "Generic Bound Body", ref="ordinary-aura-host")
        engine.move_card(aura.object_id, "battlefield", aura_target_ref=own_host.ref)
        ref, = engine.create_token("B", name="", copy_of=aura.ref, aura_target_ref=original_host.ref)
        original = engine._resolve_object("B", ref, zones={"battlefield"})
        engine.change_control(original.object_id, "A", reason="fixture custody")
        self.assertEqual(("B", "A"), (original.owner, original.controller))
        opposing, = engine.create_token("C", name="Clue")

        self.assertTrue(self.binding(session, collector)["strict_capability_ready"])
        self.prepare(session, mana={"B": 1})
        self.checkpoint(session)
        self.pay_cast(session, rites, payment={"B": 1}, sacrifice_cards=[collector.ref])
        self.assertEqual("graveyard", session.state.cards[collector.object_id].zone)
        self.assertEqual([ref], self._trigger_targets(session))
        for wrong in (aura.ref, opposing):
            self.reject(session, {"action_id": "choose", "targets": [wrong]}, seat="A")
        self._choose(session, targets=[ref])
        self.assertEqual("aura.entry", self.resolve(session).kind)
        self.assertEqual([ref], session.state.stack[-1].targets)
        self.assertEqual(2, sum(card.is_token for card in session.state.cards.values()))
        session = self.resume(session)
        self._choose(session, aura_target=new_host.ref)
        self.assertIsNone(self.resolve(session))
        copies = [card for card in session.state.cards.values()
                  if card.is_token and session.engine.display_name(card.object_id) == "Mind Control"]
        self.assertEqual(2, len(copies))
        copied, = [card for card in copies if card.object_id != original.object_id]
        self.assertEqual(("A", "A", new_host.object_id), (copied.owner, copied.controller, copied.attached_to))
        self.assertEqual(original_host.object_id, session.state.cards[original.object_id].attached_to)
        self.assertEqual("A", session.state.cards[new_host.object_id].controller)
        self.replay(session, load=True)

    def test_actual_romana_uses_current_turn_token_and_revalidates_at_resolution(self):
        from test_phasing import PhasingRuntimeTests

        session = self.session(745103)
        engine = session.engine
        source = self.add(engine, "Romana II")
        bounce = self.add(engine, "Disperse", zone="hand")
        older, = engine.create_token("B", name="Clue")
        PhasingRuntimeTests._stage_end_step(self, session, "D")
        PhasingRuntimeTests._pass_end_step(self, session, "A")
        fresh, = engine.create_token("B", name="Clue")
        target = engine._resolve_object("B", fresh, zones={"battlefield"})
        target.counters["charge"] = 1
        self.assertTrue(self.binding(session, source)["strict_capability_ready"])
        self.prepare(session, mana={"C": 2, "U": 1})
        self.checkpoint(session)
        action, = [row for row in self.actions(session) if row["id"].startswith(f"activate:{source.ref}:")]
        self.assertEqual([fresh], action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [fresh], "pay": "manual", "payment": {"C": 1}}
        self.reject(session, {**command, "targets": [older]}, seat="A")
        activated = session.act("pilot:A", command)
        self.assertTrue(activated.ok, activated.summary)
        valid = self.resume(session)
        self.assertIsNone(self.resolve(valid))
        copied, = [card for card in valid.state.cards.values() if card.is_token and card.owner == "A"]
        self.assertEqual("Clue", valid.engine.display_name(copied.object_id))
        self.assertTrue(copied.tapped)
        self.assertEqual({}, copied.counters)
        self.replay(valid, load=True)

        self.pay_cast(session, bounce, [fresh], payment={"C": 1, "U": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual("outside", session.state.cards[target.object_id].zone)
        self.assertFalse(any(card.is_token and card.owner == "A" for card in session.state.cards.values()))
        self.replay(session, load=True)
