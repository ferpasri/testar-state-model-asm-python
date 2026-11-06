"""Stable ASM error codes for integrations and shared conformance runners."""


class AsmError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise AsmError(code, message)
