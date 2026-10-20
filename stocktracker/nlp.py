"""Rule-based reading of a headline: what kind of event it is, whether it is
good or bad for the share, whether the effect is short or long term, and how big."""
import re

# event_type: (patterns, base sentiment -1..1, horizon, weight 0..1, label)
EVENTS = {
    "fraud_regulatory": ([r"\bfraud\b", r"\bprobe\b", r"\braid", r"\bsebi (order|notice|bars|penalty)",
                          r"\bpenalty\b", r"\bfine[sd]?\b", r"\bwarning letter\b", r"\bimport alert\b",
                          r"\bshow[- ]cause\b", r"\benforcement directorate\b", r"\bED\b searches",
                          r"\bincome tax (raid|search)", r"\bbanned?\b", r"\bforensic audit"],
                         -0.8, "both", 1.0, "Regulatory / legal trouble"),
    "default_insolvency": ([r"\bdefault", r"\binsolvency\b", r"\bNCLT\b", r"\bbankrupt", r"\bdowngrad\w+ to (d|junk)",
                            r"\bdebt restructuring\b"], -0.9, "long", 1.0, "Debt default / insolvency"),
    "management_exit": ([r"\b(resigns?|resignation|steps? down|quits?|exits?)\b.*\b(ceo|md|cfo|chairman|director|chief)",
                         r"\b(ceo|md|cfo|chairman|chief \w+ officer)\b.*\b(resigns?|steps? down|quits?|exits?)\b"],
                        -0.5, "both", 0.8, "Top management exit"),
    "management_appoint": ([r"\b(appoints?|appointed|names?|elevates?|new)\b.*\b(ceo|md|cfo|chairman|chief)",
                            r"\b(ceo|md|chairman)\b.*\bappoint"], 0.15, "long", 0.6, "Top management change"),
    "results": ([r"\bq[1-4]\b", r"\bquarter(ly)? results?\b", r"\bnet profit\b", r"\bearnings\b", r"\bprofit (rises|jumps|falls|declines|surges|drops)",
                 r"\brevenue (rises|jumps|falls|grows|declines)", r"\bebitda\b", r"\bfy\d{2}\b results"],
                0.0, "short", 0.9, "Quarterly results"),
    "order_win": ([r"\b(bags?|wins?|secures?|bagged|won|receives?|gets?)\b.*\b(order|contract|deal|tender|project)s?\b",
                   r"\border (worth|book|inflow)\b", r"\bLoA\b", r"\bletter of award\b"], 0.6, "both", 0.8, "Order / contract win"),
    "product_approval": ([r"\b(launch(es|ed)?|unveil(s|ed)?|introduc(es|ed))\b", r"\b(usfda|dcgi|cdsco) (nod|approval|approves)",
                          r"\bapproval for\b", r"\bpatent\b"], 0.4, "long", 0.6, "Product launch / approval"),
    "merger_acquisition": ([r"\bacquir(e|es|ed|ing)\b", r"\bacquisition\b", r"\bmerger\b", r"\bamalgamation\b", r"\bdemerger\b",
                            r"\btakeover\b", r"\bopen offer\b", r"\bjoint venture\b", r"\bJV\b"], 0.3, "both", 0.8, "Merger / acquisition / JV"),
    "capital_action": ([r"\bdividend\b", r"\bbonus (issue|shares?)\b", r"\bbuy-?back\b", r"\bstock split\b", r"\bsplit\b"],
                       0.5, "short", 0.6, "Dividend / bonus / buyback / split"),
    "fund_raise": ([r"\bQIP\b", r"\brights issue\b", r"\bpreferential (issue|allotment)\b", r"\braises? (rs|₹|\$)", r"\bfund ?rais"],
                   -0.1, "both", 0.5, "Fund raising (possible dilution)"),
    "rating_change": ([r"\bupgrad(e|es|ed)\b", r"\bdowngrad(e|es|ed)\b", r"\btarget price\b", r"\b(buy|sell|hold|overweight|underweight) (rating|call)\b",
                       r"\binitiates? coverage\b"], 0.0, "short", 0.6, "Broker / rating change"),
    "promoter_activity": ([r"\bpromoters?\b.*\b(pledge|stake|sell|buy|offload|acquire|increase|reduce)", r"\bpledged? shares\b",
                           r"\boffer for sale\b", r"\bOFS\b"], 0.0, "both", 0.8, "Promoter stake activity"),
    "big_deal": ([r"\bblock deal\b", r"\bbulk deal\b", r"\bpicks? (up )?stake\b", r"\bbuys? stake\b", r"\bsells? stake\b",
                  r"\boffloads?\b", r"\bstake sale\b"], 0.0, "short", 0.7, "Bulk / block deal or stake change"),
    "expansion": ([r"\bcapex\b", r"\bexpansion\b", r"\bnew (plant|facility|factory|unit)\b", r"\bcapacity\b", r"\bcommission(s|ed|ing)\b",
                   r"\bforay\b"], 0.4, "long", 0.6, "Expansion / capex"),
    "operations_disruption": ([r"\bfire\b", r"\bshut ?down\b", r"\bstrike\b", r"\bplant closure\b", r"\brecall\b", r"\bcyber ?attack\b",
                               r"\boutage\b", r"\baccident\b"], -0.5, "short", 0.7, "Operations disruption"),
    "guidance": ([r"\bguidance\b", r"\boutlook\b", r"\bforecast\b"], 0.0, "both", 0.6, "Guidance / outlook"),
}

POS = ["surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies", "gain", "gains", "rise", "rises",
       "rose", "record", "beat", "beats", "strong", "robust", "growth", "grows", "upgrade", "upgraded", "wins", "win",
       "bags", "approval", "approves", "profit up", "higher", "outperform", "bullish", "boost", "boosts", "expands",
       "positive", "buy rating", "top pick", "hits 52-week high", "all-time high", "multibagger", "raises guidance",
       "turnaround", "best-ever", "doubles", "climbs", "new high", "accumulate"]
NEG = ["fall", "falls", "fell", "plunge", "plunges", "slump", "slumps", "crash", "crashes", "drop", "drops", "decline",
       "declines", "miss", "misses", "weak", "loss", "losses", "downgrade", "downgraded", "resigns", "penalty", "fraud",
       "probe", "cut", "cuts", "lower", "underperform", "bearish", "concern", "concerns", "warning", "slowdown",
       "sell rating", "52-week low", "tumble", "tumbles", "sinks", "slips", "pressure", "headwind", "lawsuit", "default",
       "halts", "delay", "delayed", "pledge", "dilution", "exit", "exits", "worst", "shrinks", "reduce"]

# Global / macro news mapped to Indian industries it tends to move.
# (keywords, industry keywords, direction for those industries, label)
MACRO = [
    ([r"crude (oil )?(prices? )?(surge|jump|rise|rally|spike|soar)", r"oil prices? (surge|jump|rise|soar)", r"\bbrent\b.*(rise|jump|surge)"],
     ["paint", "aviation", "airline", "tyre", "refiner", "oil marketing", "chemical"], -1, "Crude oil up"),
    ([r"crude (oil )?(prices? )?(fall|drop|slump|plunge|crash)", r"oil prices? (fall|drop|slump|plunge)"],
     ["paint", "aviation", "airline", "tyre", "refiner", "oil marketing", "chemical"], +1, "Crude oil down"),
    ([r"rupee (weakens|falls|slips|hits record low|depreciat)", r"dollar (strengthens|rises) against rupee"],
     ["software", "it services", "computers", "pharma", "textile"], +1, "Rupee weaker (helps exporters)"),
    ([r"\bfed\b.*(rate cut|cuts rates)", r"\brbi\b.*(rate cut|cuts repo|cuts rates)"],
     ["bank", "finance", "nbfc", "realty", "real estate", "housing", "auto"], +1, "Interest rate cut"),
    ([r"\bfed\b.*(rate hike|raises rates)", r"\brbi\b.*(rate hike|hikes repo|raises repo)"],
     ["bank", "finance", "nbfc", "realty", "real estate", "housing"], -1, "Interest rate hike"),
    ([r"\btariffs?\b.*\b(india|indian)\b", r"\bh-?1b\b.*(fee|curb|restrict)"],
     ["software", "it services", "textile", "pharma", "auto ancillar", "gems"], -1, "Trade / visa curbs"),
    ([r"\bgold prices? (surge|rise|jump|record)"], ["jewel", "gold loan", "gems"], +1, "Gold price up"),
    ([r"\bmonsoon\b.*(deficient|weak|below normal)"], ["fertili", "agro", "tractor", "fmcg", "rural"], -1, "Weak monsoon"),
    ([r"\bmonsoon\b.*(above normal|normal|good|surplus)"], ["fertili", "agro", "tractor", "fmcg", "rural"], +1, "Good monsoon"),
    ([r"\bsteel\b.*(safeguard duty|anti-dumping)"], ["steel", "iron"], +1, "Steel import duty"),
    ([r"\bdefen[cs]e\b.*(budget|order|procurement|export)"], ["defence", "aerospace", "shipbuild"], +1, "Defence spending"),
]

_EV = {k: ([re.compile(p, re.I) for p in v[0]],) + v[1:] for k, v in EVENTS.items()}
_POS = [re.compile(r"\b" + re.escape(w) + r"\b", re.I) for w in POS]
_NEG = [re.compile(r"\b" + re.escape(w) + r"\b", re.I) for w in NEG]
_MACRO = [([re.compile(p, re.I) for p in m[0]],) + m[1:] for m in MACRO]

HORIZON_TEXT = {"short": "short term (days to weeks)", "long": "long term (months to years)",
                "both": "both short and long term"}


def tone(text):
    p = sum(1 for r in _POS if r.search(text))
    n = sum(1 for r in _NEG if r.search(text))
    return (p - n) / (p + n + 1), p, n


def classify(title, summary=""):
    """Return dict(event_type, label, sentiment, horizon, impact, explanation)."""
    text = f"{title}. {summary or ''}"
    best = None
    for key, (pats, base, horizon, weight, label) in _EV.items():
        if any(p.search(title) for p in pats):
            score = weight + 0.2            # headline match counts more
        elif any(p.search(text) for p in pats):
            score = weight
        else:
            continue
        if best is None or score > best[0]:
            best = (score, key, base, horizon, weight, label)
    t, p, n = tone(text)
    if best is None:
        key, base, horizon, weight, label = "general", 0.0, "short", 0.3, "General news"
    else:
        _, key, base, horizon, weight, label = best
    sentiment = max(-1.0, min(1.0, 0.5 * base + 0.8 * t if base else t * 1.1))
    impact = round(weight * (0.4 + abs(sentiment)), 3)
    direction = "positive" if sentiment > 0.15 else "negative" if sentiment < -0.15 else "neutral / unclear"
    expl = (f"{label}: looks {direction} for the share; effect is likely {HORIZON_TEXT[horizon]}. "
            f"({p} positive / {n} negative cue words)")
    return dict(event_type=key, label=label, sentiment=round(sentiment, 3), horizon=horizon,
                impact=impact, explanation=expl)


def macro_hits(text):
    """Global/macro themes in a headline -> list of (label, industry keywords, direction)."""
    return [(label, inds, d) for pats, inds, d, label in _MACRO if any(p.search(text) for p in pats)]
