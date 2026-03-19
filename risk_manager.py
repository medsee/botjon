"""
Risk Manager — botni minusdan saqlaydi
- Max kunlik zarar chegarasi
- Har pozitsiya uchun Stop-Loss va Take-Profit
- Max ochiq pozitsiyalar soni
- Pozitsiya hajmi nazorati
"""
import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    # Kapital
    max_trade_pct: float = 0.03       # Har savdoda balansning max 3%
    max_open_positions: int = 3        # Bir vaqtda max 3 ta ochiq pozitsiya

    # Stop-Loss / Take-Profit
    stop_loss_pct: float = 0.008       # 0.8% Stop-Loss
    take_profit_pct: float = 0.015     # 1.5% Take-Profit

    # Kunlik limitlar
    max_daily_loss_pct: float = 0.05   # Kuniga max 5% zarar
    max_daily_trades: int = 50         # Kuniga max 50 ta savdo

    # Qo'shimcha himoya
    min_volume_usdt: float = 500_000   # Juftlik kunlik hajmi min 500k USDT
    min_price_change_1m: float = 0.001 # Min 0.1% harakat 1 daqiqada
    max_spread_pct: float = 0.003      # Max spread 0.3%
    cooldown_after_loss: int = 60      # Zarar bo'lsa 60 soniya kutish


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
            logger.info("Yangi kun — statistika yangilandi")

    @property
    def win_rate(self) -> float:
        if self.trades == 0:
            return 0.0
        return self.wins / self.trades * 100

    @property
    def loss_pct(self) -> float:
        if self.starting_balance == 0:
            return 0.0
        return abs(min(self.total_pnl, 0)) / self.starting_balance * 100


class RiskManager:
    def __init__(self, config: RiskConfig = None):
        self.cfg = config or RiskConfig()
        self.stats = DailyStats()
        self.last_loss_time: Optional[float] = None

    def set_starting_balance(self, balance: float):
        if self.stats.starting_balance == 0:
            self.stats.starting_balance = balance

    def can_trade(self, balance: float) -> tuple[bool, str]:
        """Savdo qilish mumkinmi tekshirish"""
        self.stats.reset_if_new_day(balance)

        # Cooldown tekshirish
        if self.last_loss_time:
            import time
            elapsed = time.time() - self.last_loss_time
            if elapsed < self.cfg.cooldown_after_loss:
                remaining = int(self.cfg.cooldown_after_loss - elapsed)
                return False, f"⏳ Zarar so'ng {remaining}s kutish"

        # Kunlik zarar limiti
        if self.stats.loss_pct >= self.cfg.max_daily_loss_pct * 100:
            return False, f"🛑 Kunlik zarar limiti: {self.stats.loss_pct:.1f}%"

        # Kunlik savdolar soni
        if self.stats.trades >= self.cfg.max_daily_trades:
            return False, f"🛑 Kunlik savdolar limiti: {self.stats.trades}"

        return True, "✅"

    def calc_position_size(self, balance: float, price: float) -> float:
        """Pozitsiya hajmini hisoblash"""
        max_usdt = balance * self.cfg.max_trade_pct
        # Kamida 5 USDT
        max_usdt = max(max_usdt, 5.0)
        qty = max_usdt / price
        return qty

    def calc_tp_sl(self, entry_price: float, side: str) -> tuple[float, float]:
        """Take-Profit va Stop-Loss hisoblash"""
        if side == "BUY":
            tp = entry_price * (1 + self.cfg.take_profit_pct)
            sl = entry_price * (1 - self.cfg.stop_loss_pct)
        else:
            tp = entry_price * (1 - self.cfg.take_profit_pct)
            sl = entry_price * (1 + self.cfg.stop_loss_pct)
        return round(tp, 8), round(sl, 8)

    def record_trade(self, pnl: float):
        """Savdo natijasini qayd qilish"""
        import time
        self.stats.trades += 1
        self.stats.total_pnl += pnl
        if pnl > 0:
            self.stats.wins += 1
        else:
            self.stats.losses += 1
            self.last_loss_time = time.time()

    def get_summary(self) -> str:
        s = self.stats
        return (
            f"📊 Bugungi statistika:\n"
            f"  Savdolar: {s.trades} (✅{s.wins} ❌{s.losses})\n"
            f"  Win rate: {s.win_rate:.1f}%\n"
            f"  PnL: {'+'if s.total_pnl>=0 else ''}{s.total_pnl:.4f} USDT\n"
            f"  Zarar: {s.loss_pct:.2f}%"
        )
