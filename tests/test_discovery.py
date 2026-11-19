"""The Verifiers loader must resolve this environment's ID to its Taskset.

``vf-run``, ``vf-validate`` and the Hub all find an environment the same way: the
ID ``neuro-grounded-qa`` becomes the module ``neuro_grounded_qa``, which must export
exactly one ``Taskset`` subclass in ``__all__``. If this test fails, no CLI can load
the environment.
"""

import verifiers.v1 as vf
from verifiers.v1.utils.loaders import taskset_class

import neuro_grounded_qa
from neuro_grounded_qa.taskset import NeuroGroundedQATaskset

ENV_ID = "neuro-grounded-qa"


def test_loader_resolves_env_id_to_taskset():
    assert taskset_class(ENV_ID) is NeuroGroundedQATaskset


def test_package_exports_exactly_one_taskset():
    exported = [getattr(neuro_grounded_qa, name) for name in neuro_grounded_qa.__all__]
    tasksets = [obj for obj in exported if isinstance(obj, type) and issubclass(obj, vf.Taskset)]
    assert tasksets == [NeuroGroundedQATaskset]
    assert neuro_grounded_qa.__all__ == ["NeuroGroundedQATaskset"]


def test_stub_loads_one_placeholder_task():
    taskset = NeuroGroundedQATaskset(vf.TasksetConfig())
    tasks = list(taskset)
    assert len(tasks) == 1
    assert tasks[0].data.id == "placeholder-0"
