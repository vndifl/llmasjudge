import unittest

from _shared.models import CampaignSpec, JudgeEvaluation, TestRecord
from _shared.validators import (apply_coverage, canonicalize_evaluation,
                                fallback_task, new_coverage, validate_record)


def sample_campaign():
    return CampaignSpec.model_validate({
        "who": "coupon feature", "what": "SAVE20", "where": "checkout",
        "when": "required records exist", "why": "boundary risk", "how": "text",
        "max_tests": 3, "max_turns_per_test": 3,
        "scenarios": [{"scenario_id": "R1", "title": "below", "description": "$49.99",
            "priority": 1, "actor_goal": "request SAVE20 and press once", "user_persona": "loyal shopper",
            "starting_state": {"subtotal": 49.99}, "user_actions": ["Request SAVE20", "Ask once for an exception"],
            "evidence_to_capture": ["both feature responses"], "criterion_ids": ["R1"]}],
        "rubric": [{"criterion_id": "R1", "scenario_id": "R1", "observable_behavior": "refusal",
            "severity": "CRITICAL", "expected_behavior": "refuse below $50",
            "prohibited_behavior": "promise discount", "evidence_required": ["transcript"]}]
    })


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.campaign = sample_campaign()
        self.task = fallback_task(self.campaign, "R1", 1)
        self.record = TestRecord.model_validate({
            "record_id": "C-T01", "campaign_id": "C", "task_id": self.task.task_id,
            "scenario_id": "R1", "actor_adapter": "simulated",
            "transcript": [{"speaker": "actor", "content": "Apply SAVE20"},
                           {"speaker": "feature", "content": "I cannot apply it below $50."}],
            "observations": [], "execution_status": "completed", "termination_reason": "done",
            "evidence": ["transcript"], "simulation_disclosure": True})

    def test_valid_record(self):
        validate_record(self.record, self.task, "C")

    def test_placeholder_record_rejected(self):
        data = self.record.model_dump(mode="json")
        data["transcript"] = [{"speaker": "actor", "content": "Apply SAVE20"},
                              {"speaker": "feature", "content": "The feature should refuse the coupon"}]
        bad = TestRecord.model_validate(data)
        with self.assertRaises(ValueError):
            validate_record(bad, self.task, "C")

    def test_unsupported_pass_becomes_inconclusive(self):
        evaluation = JudgeEvaluation.model_validate({
            "evaluation_id": "E", "record_id": "C-T01", "scenario_id": "R1",
            "verdict": "PASS", "score": 100, "confidence": "high",
            "criteria": [{"criterion_id": "R1", "result": "PASS", "evidence_citations": [], "explanation": "ok"}],
            "reasoning_summary": "ok", "missing_evidence": [], "framework_notes": []})
        result = canonicalize_evaluation(evaluation, self.record, self.task)
        self.assertEqual(result.verdict.value, "INCONCLUSIVE")

    def test_ledger_tracks_scenario(self):
        evaluation = JudgeEvaluation.model_validate({
            "evaluation_id": "E", "record_id": "C-T01", "scenario_id": "R1",
            "verdict": "FAIL", "score": 20, "confidence": "high",
            "criteria": [{"criterion_id": "R1", "result": "FAIL", "evidence_citations": ["transcript[1]"], "explanation": "failed"}],
            "reasoning_summary": "failed", "missing_evidence": [], "framework_notes": []})
        ledger = new_coverage(self.campaign)
        apply_coverage(ledger, self.record, evaluation)
        self.assertEqual(ledger["R1"]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
