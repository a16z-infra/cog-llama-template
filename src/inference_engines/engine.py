from abc import ABC, abstractmethod
from typing import Any


class Engine(ABC):
    """
    WIP - this is what the engine looks like at the moment, outlining this just as an exercise to see what our ABC looks like. It will change.
    """

    @abstractmethod
    def load_lora(self, lora_data: dict):
        """
        loads a lora from files into the format that this particular engine expects. DOES NOT prepare the engine for inference.
        lora_data is a dictionary of file names & references from the zip file
        """
        pass

    @abstractmethod
    def set_lora(self, lora: Any):
        """
        given a loaded lora (created w/load_lora), configures the engine to use that lora in combination with the loaded base weights.
        """
        pass

    @abstractmethod
    def delete_lora(self):
        """
        Deletes a LoRA.
        """
        pass

    @abstractmethod
    def is_lora_active(self) -> bool:
        """
        Checks whether a LoRA has currently been loaded onto the engine.
        """
        pass

    @abstractmethod
    def __call__(self, prompt, **kwargs):
        """
        generation!
        """
        pass
