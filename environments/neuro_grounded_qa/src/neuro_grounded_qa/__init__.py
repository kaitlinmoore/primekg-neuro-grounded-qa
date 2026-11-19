"""neuro-grounded-qa: a Verifiers v1 environment for evidence-grounded QA over PrimeKG.

The Verifiers loader turns the environment ID ``neuro-grounded-qa`` into the module
name ``neuro_grounded_qa`` (dashes to underscores), imports this package, and looks
in ``__all__`` for exactly one ``Taskset`` subclass. So ``__all__`` must export the
taskset and nothing else.
"""

from neuro_grounded_qa.taskset import NeuroGroundedQATaskset

__all__ = ["NeuroGroundedQATaskset"]
