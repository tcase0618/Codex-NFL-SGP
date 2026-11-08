"""Case NFL Quant Intelligence Platform.

Deterministic data -> metrics -> features -> models -> simulation pipeline.
No LLM or paid inference is used at runtime. Every stage records provenance.

Data tiers (see ``nflquant.provenance``):
    OBSERVED   values copied from a cited public source row
    DERIVED    deterministic formulas over observed rows
    MODELED    fitted estimates (ratings, shrunk rates)
    PREDICTED  pregame forecasts produced from information before kickoff
    SYNTHETIC  examples only; never mixed with the above
"""

__version__ = "1.0.0"
