OS_TYPE_DESC: str = (
    "SAFETY_OS_TYPE or platform.system() value, SAFETY_OS_TYPE has "
    "higher priority, it can be empty."
)
OS_RELEASE_DESC: str = (
    "SAFETY_OS_RELEASE or platform.release() value, "
    "SAFETY_OS_RELEASE has higher priority, it can be empty."
)
OS_DESCRIPTION_DESC: str = (
    "SAFETY_OS_DESCRIPTION or platform.platform() value, "
    "SAFETY_OS_DESCRIPTION has higher priority."
)
PYTHON_VERSION_DESC: str = (
    "The output of call platform.python_version(), " "major.minor.patchlevel value."
)
SAFETY_COMMAND_DESC: str = "The command used in Safety CLI."
SAFETY_OPTIONS_DESC: str = (
    "Logs the options used and the flag names used and how "
    "many times are used. Useful to understand the impact of a deprecation."
)
SAFETY_VERSION_DESC: str = "A PEP 440 valid version of Safety CLI."
SAFETY_SOURCE_DESC: str = "What is the source of the safety use."

BRANCH_DESC: str = (
    "The current branch name inside of the directory " "where was found git."
)
TAG_DESC: str = "If the current status is a tag, this represents the tag."
