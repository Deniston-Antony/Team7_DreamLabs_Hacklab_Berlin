"""Offline checks for the success detector: no API call, no key."""

import threading
import time
import unittest

import mujoco
import numpy as np

from sortflow import BODIES, TARGETS, GoalWatcher, SuccessWatcher, contact_goal_reached, is_inside, parse_task
from yam_env import YamEnv


class SortflowTest(unittest.TestCase):
    """The detector's verdicts match where the objects really are."""

    def setUp(self):
        """Open a fresh scene."""
        self.env = YamEnv()
        self.home = np.r_[np.zeros(6), 1.0]

    def tearDown(self):
        """Release the renderer."""
        self.env.close()

    def drop(self, item, x, y, z):
        """Teleport an item to a point and let it settle."""
        joint = "bottle_free" if item == "bottle" else f"{BODIES[item]}_free"
        self.env.data.joint(joint).qpos[:3] = [x, y, z]
        mujoco.mj_forward(self.env.model, self.env.data)
        for _ in range(40):
            self.env.step(self.home, self.home)

    def test_nothing_starts_in_a_target(self):
        """The untouched scene counts no item as placed."""
        for item in BODIES:
            for target in TARGETS:
                self.assertFalse(is_inside(self.env, item, target), f"{item} starts in the {target}")

    def test_dropped_items_count(self):
        """An apple dropped in the bowl and a banana on the plate are placed."""
        self.drop("apple", 0.0, -0.04, 0.86)
        self.assertTrue(is_inside(self.env, "apple", "bowl"))
        self.drop("banana", -0.22, -0.045, 0.80)
        self.assertTrue(is_inside(self.env, "banana", "plate"))

    def test_near_misses_do_not_count(self):
        """An item held above the bowl, or resting beside it, is not placed."""
        self.env.data.joint("orange_free").qpos[:3] = [0.0, -0.04, 1.0]
        mujoco.mj_forward(self.env.model, self.env.data)
        self.assertFalse(is_inside(self.env, "orange", "bowl"))
        self.drop("bottle", 0.20, 0.0, 0.85)
        self.assertFalse(is_inside(self.env, "bottle", "bowl"))

    def test_watcher_sets_done_once_the_item_rests(self):
        """The watcher fires after the hold time, and stays quiet when the item is elsewhere."""
        done = threading.Event()
        watcher = SuccessWatcher(self.env, "apple", "bowl", done)
        watcher.start()
        try:
            self.drop("apple", 0.0, -0.04, 0.86)
            for _ in range(60):  # three simulated seconds
                self.env.step(self.home, self.home)
                time.sleep(0.01)
            self.assertTrue(done.wait(timeout=5))
            self.assertIsNotNone(watcher.success_time)
        finally:
            watcher.stop.set()
            watcher.join(timeout=2)

    def test_the_contact_rule_needs_the_item_to_touch_the_target_and_settle(self):
        """The website's rule: in the bowl and touching it counts; held in the air above it does not."""
        self.env.data.joint("apple_free").qpos[:3] = [0.0, -0.04, 0.95]
        mujoco.mj_forward(self.env.model, self.env.data)
        self.assertFalse(contact_goal_reached(self.env, "apple", "bowl"))
        self.drop("apple", 0.0, -0.04, 0.86)
        for _ in range(80):  # let it settle
            self.env.step(self.home, self.home)
        self.assertTrue(contact_goal_reached(self.env, "apple", "bowl"))
        self.assertFalse(contact_goal_reached(self.env, "apple", "plate"))
        self.assertFalse(contact_goal_reached(self.env, "no_such_body", "bowl"))

    def test_goal_watcher_fires_after_the_settled_frames(self):
        """GoalWatcher sets done once the apple has rested in the bowl for 30 rendered frames, every time."""
        for _ in range(3):  # a race in the first version made this pass only about half the time
            env = YamEnv()
            try:
                done = threading.Event()
                watcher = GoalWatcher(env, "apple", "bowl", done)
                watcher.start()
                env.data.joint("apple_free").qpos[:3] = [0.0, -0.04, 0.86]
                mujoco.mj_forward(env.model, env.data)
                for _ in range(120):
                    env.step(self.home, self.home)
                    env.images()
                self.assertTrue(done.is_set())
                self.assertIsNotNone(watcher.success_time)
                watcher.join()
                self.assertNotIn("images", env.__dict__, "the environment's own images() is back")
            finally:
                env.close()

    def test_goal_watcher_stays_quiet_when_the_item_is_elsewhere(self):
        """An untouched scene never counts as success, and the closest distance is recorded."""
        done = threading.Event()
        watcher = GoalWatcher(self.env, "orange", "bowl", done)
        watcher.start()
        for _ in range(60):
            self.env.step(self.home, self.home)
            self.env.images()
        watcher.join()
        self.assertFalse(done.is_set())
        self.assertGreater(watcher.min_distance, 0.1)

    def test_task_wording(self):
        """Tasks name an item and a target in ordinary words, and anything else is left unchecked."""
        self.assertEqual(parse_task("put apple into the bowl"), ("apple", "bowl"))
        self.assertEqual(parse_task("Put the water bottle on the plate"), ("bottle", "plate"))
        self.assertIsNone(parse_task("wave hello"))


if __name__ == "__main__":
    unittest.main()
