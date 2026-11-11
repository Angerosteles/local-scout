"""Full alphabetical catalog and declarative conditions; no recommendation/ranking."""
from pathlib import Path
from .decisions import evaluate


def recipes():
    import yaml
    result = []
    for path in sorted((Path(__file__).resolve().parents[1]/'recipes').glob('*.md')):
        front = path.read_text(encoding='utf-8').split('---',2)[1]
        item = yaml.safe_load(front)
        if not isinstance(item,dict) or 'requires' not in item:
            raise ValueError
        result.append(item)
    return result


def conditions(recipe, facts):
    result=[]
    for key, value in recipe['requires'].items():
        if not isinstance(value,dict) or set(value)!={'op','value'}:
            raise ValueError
        result.append(dict(fact=key,op=value['op'],actual=facts.get(key),expected=value['value'],
                           ok=evaluate(facts.get(key),value['op'],value['value'])))
    return result
