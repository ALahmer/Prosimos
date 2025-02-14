# user-defined exceptions

class Error(Exception):
    """Base class for other exceptions"""
    pass


class InvalidBpmnModelException(Error):
    """Raised when the provided BPMN model is invalid"""


class InvalidLogFileException(Error):
    """Raised when the provided log file is invalid"""

class InvalidRuleDefinition(Error):
    """Raised when the defined rule is invalid"""

class InvalidCaseAttributeException(Error):
    """Raised when the defined case attribute is invalid"""