
from pydantic.version import VERSION as PYDANTIC_VERSION

def dict_dump(obj):
    if PYDANTIC_VERSION.startswith("1."):
        return obj.dict()

    return obj.model_dump()
