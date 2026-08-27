import unittest
from _shared.models import ActorPlan, CampaignSpec, JudgeEvaluation, TestRecord
from _shared.validators import (apply_coverage, canonicalize_evaluation, fallback_task,
    new_coverage, validate_actor_plan, validate_record)

def campaign():
    return CampaignSpec.model_validate({"who":"coupon assistant","what":"SAVE20 requests","where":"checkout",
      "when":"all required scenarios have records or limit reached","why":"discount risk","how":"text interaction",
      "authoritative_requirements":[{"requirement_id":"REQ1","text":"SAVE20 is 20% at $50 or more"}],
      "max_tests":1,"max_turns_per_test":3,"scenarios":[{"scenario_id":"R1","title":"threshold",
      "description":"exactly $50","priority":1,"requirement_ids":["REQ1"],"actor_goal":"request SAVE20",
      "user_persona":"shopper","starting_state":{"subtotal":50.0,"coupon":"SAVE20"},
      "actor_actions":["Request SAVE20"],"evidence_to_capture":["feature response"],"criterion_ids":["R1"]}],
      "rubric":[{"criterion_id":"R1","scenario_id":"R1","requirement_ids":["REQ1"],
      "observable_behavior":"discount result","severity":"CRITICAL","expected_behavior":"apply 20%",
      "prohibited_behavior":"wrong percentage","evidence_required":["feature response"],
      "required_evidence_values":["$50","20%","$10","$40"]}]})

class Tests(unittest.TestCase):
    def setUp(self):
        self.c=campaign(); self.t=fallback_task(self.c,"R1",1)
        self.r=TestRecord.model_validate({"record_id":"C-T01","campaign_id":"C","task_id":self.t.task_id,
          "scenario_id":"R1","actor_adapter":"simulated","starting_state":self.t.starting_state,
          "transcript":[{"speaker":"actor","content":"My subtotal is $50 and coupon is SAVE20."},
          {"speaker":"feature","content":"SAVE20 applied: 20% or $10 off; new subtotal $40."}],
          "execution_status":"completed","termination_reason":"done","simulation_disclosure":True})

    def test_valid_record(self): validate_record(self.r,self.t,"C")
    def test_role_confusion_rejected(self):
        data=self.r.model_dump(mode="json"); data["transcript"][-1]["speaker"]="actor"
        with self.assertRaises(ValueError): validate_record(TestRecord.model_validate(data),self.t,"C")
    def test_missing_starting_state_rejected(self):
        data=self.r.model_dump(mode="json"); data["transcript"][0]["content"]="Apply my coupon."
        with self.assertRaises(ValueError): validate_record(TestRecord.model_validate(data),self.t,"C")
    def test_actor_cannot_speak_for_feature(self):
        p=ActorPlan(task_id=self.t.task_id,scenario_id="R1",actor_messages=["The assistant applies it"],strategy_summary="x")
        with self.assertRaises(ValueError): validate_actor_plan(p,self.t)
    def test_wrong_percentage_pass_is_overridden(self):
        bad=self.r.model_copy(update={"transcript":[self.r.transcript[0],
          self.r.transcript[1].model_copy(update={"content":"A 10% discount was applied to $50."})]})
        e=JudgeEvaluation.model_validate({"evaluation_id":"E","record_id":"C-T01","scenario_id":"R1",
          "verdict":"PASS","score":100,"confidence":"high","criteria":[{"criterion_id":"R1","result":"PASS",
          "evidence_citations":["transcript[1]"],"explanation":"discount applied"}],"reasoning_summary":"pass"})
        result=canonicalize_evaluation(e,bad,self.t,self.c.rubric)
        self.assertEqual(result.verdict.value,"INCONCLUSIVE"); self.assertEqual(result.criteria[0].result.value,"INCONCLUSIVE")
    def test_coverage_dimensions(self):
        e=JudgeEvaluation.model_validate({"evaluation_id":"E","record_id":"C-T01","scenario_id":"R1",
          "verdict":"FAIL","score":0,"confidence":"high","criteria":[{"criterion_id":"R1","result":"FAIL",
          "evidence_citations":["transcript[1]"],"explanation":"wrong"}],"reasoning_summary":"fail"})
        ledger=new_coverage(self.c); apply_coverage(ledger,self.r,e)
        self.assertEqual((ledger["R1"]["execution"],ledger["R1"]["evidence"],ledger["R1"]["verdict"]),("EXECUTED","SUFFICIENT","FAIL"))

if __name__=="__main__": unittest.main()
