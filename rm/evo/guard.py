"""MULTIPLE-TESTING GUARDRAIL. With N strategies tried, the best t-stat of N worthless ideas is about sqrt(2 ln N): a lucky winner,
not an edge. The bar a discovery must clear therefore rises with every trial ever made (the counter is permanent, never reset).
Expected maximum of N standard normals (Gumbel limit):  a = sqrt(2 ln N),  mu_N = a - (ln ln N + ln 4pi) / (2a),  scale b = 1/a,
sd = pi b / sqrt(6).  Bar: t_min(N) = max(2.5, mu_N + 1.645 sd)  (95% of the time pure luck stays below it)."""
import math

def null_max(N):
    if N < 3: return 0.0, 1.0
    a = math.sqrt(2 * math.log(N)); mu = a - (math.log(math.log(N)) + math.log(4 * math.pi)) / (2 * a)
    return mu, 1.0 / a

def t_min(N, base=2.5, z=1.645):
    mu, b = null_max(N); return max(base, mu + z * math.pi * b / math.sqrt(6))

def p_luck(t, N):
    """chance that the best of N worthless ideas reaches t (Gumbel): 1 - exp(-exp(-(t - mu)/b))"""
    mu, b = null_max(N)
    if N < 3: return 0.5 * math.erfc(t / math.sqrt(2))
    return 1.0 - math.exp(-math.exp(-(t - mu) / b))
