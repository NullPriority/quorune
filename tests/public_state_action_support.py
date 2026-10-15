from __future__ import annotations

import tempfile
from pathlib import Path
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PublicStateActionFixture:
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "additional-modifiers.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/additional-conditional-modifier-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Additional modifiers", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound

    session = _bound.BoundEffectProgramRuntimeTests.session

    add = _bound.BoundEffectProgramRuntimeTests.add

    ready = _bound.BoundEffectProgramRuntimeTests.ready

    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint

    resolve = _bound.BoundEffectProgramRuntimeTests.resolve

    replay = _bound.BoundEffectProgramRuntimeTests.replay

    def clear_initial_hand(self, session):
        for object_id in tuple(session.state.players["A"].zones["hand"]):
            session.engine.move_card(object_id, "library", log=False)

    def assert_whole_admission(self, name):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry

        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertTrue(bind_card_program_runtime(
            program, capability_registry=self.registry, profile="commander_review",
        )["strict_capability_ready"])
