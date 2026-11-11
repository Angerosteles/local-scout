ID = {"type": "string", "pattern": "^[a-z0-9_-]{1,64}$", "maxLength": 64}
FIELDS = {
    "scout_handle": ["task", "text"],
    "scout_calibrate": ["dataset_id", "threshold", "min_cases", "seed"],
    "scout_inspect": ["dataset_id"],
    "scout_recipes": ["candidate_id"],
    "scout_choose": ["candidate_id", "recipe_id", "reason"],
    "scout_train": ["candidate_id", "recipe_id", "dataset_id"],
    "scout_predict": ["artifact_id", "query_id"],
    "scout_report": ["dataset_id"],
    "scout_calculate": ["n", "cloud", "local", "p_abstain", "fallback", "setup", "error_rate", "error_cost"],
}
SCHEMAS = {}
for name, fields in FIELDS.items():
    props = {key: dict(ID) for key in fields}
    if name == "scout_handle":
        props = {"task": {"type": "string", "enum": ["triage", "search", "fields"]}, "text": {"type": "string", "maxLength": 4096}}
    if name == "scout_calibrate":
        props.update(threshold={"type":"number","minimum":0,"maximum":1},
                     min_cases={"type":"integer","minimum":1,"maximum":1000},
                     seed={"type":"integer","minimum":1,"maximum":4294967295})
    if name == "scout_choose":
        props["reason"] = {"type": "string", "minLength": 1, "maxLength": 400,
                            "description": "Reason up to 400 characters; use explicit premises such as label_count=3>=2."}
    if name == "scout_calculate":
        props = {key: {"type": ["number", "null"], "minimum": 0} for key in fields}
        props["n"] = {"type": "integer", "minimum": 0}
        for key in ("p_abstain", "error_rate"):
            props[key] = {"type": "number", "minimum": 0, "maximum": 1}
        props["cloud"] = {"type": "object", "properties": {
            "amount": {"type": ["number", "null"], "minimum": 0},
            "currency": {"type": "string", "pattern": "^[A-Z]{3}$"}},
            "required": ["amount", "currency"], "additionalProperties": False}
    SCHEMAS[name] = {"name": name,
        "description": "Local Scout: approved synthetic IDs only; no raw text, paths or personal data.",
        "parameters": {"type": "object", "properties": props,
                       "required": fields, "additionalProperties": False}}

DESCRIPTIONS = {
 'scout_handle': 'Handle text locally after calibration and an active choice; abstention means do the task yourself. Text is masked locally and not stored.',
 'scout_calibrate': 'Replay train/validation without future data; return family facts and estimates. No PII or recommendations. Requires observe and report consent.',
 'scout_inspect': 'Return train/validation facts and the complete ID catalog. The agent chooses the method. Approved synthetic IDs only.',
 'scout_recipes': 'Return full cards with requires and fit, without ranking. Compare with the profile; do not quote data.',
 'scout_choose': 'Record a choice and reason (up to 400 characters). Use explicit premises such as label_count=3>=2. No PII. The audit does not choose for the agent.',
 'scout_train': 'Train locally on an approved dataset. Fit on train; threshold and metrics use validation. Only the operator accesses the frozen holdout. Results link to the decision.',
 'scout_predict': 'Predict an approved query_id without raw text; return up to three index sources and abstain on unknown features.',
 'scout_report': 'Return observed_attempt categories and counts in the approved scope; this proves neither delivery nor protection of all traffic.',
 'scout_calculate': 'Calculate costs and break_even_n from explicit assumptions in one currency; null means unavailable.',
}
for name, schema in SCHEMAS.items():
    schema['description'] = 'Make one Scout tool call at a time; Hermes rejects batches. ' + DESCRIPTIONS[name]


def validate(name, params):
    """Small strict validator mirrors published schemas without another dependency."""
    import re
    if name not in FIELDS or not isinstance(params,dict) or set(params)!=set(FIELDS[name]):
        raise ValueError
    for key,value in params.items():
        schema=SCHEMAS[name]['parameters']['properties'][key]
        if key.endswith('_id') and (not isinstance(value,str) or not re.fullmatch(ID['pattern'],value)):
            raise ValueError
        if key=='reason' and (not isinstance(value,str) or not 1<=len(value)<=400):
            raise ValueError
    if name=='scout_handle' and (params['task'] not in ('triage','search','fields') or not isinstance(params['text'],str) or len(params['text'])>4096):
        raise ValueError
    if name=='scout_calculate':
        from .scout.calculator import calculate
        calculate(params)

    if name=='scout_calibrate':
        from .scout.calibration import parameters
        parameters({k:v for k,v in params.items() if k != 'dataset_id'})
