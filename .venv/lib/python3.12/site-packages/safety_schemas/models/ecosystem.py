from pydantic.dataclasses import dataclass


@dataclass
class EcosystemIgnoreConfigModel:
    pass


@dataclass
class PythonEcosystemIgnoreConfigModel(EcosystemIgnoreConfigModel):
    unpinned_specifications: bool = True
    environment_results: bool = True
