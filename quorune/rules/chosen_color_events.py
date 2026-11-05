"""Dependencies for event-time reads of an object's public color designation."""
from collections.abc import Mapping


CHOSEN_COLOR_CAST_CAPABILITY = 'trigger.event.spell_cast_chosen_color'


def chosen_color_cast_dependencies(program):
    """Infer the choice reader independently of compiler-provided dependencies."""
    def uses_color(value):
        if isinstance(value, Mapping):
            return value.get('field') == 'source_annotation.chosen_color' or any(
                uses_color(child) for child in value.values())
        return isinstance(value, (list, tuple)) and any(uses_color(child) for child in value)

    return (CHOSEN_COLOR_CAST_CAPABILITY,) if program.event == 'spell.cast' and uses_color(
        program.event_condition) else ()
