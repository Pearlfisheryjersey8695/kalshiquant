"""
Strategy configuration registry.

Each strategy is a dataclass of parameters that the execution engine
and signal pipeline use for entry/exit logic and risk sizing.
Multiple strategies can be active simultaneously.
"""

import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger("kalshi.strategies")

# Lock for thread-safe strategy updates
_strategies_lock = threading.Lock()


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    allowed_regimes: frozenset  # which regimes activate this strategy
    min_edge: float             # minimum edge threshold (probability units, e.g. 0.05 = 5c)
    kelly_fraction: float       # multiplier on base half-Kelly
    stop_loss_pct: float        # max loss before exit (0.15 = 15%)
    take_profit_ratio: float    # reward:risk ratio for full TP
    max_hold_hours: float       # max time in position
    max_contracts: int          # per-position contract cap
    meta_gate: float            # minimum meta-model quality score
    # Triple-barrier (López de Prado AFML ch.3) — opt-in per strategy.
    # When True, the execution engine replaces fixed-pct stop/TP with
    # vol-scaled barriers using pt_sigma_mult / sl_sigma_mult.
    use_triple_barrier: bool = False
    pt_sigma_mult: float = 2.0   # take-profit barrier in sigma units
    sl_sigma_mult: float = 1.0   # stop-loss barrier in sigma units


STRATEGIES: dict[str, StrategyConfig] = {
    "convergence": StrategyConfig(
        name="convergence",
        allowed_regimes=frozenset({"CONVERGENCE"}),
        min_edge=0.05,
        kelly_fraction=0.50,
        stop_loss_pct=0.15,
        take_profit_ratio=2.0,
        max_hold_hours=4.0,
        max_contracts=500,
        meta_gate=0.30,
    ),
    "momentum": StrategyConfig(
        name="momentum",
        allowed_regimes=frozenset({"TRENDING"}),
        min_edge=0.08,
        kelly_fraction=0.35,
        stop_loss_pct=0.10,
        take_profit_ratio=2.5,
        max_hold_hours=2.0,
        max_contracts=300,
        meta_gate=0.40,
        # Trending markets need wider stops in high-vol regimes; vol-scaled
        # barriers handle this automatically.
        use_triple_barrier=True,
        pt_sigma_mult=2.5,
        sl_sigma_mult=1.0,
    ),
    "mean_reversion": StrategyConfig(
        name="mean_reversion",
        allowed_regimes=frozenset({"MEAN_REVERTING"}),
        min_edge=0.025,
        kelly_fraction=0.50,
        stop_loss_pct=0.12,
        take_profit_ratio=1.5,
        max_hold_hours=6.0,
        max_contracts=400,
        meta_gate=0.20,
        # Mean-reversion benefits most from σ-scaling: wide stop on volatile
        # markets prevents premature exit before reversion completes.
        use_triple_barrier=True,
        pt_sigma_mult=1.5,
        sl_sigma_mult=1.5,
    ),
    "event_driven": StrategyConfig(
        name="event_driven",
        allowed_regimes=frozenset({"TRENDING", "MEAN_REVERTING", "HIGH_VOLATILITY", "CONVERGENCE"}),
        min_edge=0.04,
        kelly_fraction=0.60,
        stop_loss_pct=0.20,
        take_profit_ratio=3.0,
        max_hold_hours=8.0,
        max_contracts=200,
        meta_gate=0.25,
    ),
    "arbitrage": StrategyConfig(
        name="arbitrage",
        allowed_regimes=frozenset({"CONVERGENCE", "MEAN_REVERTING", "TRENDING"}),
        min_edge=0.03,
        kelly_fraction=0.40,
        stop_loss_pct=0.10,
        take_profit_ratio=1.5,
        max_hold_hours=2.0,
        max_contracts=300,
        meta_gate=0.20,
    ),
    "parlay_arb": StrategyConfig(
        name="parlay_arb",
        # Parlays work in ANY regime — the edge is structural, not regime-dependent
        allowed_regimes=frozenset({"CONVERGENCE", "MEAN_REVERTING", "TRENDING", "HIGH_VOLATILITY", "UNKNOWN"}),
        min_edge=0.02,           # 2c min (parlays often have huge edges)
        kelly_fraction=0.30,     # conservative — parlays are complex
        stop_loss_pct=0.80,      # very wide stop — only exit on near-total loss
        take_profit_ratio=1.0,   # take profit at 1:1 (or hold to settlement)
        max_hold_hours=72.0,     # HOLD UP TO 3 DAYS — games need time to play
        max_contracts=200,       # cap per position
        meta_gate=0.10,          # low gate — parlay math IS the thesis
    ),
}


def select_strategies(regime: str, minutes_to_release: float = 999.0) -> list[StrategyConfig]:
    """Return applicable strategies for a given market state.

    A market can match multiple strategies simultaneously.
    Event-driven activates when an economic release is within 4 hours.
    Thread-safe.
    """
    with _strategies_lock:
        matched = []
        for config in STRATEGIES.values():
            if config.name == "event_driven":
                # Only activate near economic releases
                if minutes_to_release < 240 and regime in config.allowed_regimes:
                    matched.append(config)
            else:
                if regime in config.allowed_regimes:
                    matched.append(config)
        return matched


def get_strategy(name: str) -> StrategyConfig:
    """Look up a strategy config by name, defaulting to convergence. Thread-safe."""
    with _strategies_lock:
        return STRATEGIES.get(name, STRATEGIES["convergence"])


def update_strategy(name: str, **kwargs) -> StrategyConfig | None:
    """Update a strategy config at runtime. Thread-safe."""
    with _strategies_lock:
        if name not in STRATEGIES:
            return None
        old = STRATEGIES[name]
        # Build new config with updated fields
        fields = {
            "name": old.name,
            "allowed_regimes": old.allowed_regimes,
            "min_edge": old.min_edge,
            "kelly_fraction": old.kelly_fraction,
            "stop_loss_pct": old.stop_loss_pct,
            "take_profit_ratio": old.take_profit_ratio,
            "max_hold_hours": old.max_hold_hours,
            "max_contracts": old.max_contracts,
            "meta_gate": old.meta_gate,
        }
        for k, v in kwargs.items():
            if k in fields:
                if k == "allowed_regimes" and isinstance(v, (list, set)):
                    v = frozenset(v)
                fields[k] = v
        new_config = StrategyConfig(**fields)
        STRATEGIES[name] = new_config
        logger.info("Strategy %s updated: %s", name, kwargs)
        return new_config


def load_strategies_from_manager(strategy_manager) -> int:
    """Load strategy configs from the strategy manager's SQLite DB.
    Returns number of strategies synced.
    """
    if strategy_manager is None:
        return 0

    synced = 0
    for s in strategy_manager.list_strategies():
        name = s.get("type", s.get("name", "")).lower()
        if name not in STRATEGIES:
            continue

        risk = s.get("risk_limits", {})
        updates = {}

        # Map strategy manager fields to StrategyConfig fields
        # UI stores percentages as whole numbers (14 = 14%), engine uses decimals (0.14)
        if risk.get("stop_loss_pct") is not None:
            v = risk["stop_loss_pct"]
            updates["stop_loss_pct"] = v / 100.0 if v > 1 else v  # 14 -> 0.14
        if risk.get("take_profit_pct") is not None:
            v = risk["take_profit_pct"]
            updates["take_profit_ratio"] = v / 100.0 if v > 5 else v  # 40 -> 0.40 (used as ratio)
        if risk.get("kelly_fraction") is not None:
            v = risk["kelly_fraction"]
            updates["kelly_fraction"] = v if v <= 1 else v / 100.0  # already decimal or 50 -> 0.50
        if risk.get("max_open_positions") is not None:
            updates["max_contracts"] = int(risk["max_open_positions"])
        if risk.get("min_edge") is not None:
            v = risk["min_edge"]
            updates["min_edge"] = v / 100.0 if v > 0.5 else v  # 3 -> 0.03 (3 cents)
        if risk.get("min_confidence") is not None:
            v = risk["min_confidence"]
            updates["meta_gate"] = v if v <= 1 else v / 100.0  # 0.6 stays, 60 -> 0.60
        if risk.get("max_position_size") is not None:
            updates["max_contracts"] = int(risk["max_position_size"])

        if updates:
            update_strategy(name, **updates)
            synced += 1

    logger.info("Synced %d strategies from manager", synced)
    return synced
