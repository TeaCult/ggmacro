"""Three-tier desktop control. Desktop backends are loaded only on request."""
from .local_decider import ActionGate, Decision, DecisionError, LocalDecider, SupervisorDecision
from .runtime import MacroLoom, SupervisedMacroRunner

__all__ = [
    'ActionGate', 'Decision', 'DecisionError', 'LocalDecider',
    'SupervisorDecision', 'MacroLoom', 'SupervisedMacroRunner',
]
