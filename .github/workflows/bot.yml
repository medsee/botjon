import logging
import time
from datetime import date
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_trade_pct: float = 0.05        # 5% har savdoda
    max_open_positions: int = 5
    stop_loss_pct: float = 0.008       # 0.8%
    take_profit_pct: float = 0.015     # 1.5%
    max_daily_loss_pct: float = 0.05   # 5%
    max_daily_trades: int = 100
    min_volume_usdt: float = 200_000
    cooldown_after_loss: int = 30      # 30 soniya


@dataclass
class DailyStats:
    date: date = field(default_factory=date.today)
    total_pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    starting_balance: float = 0.0

    def reset_if_new_day(self, balance: float):
        today = date.today()
        if self.date != today:
            self.date = today
            self.total_pnl = 0.0
            self.trades = 0
            self.wins = 0
            self.losses = 0
            self.starting_balance = balance

    @property
    def win_rate(self):
        return self.wins / self.trades * 100 if self.trades else 0

    @property
    def loss_pct(self):
        if not self.starting_balance:
            return 0
        return abs(min(self.total_pnl, 0)) / self.starting_balance * 100


class RiskManager:
    def __init__(self, config: RiskConfig = None):
        self.cfg = config or RiskConfig()
        self.stats = DailyStats()
        self.last_loss_time: Optional[float] = None

    def set_starting_balance(self, balance: float):
        if not self.stats.starting_balance:
            self.stats.starting_balance = balance

    def can_trade(self, balance: float) -> tuple:
        self.stats.reset_if_new_day(balance)

        if self.last_loss_time:
            elapsed = time.time() - self.last_loss_time
            if elapsed < self.cfg.cooldown_after_loss:
                remaining = int(self.cfg.cooldown_after_loss - elapsed)
                return False, f"Cooldown: {remaining}s"

        if self.stats.loss_pct >= self.cfg.max_daily_loss_pct * 100:
            return False, f"Kunlik zarar limiti: {self.stats.loss_pct:.1f}%"

        if self.stats.trades >= self.cfg.max_daily_trades:
            return False, f"Kunlik savdolar limiti: {self.stats.trades}"

        return True, "OK"

    def calc_position_size(self, balance: float, price: float) -> float:
        max_usdt = balance * self.cfg.max_trade_pct
        max_usdt = max(max_usdt, 5.0)
        return max_usdt / price

    def calc_tp_sl(self, entry: float, side: str) -> tuple:
        if side == "BUY":
            tp = entry * (1 + self.cfg.take_profit_pct)
            sl = entry * (1 - self.cfg.stop_loss_pct)
        else:
            tp = entry * (1 - self.cfg.take_profit_pct)
            sl = entry * (1 + self.cfg.stop_loss_pct)
        return round(tp, 8), round(sl, 8)

    def record_trade(self, pnl: float):
        self.stats.trades += 1
        self.stats.total_pnl += pnl
        if pnl > 0:
            self.stats.wins += 1
        else:
            self.stats.losses += 1
            self.last_loss_time = time.time()

    def get_summary(self) -> str:
        s = self.stats
        sign = "+" if s.total_pnl >= 0 else ""
        return (
            f"Bugungi statistika:\n"
            f"Savdolar: {s.trades} (UD:{s.wins} ZR:{s.losses})\n"
            f"Win rate: {s.win_rate:.1f}%\n"
            f"PnL: {sign}{s.total_pnl:.4f} USDT"
        )
