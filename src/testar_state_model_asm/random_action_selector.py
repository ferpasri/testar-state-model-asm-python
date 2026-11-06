"""Random ASM chooses uniformly among the actions currently allowed for execution."""

from .action_selector import ActionSelector


class RandomActionSelector(ActionSelector):
    """Every current eligible action has the same chance, regardless of its history."""

    def select(self, asm):
        """Choose equally among current eligible actions, regardless of previous execution counts."""
        rankings = self.rankings(include_unmatched_choices=True)
        eligible = [item for item in rankings if item["eligible"]]
        return {"choice_id": self.choose(eligible)["choice_id"], "reason": "random_action", "rankings": rankings}
