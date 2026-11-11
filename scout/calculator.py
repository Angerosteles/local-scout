import math


def calculate(p):
    costs = ("local", "fallback", "setup", "error_cost")
    if type(p["n"]) is not int or p["n"] < 0:
        raise ValueError
    cloud = p["cloud"]
    if not isinstance(cloud, dict) or set(cloud) != {"amount", "currency"}:
        raise ValueError
    currency = cloud["currency"]
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
        raise ValueError
    values = {key: p[key] for key in costs + ("p_abstain", "error_rate")}
    values["cloud"] = cloud["amount"]
    for key, value in values.items():
        if value is None and key not in ("p_abstain", "error_rate"):
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError
        if key in ("p_abstain", "error_rate") and value > 1:
            raise ValueError
    unknown = sorted(key for key, value in values.items() if value is None)
    net = be = None
    if not unknown:
        margin = values["cloud"] - p["local"] - p["p_abstain"]*p["fallback"] - (1-p["p_abstain"])*p["error_rate"]*p["error_cost"]
        net = p["n"]*margin-p["setup"]
        if not math.isfinite(net):
            raise ValueError
        be = math.ceil(p["setup"]/margin) if margin > 0 else None
    return dict(net=net, break_even_n=be, currency=currency,
                assumptions=dict(p, period="n tasks; unit costs in one currency; conditional accepted error"), unknowns=unknown)
