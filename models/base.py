"""
Abstract base model + model registry for the quant pipeline.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json, os


class BaseModel(ABC):
    """All models inherit from this."""

    name: str = "base"

    @abstractmethod
    def fit(self, data):
        ...

    @abstractmethod
    def predict(self, data):
        ...

    def save(self, directory="models/saved"):
        os.makedirs(directory, exist_ok=True)
        meta = {
            "model": self.name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        path = os.path.join(directory, f"{self.name}_meta.json")
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
        return path


class ModelRegistry:
    """Central registry of all models."""

    def __init__(self):
        self._models: dict[str, BaseModel] = {}

    def register(self, model: BaseModel):
        self._models[model.name] = model

    def get(self, name: str) -> BaseModel:
        return self._models[name]

    def all(self) -> dict[str, BaseModel]:
        return dict(self._models)

    def list_names(self) -> list[str]:
        return list(self._models.keys())


registry = ModelRegistry()
