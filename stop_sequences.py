# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung

import json


def parse_stop_sequences(value):
    if value.lstrip().startswith("["):
        try:
            sequences = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            if not all(isinstance(sequence, str) for sequence in sequences):
                raise ValueError("stop_sequences JSON array must contain only strings.")
            return [sequence for sequence in sequences if sequence]
    return [line for line in value.splitlines() if line]
