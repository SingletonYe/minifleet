"""MiniFleet: turn human intent into verified, production-grade software systems.

MiniFleet is a small, dependency-free orchestration engine modelled on the
"autonomous fleet" idea: an intent is compiled into a design, the design into a
task graph, the task graph is executed by isolated agent workers, and the result
is admitted to production only after passing machine-checkable verification
gates. Every claim in the final report is traceable to a gate and its evidence.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
