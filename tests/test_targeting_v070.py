from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from common import keep_all, load_assets
from quorune import CommanderSession, GameConfig
from quorune.engine import GameRuleError
from quorune.model import StackItem
from quorune.record import checkpoint_envelope, replay_record
from quorune.semantics import SemanticProgram
from quorune.targets import TargetGroup


class ExactTargetingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int = 701) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {"A": self.zimone, "B": self.mishra},
            first_player="A",
            seed=seed,
            config=GameConfig(
                seed=seed,
                profile="commander_duel",
                auto_pass_empty_priority=False,
            ),
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        return session

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def put_spell_on_stack(
        self,
        engine,
        seat: str,
        name: str,
        *,
        ref: str,
    ) -> StackItem:
        card = self.card(engine, seat, name)
        engine._remove_from_zone(card)
        card.zone = "stack"
        card.controller = seat
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        item = StackItem(
            stack_id=f"test-{ref}",
            ref=ref,
            kind="spell",
            controller=seat,
            label=name,
            card_object_id=card.object_id,
            default_destination=(
                "battlefield"
                if self.db.lookup(name).is_permanent_spell
                else "graveyard"
            ),
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        return item

    def test_an_offer_empty_stack_and_treasure_resolution(self):
        session = self.make_session()
        engine = session.engine
        offer = self.card(engine, "A", "An Offer You Can't Refuse")
        birds = self.card(engine, "A", "Birds of Paradise")
        bloom = self.card(engine, "A", "Bloom Tender")
        for card in (birds, bloom):
            engine.move_card(card.object_id, "battlefield", controller="A")
        engine.move_card(offer.object_id, "hand")
        engine.state.players["A"].mana_pool["U"] = 1
        engine.state.priority_player = "A"

        empty_hints = engine._priority_action_hints("A")
        self.assertNotIn(offer.ref, empty_hints["cast"])
        self.assertTrue(
            any(
                item.get("card") == offer.ref
                and item.get("reason") == "mandatory_target_unavailable"
                for item in empty_hints["diagnostic"]["unpayable"]
            )
        )

        target = self.put_spell_on_stack(
            engine, "B", "Sol Ring", ref="S-target"
        )
        hints = engine._priority_action_hints("A")
        self.assertIn(offer.ref, hints["cast"])
        action = next(
            item
            for item in hints["actions"]
            if item.get("card") == offer.ref
        )
        self.assertEqual(
            [target.ref],
            action["target_schema"]["legal_refs"],
        )

        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"U": 1},
                "plan": "HOLD_INTERACTION",
                "reason": "Counter the exposed noncreature spell.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(target.ref, session.commands[-1]["payload"]["targets"][0])
        offer_stack_ref = engine.state.stack[-1].ref
        for _ in range(8):
            if not any(
                item.ref == offer_stack_ref for item in engine.state.stack
            ):
                break
            principal = session.pending_principals()[0]
            passed = session.act(
                principal,
                {
                    "action_id": "pass",
                    "plan": "HOLD_INTERACTION",
                    "reason": "Pass priority for deterministic resolution.",
                },
            )
            self.assertTrue(passed.ok, passed.summary)

        self.assertFalse(
            any(item.ref == target.ref for item in engine.state.stack)
        )
        treasures = [
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.zone == "battlefield"
            and card.printed_name == "Treasure"
        ]
        self.assertEqual(2, len(treasures))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "record"
            session.save(output)
            self.assertTrue(replay_record(output, self.db, verify=True)["ok"])

    def test_nonqualifying_creature_spell_does_not_enable_offer(self):
        session = self.make_session(seed=702)
        engine = session.engine
        offer = self.card(engine, "A", "An Offer You Can't Refuse")
        engine.move_card(offer.object_id, "hand")
        engine.state.players["A"].mana_pool["U"] = 1
        self.put_spell_on_stack(
            engine, "B", "Goblin Engineer", ref="S-creature"
        )
        engine.state.priority_player = "A"
        self.assertNotIn(offer.ref, engine._priority_action_hints("A")["cast"])

    def test_modes_reb_and_pyroblast_use_actual_target_rules(self):
        session = self.make_session(seed=703)
        engine = session.engine
        reb = self.card(engine, "B", "Red Elemental Blast")
        pyro = self.card(engine, "B", "Pyroblast")
        blue = self.card(engine, "A", "Zimone and Dina")
        nonblue = self.card(engine, "B", "Sol Ring")
        engine.move_card(reb.object_id, "hand")
        engine.move_card(pyro.object_id, "hand")
        engine.move_card(blue.object_id, "battlefield", controller="A")
        engine.move_card(nonblue.object_id, "battlefield", controller="B")
        engine.state.players["B"].mana_pool["R"] = 2
        engine.state.priority_player = "B"
        hints = engine._priority_action_hints("B")
        reb_action = next(
            action for action in hints["actions"] if action.get("card") == reb.ref
        )
        pyro_action = next(
            action
            for action in hints["actions"]
            if action.get("card") == pyro.ref
        )
        self.assertEqual(
            [blue.ref],
            reb_action["target_schema"]["mode_schemas"]["destroy"]["groups"][0][
                "legal_refs"
            ],
        )
        self.assertIn(
            nonblue.ref,
            pyro_action["target_schema"]["mode_schemas"]["destroy"]["groups"][0][
                "legal_refs"
            ],
        )

    def test_count_distinct_and_partial_resolution(self):
        session = self.make_session(seed=704)
        engine = session.engine
        first = self.card(engine, "B", "Sol Ring")
        second = self.card(engine, "B", "Panharmonicon")
        source = self.card(engine, "A", "Force of Vigor")
        for card in (first, second):
            engine.move_card(card.object_id, "battlefield", controller="B")
        engine._remove_from_zone(source)
        source.zone = "stack"
        source.controller = "A"
        program = SemanticProgram(
            key="test:up-to-two",
            label="Test up to two",
            oracle_id=source.oracle_id,
            ability_id="test:up-to-two-targets",
            effects=[
                {"op": "destroy", "card": "$target.0"},
                {"op": "destroy", "card": "$target.1"},
            ],
            destination="graveyard",
            target_schema={
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "artifact": True,
                "up_to": 2,
                "distinct": True,
            },
            trust_level="provisional",
        )
        engine.semantics.put(program)
        selected, grouped = engine._validate_semantic_targets(
            "A",
            program,
            [first.ref, second.ref],
            source_ref=source.ref,
        )
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                program,
                [first.ref, first.ref],
                source_ref=source.ref,
            )
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                program,
                [first.ref, second.ref, first.ref],
                source_ref=source.ref,
            )
        item = StackItem(
            stack_id="partial-target-test",
            ref="S-partial",
            kind="spell",
            controller="A",
            label=program.label,
            card_object_id=source.object_id,
            semantic_key=program.key,
            targets=selected,
            default_destination="graveyard",
            visibility=["A", "B"],
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    ref: engine._target_snapshot(ref) for ref in selected
                },
                "targets_revalidated": False,
            },
        )
        engine.state.stack.append(item)
        engine.move_card(first.object_id, "graveyard", reason="response")
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", second.zone)
        self.assertTrue(
            any(event.code == "target.illegal" for event in engine.state.events)
        )
        telemetry = engine.state.players["A"].stats["decision_optimization"]
        self.assertEqual(1, telemetry["targets_became_illegal_on_resolution"])

    def test_all_targets_illegal_counters_by_rules(self):
        session = self.make_session(seed=705)
        engine = session.engine
        offer = self.card(engine, "A", "An Offer You Can't Refuse")
        target = self.put_spell_on_stack(
            engine, "B", "Sol Ring", ref="S-doomed"
        )
        engine._remove_from_zone(offer)
        offer.zone = "stack"
        offer.controller = "A"
        program = engine.semantics.get(
            f"{offer.oracle_id}:spell:front"
        )
        selected, grouped = engine._validate_semantic_targets(
            "A", program, [target.ref], source_ref="S-offer"
        )
        item = StackItem(
            stack_id="offer-test",
            ref="S-offer",
            kind="spell",
            controller="A",
            label=offer.printed_name,
            card_object_id=offer.object_id,
            semantic_key=program.key,
            targets=selected,
            default_destination="graveyard",
            visibility=["A", "B"],
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    target.ref: engine._target_snapshot(target.ref)
                },
                "targets_revalidated": False,
            },
        )
        engine.state.stack.append(item)
        engine._counter_stack_item(
            target.ref,
            as_rule=True,
            countered_by="B",
            reason="test response",
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", offer.zone)
        self.assertTrue(
            any(
                event.code == "stack.counter"
                and event.details.get("stack") == item.ref
                and event.details.get("counter_kind") == "rules"
                for event in engine.state.events
            )
        )

    def test_hidden_zones_are_rejected_by_target_schema(self):
        with self.assertRaises(ValueError):
            TargetGroup.from_mapping(
                {
                    "zones": ["hand"],
                    "categories": ["card"],
                    "count": 1,
                }
            )


if __name__ == "__main__":
    unittest.main()
