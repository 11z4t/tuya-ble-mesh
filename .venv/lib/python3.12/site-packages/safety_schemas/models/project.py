from dataclasses import asdict, field
from pathlib import Path
from typing import List, Optional, Union

from pydantic.dataclasses import dataclass
from typing_extensions import Self

from ..report.schemas.v3_0 import main as v3_0
from .base import SafetyBaseModel
from .file import FileModel
from .git import GITModel
from .policy_file import PolicyFileModel
from .util import dict_dump

@dataclass
class ProjectModel(SafetyBaseModel):
    id: str
    upload_request_id: Optional[str] = None
    project_path: Optional[Path] = None
    name: Optional[str] = None
    url_path: Optional[str] = None
    policy: Optional[PolicyFileModel] = None
    git: Optional[GITModel] = None
    files: List[FileModel] = field(default_factory=lambda: [])

    def as_v30(self, full: bool = True) -> Union[v3_0.Projects, v3_0.ProjectsScan]:
        if not full:
            if not self.upload_request_id:
                raise TypeError('upload_request_id is required when a single project is created')
            return v3_0.Projects(id=self.id, upload_request_id=self.upload_request_id)
        
        if not self.project_path:
            raise TypeError('project_path is required when a project scan is created')

        git_repr = v3_0.Git(**asdict(self.git)) if self.git else None
        policy = self.policy.as_v30() if self.policy else None
        location = str(self.project_path.resolve().parent)

        return v3_0.ProjectsScan(id=self.id, policy=policy, git=git_repr, location=location, files=[f.as_v30() for f in self.files])      

    @classmethod
    def from_v30(cls, obj: v3_0.SchemaModelV30) -> 'ProjectModel':

        if not isinstance(obj, v3_0.ProjectsScan) and not isinstance(obj, v3_0.Projects):
            raise TypeError('Expected instance of v3_0.ProjectsScan or v3_0.Projects')

        if isinstance(obj, v3_0.ProjectsScan):
            git_model_inst = None

            if obj.git:
                git_model_inst = GITModel(**dict_dump(obj.git))

            return ProjectModel(
                id=obj.id,
                project_path=Path(obj.location),
                upload_request_id=obj.upload_request_id,
                git=git_model_inst,
                files=[FileModel.from_v30(f) for f in obj.files],
            )

        return ProjectModel(id=obj.id, upload_request_id=obj.upload_request_id)
