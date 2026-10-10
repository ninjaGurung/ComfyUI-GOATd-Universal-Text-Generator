# Author: ninjaGurung
# GitHub: https://github.com/ninjaGurung

from .llm_node import GOATTextGeneratorSettings, GOATUniversalTextGenerator, UniversalLLM

NODE_CLASS_MAPPINGS = {
    "GOATUniversalTextGenerator": GOATUniversalTextGenerator,
    "GOATTextGeneratorSettings": GOATTextGeneratorSettings,
    "UniversalLLM": UniversalLLM,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "GOATUniversalTextGenerator": "GOAT'd Text Generator 🐐",
    "GOATTextGeneratorSettings": "GOAT'd Text Generator Settings 🐐",
    "UniversalLLM": "Universal LLM",
}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
