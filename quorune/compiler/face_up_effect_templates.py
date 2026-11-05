"""One targeted face-down creature receives an ordinary face-up instruction."""
import re
from ..face_up_effect_model import FACE_UP_EFFECT_MECHANIC,face_up_instruction,face_up_target_schema


def targeted_face_up_template(text):
    match=re.fullmatch(r'Turn target face-down creature(?P<relation> an opponent controls| you control)? face up\.',text.strip(),re.I)
    if match is None:return None
    phrase=(match['relation'] or '').casefold()
    relation='opponent' if phrase==' an opponent controls' else 'you' if phrase else 'any'
    return ('fixed-target-face-up-v1',(face_up_instruction(),),face_up_target_schema(relation),
        (FACE_UP_EFFECT_MECHANIC,'cr-115-targets'))
