"""Deterministic role, evidence, coverage, and verdict gates."""
import re
from decimal import Decimal, InvalidOperation
from .models import (ActorPlan, CampaignPlan, CampaignSpec, CoverageEntry,
    EvidenceCoverage, ExecutionCoverage, JudgeEvaluation, RubricCriterion,
    TargetSimulation, TestRecord, TestTask, Verdict)

PLACEHOLDERS=(r"^\s*\(.*\)\s*$",r"no specific task",r"value depending",r"if applicable",
              r"the (?:assistant|feature) should",r"expected to",r"potential application")
FEATURE_ACTIONS=re.compile(r"\b(?:assistant|feature|system)\s+(?:applies?|refuses?|confirms?|offers?|responds?)\b",re.I)

def placeholder(text): return any(re.search(x,text,re.I) for x in PLACEHOLDERS)

def validate_plan(plan: CampaignPlan):
    c=plan.campaign
    if c.unresolved_ambiguities: raise ValueError(f"unresolved material ambiguities: {c.unresolved_ambiguities}")
    if c.max_tests < len(c.scenarios): raise ValueError("max_tests is smaller than required scenarios")
    for index,scenario in enumerate(c.scenarios,1):
        validate_task(fallback_task(c,scenario.scenario_id,index),c,{})
    validate_task(plan.first_task,c,{})

def validate_task(t: TestTask,c: CampaignSpec,coverage: dict):
    scenarios={x.scenario_id:x for x in c.scenarios}
    if t.scenario_id not in scenarios: raise ValueError("task references unknown scenario")
    s=scenarios[t.scenario_id]
    if set(t.criterion_ids)!=set(s.criterion_ids): raise ValueError("task criteria do not match scenario")
    if set(t.requirement_ids)!=set(s.requirement_ids): raise ValueError("task requirements do not match scenario")
    if not t.starting_state: raise ValueError("task requires structured starting_state")
    if any(placeholder(x) or FEATURE_ACTIONS.search(x) for x in t.actor_actions):
        raise ValueError("actor_actions contain feature behavior, expected behavior, or placeholders")
    current=coverage.get(t.scenario_id,{})
    if current.get("execution") == ExecutionCoverage.EXECUTED.value and not t.allow_repeat:
        raise ValueError("task repeats an executed scenario")

def fallback_task(c: CampaignSpec,scenario_id: str,sequence: int):
    s=next(x for x in c.scenarios if x.scenario_id==scenario_id)
    criteria=[x for x in c.rubric if x.criterion_id in s.criterion_ids]
    grading="\n".join(f"{x.criterion_id}: expected={x.expected_behavior}; prohibited={x.prohibited_behavior}; "
                      f"evidence={'; '.join(x.evidence_required)}; values={'; '.join(x.required_evidence_values)}"
                      for x in criteria)
    return TestTask(task_id=f"TASK-{scenario_id}-{sequence:02d}",scenario_id=scenario_id,
        goal=s.actor_goal,user_persona=s.user_persona,target_description=f"{c.who}: {c.what}",
        starting_state=s.starting_state,actor_actions=s.actor_actions,max_turns=c.max_turns_per_test,
        evidence_to_capture=s.evidence_to_capture,requirement_ids=s.requirement_ids,
        criterion_ids=s.criterion_ids,grading_context=grading)

def validate_actor_plan(p: ActorPlan,t: TestTask):
    if p.task_id!=t.task_id or p.scenario_id!=t.scenario_id: raise ValueError("Actor Plan IDs do not match task")
    if len(p.actor_messages)>t.max_turns: raise ValueError("Actor exceeded maximum exchanges")
    if any(placeholder(x) or FEATURE_ACTIONS.search(x) for x in p.actor_messages):
        raise ValueError("Actor spoke for the feature or used placeholder/expected language")

def validate_target(s: TargetSimulation,p: ActorPlan,t: TestTask):
    if s.task_id!=t.task_id or s.scenario_id!=t.scenario_id: raise ValueError("Target IDs do not match task")
    if len(s.feature_responses)!=len(p.actor_messages): raise ValueError("Target must return one response per Actor message")
    if any(placeholder(x) for x in s.feature_responses): raise ValueError("Target response contains placeholder language")

def _number(text):
    try: return Decimal(text.replace("$","").replace(",","").strip())
    except InvalidOperation: return None

def value_present(value,text):
    if isinstance(value,bool): return str(value).lower() in text.lower()
    if isinstance(value,(int,float)):
        wanted=Decimal(str(value))
        for token in re.findall(r"\$?-?\d[\d,]*(?:\.\d+)?",text):
            found=_number(token)
            if found is not None and found==wanted: return True
        return False
    return str(value).lower() in text.lower()

def validate_record(r: TestRecord,t: TestTask,campaign_id: str):
    if (r.campaign_id,r.task_id,r.scenario_id)!=(campaign_id,t.task_id,t.scenario_id):
        raise ValueError("record identifiers do not match task")
    if r.starting_state!=t.starting_state: raise ValueError("record starting_state changed")
    if not r.simulation_disclosure: raise ValueError("simulation disclosure required")
    if len(r.transcript)>t.max_turns*2: raise ValueError("record exceeded maximum exchanges")
    for i,turn in enumerate(r.transcript):
        expected="actor" if i%2==0 else "feature"
        if turn.speaker!=expected: raise ValueError(f"transcript[{i}] must be {expected}")
        if placeholder(turn.content): raise ValueError(f"transcript[{i}] contains placeholder language")
    if r.transcript[-1].speaker!="feature": raise ValueError("transcript must end with a feature response")
    transcript="\n".join(x.content for x in r.transcript)
    missing=[key for key,value in t.starting_state.items() if not value_present(value,transcript)]
    if missing: raise ValueError(f"starting-state evidence missing from transcript: {missing}")

def _required_value_present(value: str,text: str):
    if "%" in value: return value.replace(" ","").lower() in text.replace(" ","").lower()
    number=_number(value)
    return value_present(float(number),text) if number is not None else value.lower() in text.lower()

def canonicalize_evaluation(e: JudgeEvaluation,r: TestRecord,t: TestTask,criteria: list[RubricCriterion]):
    if e.record_id!=r.record_id or e.scenario_id!=r.scenario_id: raise ValueError("evaluation IDs do not match record")
    if {x.criterion_id for x in e.criteria}!=set(t.criterion_ids): raise ValueError("evaluation criteria do not match task")
    reasons=[]
    for result in e.criteria:
        seen=set(); cited=[]
        for citation in result.evidence_citations:
            match=re.fullmatch(r"transcript\[(\d+)\]",citation)
            if not match or int(match.group(1))>=len(r.transcript): reasons.append(f"invalid citation {citation}"); continue
            if citation in seen: reasons.append(f"duplicate citation {citation}"); continue
            seen.add(citation); cited.append(r.transcript[int(match.group(1))])
        if result.result==Verdict.PASS:
            if not any(x.speaker=="feature" for x in cited): reasons.append(f"{result.criterion_id} PASS lacks feature evidence")
            rule=next(x for x in criteria if x.criterion_id==result.criterion_id)
            cited_text="\n".join(x.content for x in cited)
            missing=[x for x in rule.required_evidence_values if not _required_value_present(x,cited_text)]
            if missing: reasons.append(f"{result.criterion_id} missing required cited values: {missing}")
    inconsistent=e.verdict==Verdict.PASS and (reasons or e.missing_evidence or any(x.result!=Verdict.PASS for x in e.criteria))
    if inconsistent:
        note="Framework replaced unsupported PASS: "+"; ".join(reasons or e.missing_evidence)
        criteria_fixed=[x.model_copy(update={"result":Verdict.INCONCLUSIVE,
            "explanation":x.explanation+" Framework could not verify all required evidence."}) for x in e.criteria]
        return e.model_copy(update={"verdict":Verdict.INCONCLUSIVE,"score":0,"confidence":"low",
            "criteria":criteria_fixed,"missing_evidence":list(dict.fromkeys(e.missing_evidence+reasons)),
            "recommended_follow_up":"Rerun with complete, correctly cited evidence.",
            "framework_notes":e.framework_notes+[note]})
    return e

def new_coverage(c: CampaignSpec):
    return {x.scenario_id:CoverageEntry(scenario_id=x.scenario_id).model_dump(mode="json") for x in c.scenarios}

def apply_coverage(coverage: dict,r: TestRecord,e: JudgeEvaluation):
    x=CoverageEntry.model_validate(coverage[r.scenario_id]); x.record_ids.append(r.record_id); x.evaluation_ids.append(e.evaluation_id)
    x.execution=ExecutionCoverage.EXECUTED; x.verdict=e.verdict
    x.evidence=EvidenceCoverage.SUFFICIENT if e.verdict in {Verdict.PASS,Verdict.FAIL} else EvidenceCoverage.INSUFFICIENT
    coverage[r.scenario_id]=x.model_dump(mode="json")

def first_open_scenario(c: CampaignSpec,coverage: dict):
    for s in sorted(c.scenarios,key=lambda x:x.priority):
        if coverage[s.scenario_id]["execution"]==ExecutionCoverage.NOT_RUN.value: return s.scenario_id
    return None
