"""Source lowering for one optional cast-linked creature-copy price."""

from ..rules.paid_sacrifice_copy import TEMPLATE, cast_copy_cost_schema


_CLAUSE = (
    "as an additional cost to cast this spell, you may sacrifice one or more creatures. "
    "when you do, copy this spell for each creature sacrificed this way."
)


class PaidSacrificeCopyAdditionalCostTemplate:
    template_id = TEMPLATE

    @property
    def cost_schema(self):
        return cast_copy_cost_schema()


def paid_sacrifice_copy_cost_template(text):
    if " ".join(text.split()).casefold() == _CLAUSE:
        return PaidSacrificeCopyAdditionalCostTemplate()
    return None
