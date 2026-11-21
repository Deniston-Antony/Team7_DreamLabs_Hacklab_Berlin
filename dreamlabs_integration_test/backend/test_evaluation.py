import tempfile
import unittest
from pathlib import Path

from evaluation import Evidence, TaskDefinition, evaluate_task, parse_instruction
from evaluation.batch import aggregate
from evaluation.models import ContactEvent, Criterion, Environment, ObjectState, Snapshot
from evaluation.reports import batch_html, task_html, task_report
from evaluation.store import EvaluationStore

IDENTITY = [1.0, 0.0, 0.0, 0.0]
TIPPED = [0.7071, 0.7071, 0.0, 0.0]
START = {"apple": [0.15, 0.10, 0.79], "bowl": [0.0, -0.04, 0.76], "cup": [-0.15, 0.10, 0.80]}
IDS = ["apple", "bowl", "cup"]


def item(name, position, orientation=IDENTITY):
    x, y, z = position
    half = 0.08 if name == "bowl" else 0.03
    return ObjectState(
        object_id=name,
        position=position,
        orientation_wxyz=orientation,
        movable=name != "bowl",
        aabb_min=[x - half, y - half, z - 0.03],
        aabb_max=[x + half, y + half, z + 0.03],
        speed_m_s=0.0,
    )


def snapshot(stage, changes=None):
    positions = {**START, **(changes or {})}
    environment = Environment(table_bounds=[-0.65, 0.65, -0.67, 0.43], table_z=0.76)
    return Snapshot(stage=stage, objects=[item(n, p) for n, p in positions.items()], environment=environment)


def task(instruction="put apple into the bowl", task_id="t1"):
    parsed = parse_instruction(instruction, IDS)
    return TaskDefinition(
        task_id=task_id,
        batch_id="fixture-batch",
        scene_id="fixture",
        robot_id="yam",
        instruction=instruction,
        criteria=parsed.criteria,
        protected_objects=parsed.protected,
        unresolved=parsed.unresolved,
        max_duration_seconds=100,
    )


def evidence(final=None, status="COMPLETED", task_id="t1", contacts=None, final_contacts=None, heights=None, duration=20):
    return Evidence(
        task_id=task_id,
        execution_status=status,
        source="unit-test fixture",
        initial_snapshot=snapshot("initial"),
        final_snapshot=snapshot("final", final),
        contacts=contacts if contacts is not None else [ContactEvent(a="apple", b="left_arm", start_s=1, end_s=2)],
        final_contacts=final_contacts if final_contacts is not None else [["apple", "bowl"]],
        max_heights=heights or {"apple": 0.90},
        duration_seconds=duration,
        action_count=int(duration * 20),
        model_calls=9,
    )


IN_BOWL = {"apple": [0.01, -0.03, 0.80]}


class EvaluationTests(unittest.TestCase):
    def test_01_complete_success(self):
        result = evaluate_task(task(), evidence(IN_BOWL))
        self.assertEqual(result.outcome, "SUCCESS")
        self.assertEqual(result.score, 100)
        self.assertEqual(result.coverage, 100)

    def test_02_success_with_disturbance(self):
        result = evaluate_task(task(), evidence({**IN_BOWL, "cup": [-0.12, 0.10, 0.80]}))
        self.assertEqual(result.outcome, "SUCCESS")
        self.assertEqual([i.type for i in result.incidents], ["MINOR_DISPLACEMENT"])
        self.assertEqual(result.score, 98)
        self.assertEqual(result.components["scene_preservation"].points, 13)

    def test_03_partial_success(self):
        result = evaluate_task(task(), evidence({"apple": [0.10, 0.05, 0.79]}, final_contacts=[]))
        self.assertEqual(result.outcome, "PARTIAL_SUCCESS")
        self.assertGreater(result.score, 0)
        self.assertLess(result.score, 70)

    def test_04_failed_task(self):
        result = evaluate_task(task(), evidence({}, contacts=[], final_contacts=[], heights={"apple": 0.79}))
        self.assertEqual(result.outcome, "FAILURE")
        self.assertEqual(result.score, 0)

    def test_05_unsafe(self):
        result = evaluate_task(task("put apple into the bowl without moving the cup"), evidence({**IN_BOWL, "cup": [0.9, 0.1, 0.1]}))
        self.assertEqual(result.outcome, "UNSAFE")
        self.assertLessEqual(result.score, 20)
        self.assertEqual(result.incidents[0].severity, "CRITICAL")

    def test_06_timeout_cap(self):
        result = evaluate_task(task(), evidence({"apple": [0.03, -0.01, 0.82]}, status="TIMED_OUT", final_contacts=[["apple", "left_arm"]], duration=100))
        self.assertEqual(result.outcome, "PARTIAL_SUCCESS")
        self.assertLessEqual(result.score, 50)
        self.assertEqual(evaluate_task(task(), evidence(IN_BOWL, status="TIMED_OUT", duration=100)).outcome, "SUCCESS")

    def test_07_missing_evidence(self):
        missing = evidence(IN_BOWL)
        missing.final_snapshot = None
        result = evaluate_task(task(), missing)
        self.assertEqual(result.outcome, "NOT_EVALUABLE")
        self.assertIsNone(result.score)
        self.assertIn("Final scene snapshot", result.missing_evidence)
        no_contacts = evidence(IN_BOWL)
        no_contacts.contacts = None
        partial = evaluate_task(task(), no_contacts)
        self.assertEqual(partial.components["interaction_quality"].status, "NOT_MEASURED")
        self.assertEqual(partial.coverage, 85)

    def test_08_multiple_tasks(self):
        instructions = [
            ("put apple into the bowl", IN_BOWL),
            ("move the apple to the left of the table", {"apple": [0.25, 0.10, 0.79]}),
            ("rotate the apple by 0 degrees", {}),
            ("put apple on top of the bowl", {"apple": [0.0, -0.04, 0.86]}),
            ("put apple into the bowl without moving the cup", {}),
        ]
        rows = []
        for index, (instruction, final) in enumerate(instructions):
            task_id = f"t{index}"
            definition = task(instruction, task_id)
            proof = evidence(final, task_id=task_id, final_contacts=[["apple", "bowl"]])
            result = evaluate_task(definition, proof)
            rows.append((definition, result, proof.execution_status))
            self.assertIn("Task report", task_html(task_report(definition, proof, result)))
        report = aggregate("fixture-batch", rows)
        self.assertEqual(report["total_tasks"], 5)
        self.assertEqual(len({row["task_id"] for row in report["tasks"]}), 5)
        self.assertIn("Benchmark report", batch_html(report))

    def test_09_duplicate_incident(self):
        contacts = [ContactEvent(a="cup", b="left_arm", start_s=t, end_s=t + 0.1) for t in (1, 2, 3)]
        contacts.append(ContactEvent(a="apple", b="left_arm", start_s=4, end_s=5))
        result = evaluate_task(task(), evidence({**IN_BOWL, "cup": [-0.12, 0.10, 0.80]}, contacts=contacts))
        self.assertEqual(len(result.incidents), 1)
        self.assertEqual(result.total_penalties, 2)

    def test_10_reproducibility(self):
        first = evaluate_task(task(), evidence({**IN_BOWL, "cup": [-0.05, 0.10, 0.80]}))
        second = evaluate_task(task(), evidence({**IN_BOWL, "cup": [-0.05, 0.10, 0.80]}))
        self.assertEqual(first.model_dump(), second.model_dump())

    def test_unsupported_instruction_is_not_scored(self):
        result = evaluate_task(task("wave at the camera"), evidence({}))
        self.assertEqual(result.outcome, "NOT_EVALUABLE")

    def test_infrastructure_failure_is_not_scored(self):
        proof = evidence({}, status="FAILED", contacts=[], final_contacts=[], duration=0)
        proof.errors = ["no capacity"]
        self.assertEqual(evaluate_task(task(), proof).outcome, "NOT_EVALUABLE")

    def test_html_escapes_instruction(self):
        definition = task("put apple into the bowl <script>alert(1)</script>")
        proof = evidence(IN_BOWL)
        page = task_html(task_report(definition, proof, evaluate_task(definition, proof)))
        self.assertNotIn("<script>", page)

    def test_store_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            first = EvaluationStore(Path(folder))
            result = first.submit(task(), evidence(IN_BOWL))
            second = EvaluationStore(Path(folder))
            self.assertEqual(second.result("t1").model_dump(), result.model_dump())
            with self.assertRaises(ValueError):
                first._path("../escape")

    def test_instruction_parsing(self):
        parsed = parse_instruction("Put the apple in the bowl without moving the cup", IDS)
        self.assertEqual(parsed.criteria, [Criterion(type="pick_place", object_id="apple", destination_id="bowl")])
        self.assertEqual(parsed.protected, ["cup"])


if __name__ == "__main__":
    unittest.main()
