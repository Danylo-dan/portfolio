# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# Імпорти
import time
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import pytz
import config
import matplotlib.pyplot as plt
import matplotlib.dates as mdates 
import mplfinance as mpf
import os
import requests
from colorama import Fore, Style, init
import platform

init(autoreset=True, strip=False)

# Визначаємо київський часовий пояс
KYIV_TZ = pytz.timezone('Europe/Kiev')
# Налаштування папки відносно операційної системи
signals_dir = (
    r"C:\programmes\other\Arbitrage\Arbitrage\binance_spot_signals" if platform.system() == "Windows"
    else r"C:\programmes\other\Arbitrage\Arbitrage\binance_spot_signals"
)

# Налаштування
CONFIG = {
    "timeframe":                       "1m",         # Таймфрейм для аналізу (1 хвилина)
    "stop_loss_percent":                1.0,         # Рівень стоп-лосу у відсотках від ціни входу
    "rsi_period":                       14,          # Період RSI (Relative Strength Index)
    "rsi_overbought":                   70,          # Рівень перекупленості RSI
    "rsi_oversold":                     30,          # Рівень перепроданості RSI
    "rsi_bars_count":                   100,         # Кількість свічок для розрахунку RSI
    "commission_rate":                  0.001,       # Комісія біржі за угоду (0.1%)
    "initial_balance":                  100.0,       # Початковий баланс для симуляції або торгівлі
    "check_interval":                   60,          # Інтервал перевірки сигналів (в секундах)
    "monitor_interval":                 1,           # Загальний інтервал моніторингу (в секундах)
    "monitor_interval_rsi_high":        60,          # Інтервал моніторингу, якщо RSI високий (в секундах)
    "monitor_interval_rsi_low":         10,          # Інтервал моніторингу, якщо RSI низький (в секундах)
    "rsi_high_threshold":               70,          # Поріг RSI для високого рівня
    "rsi_high_max":                     100,         # Максимальне значення RSI, що вважається "дуже високим"
    "rsi_low_threshold":                30,          # Поріг RSI для низького рівня
    "rsi_low_min":                      0,           # Мінімальне значення RSI, що вважається "дуже низьким"
    "rsi_buy_exit_threshold":           40,          # Поріг RSI для виходу з позиції при купівлі (take-profit)
    "min_volume":                       10000000,    # Мінімальний обсяг торгів для активації сигналу
    "post_sell_delay":                  60,          # Затримка після продажу перед новим входом (в секундах)
    "volume_filter_update_interval":    10800,       # Інтервал оновлення фільтра за обсягом (в секундах)
    "min_profit_percent":               0.20,        # Мінімальний прибуток у відсотках для фіксації
    "profit_drop_percent":              0.10,        # Допустиме падіння прибутку перед фіксацією (трейлінг)
    "lookback_right":                   5,           # Кількість свічок справа для дивергенції (сигнальна зона)
    "lookback_left":                    5,           # Кількість свічок зліва для дивергенції (референтна зона)
    "range_upper":                      60,          # Верхній діапазон для пошуку свічок дивергенції
    "range_lower":                      5,           # Нижній діапазон для пошуку свічок дивергенції
    "candles":                          1,           # Зменшено до 1 для коротких угод
    "signals_dir":                      signals_dir,  # Використовуємо змінну
    "blacklist_symbols":                ["BNB/USDT", "LAYER/USDT", "BTC/USDT", "ADA/USDT", "EUR/USDT"],  # Ігноровані пари
    "whitelist_symbols":              [],     #Валюти для аналізу"
}

class VirtualBinanceRSITrader:
    def __init__(self):
        self.exchange = ccxt.binance({
            'apiKey': config.API_KEYS['binance']['apiKey'],
            'secret': config.API_KEYS['binance']['secret'],
            'enableRateLimit': True,
            'adjustForTimeDifference': True,
            'options': {'defaultType': 'spot'}
        })
        self.balance = {"USDT": CONFIG["initial_balance"], "ASSET": 0.0}
        self.holding_symbol = None
        self.holding_amount = 0.0
        self.holding_price = 0.0
        self.holding_time = None
        self.asset = None
        self.last_sell_time = None
        self.signal_history = []
        self.filtered_symbols = []
        self.last_volume_filter_update = None
        self.max_profit_percent = 0.0
        self.rsi_monitoring_mode = False
        self.rsi_buy_monitoring_mode = False
        self.min_sell_monitoring_mode = False
        self.previous_rsi = None
        self.previous_rsi_buy = None
        self.monitoring_symbol = None
        self.was_oversold = False
        self.trade_history = []
        self.buy_amount_usdt = 0.0
        self.initial_buy_amount_usdt = 0.0

    def send_telegram_message(self, message):
        telegram_url = f"https://api.telegram.org/bot{config.TOKEN}/sendMessage"
        payload = {
            "chat_id": config.CHANNEL_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(telegram_url, json=payload)
            if response.status_code == 200:
                print(Fore.GREEN + f"📩 Повідомлення надіслано в Telegram-канал Cripto Lux Value: {message}")
                return True
            else:
                print(Fore.RED + f"❌ Помилка надсилання повідомлення в Telegram: HTTP {response.status_code}, {response.text}")
                return False
        except Exception as e:
            print(Fore.RED + f"❌ Помилка надсилання повідомлення в Telegram: {e}")
            return False

    def send_telegram_photo(self, photo_path, caption):
        telegram_url = f"https://api.telegram.org/bot{config.TOKEN}/sendPhoto"
        try:
            with open(photo_path, 'rb') as photo:
                files = {'photo': photo}
                payload = {
                    "chat_id": config.CHANNEL_ID,
                    "caption": caption,
                    "parse_mode": "Markdown"
                }
                response = requests.post(telegram_url, data=payload, files=files)
                if response.status_code == 200:
                    print(Fore.GREEN + f"📸 Зображення надіслано в Telegram-канал Cripto Lux Value: {photo_path}")
                    return True
                else:
                    print(Fore.RED + f"❌ Помилка надсилання зображення в Telegram: HTTP {response.status_code}, {response.text}")
                    return False
        except Exception as e:
            print(Fore.RED + f"❌ Помилка надсилання зображення в Telegram: {e}")
            return False

    def sync_time_with_server(self):
        try:
            server_time = self.exchange.fetch_time()
            server_time_ms = server_time
            local_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            time_diff_ms = local_time_ms - server_time_ms
            print(Fore.BLUE + f"📘 Різниця часу між локальним і серверним часом: {time_diff_ms} мс")
            self.exchange.options['timeDifference'] = time_diff_ms
        except Exception as e:
            print(Fore.RED + f"❌ Помилка синхронізації часу з сервером: {e}")

    def test_authentication(self):
        try:
            self.exchange.fetch_balance()
            print(Fore.GREEN + "✅ API ключі валідні.")
            return True
        except ccxt.AuthenticationError as e:
            print(Fore.RED + f"❌ Помилка автентифікації: {e}")
            return False
        except Exception as e:
            print(Fore.RED + f"❌ Помилка перевірки автентифікації: {e}")
            return False

    def get_spot_pairs(self):
        try:
            self.exchange.load_markets()
            symbols = [
                symbol for symbol, market in self.exchange.markets.items()
                if market['quote'] == 'USDT' and market['active'] and market['spot']
            ]
            return symbols
        except Exception as e:
            print(Fore.RED + f"❌ Помилка отримання торгових пар: {e}")
            return []

    def is_market_active(self, symbol):
        try:
            self.exchange.load_markets()
            market = self.exchange.markets.get(symbol)
            if market is None:
                print(Fore.YELLOW + f"⚠️ {symbol}: Валютна пара не знайдена на біржі.")
                return False
            is_active = market.get('active', False)
            if not is_active:
                print(Fore.YELLOW + f"⚠️ {symbol}: Торгівля закрита (неактивна).")
            return is_active
        except Exception as e:
            print(Fore.RED + f"❌ Помилка перевірки статусу ринку для {symbol}: {e}")
            return False

    def update_volume_filter(self):
        print(Fore.CYAN + f"🔍 \n Оновлення фільтра обсягу торгівлі...")
        symbols = self.get_spot_pairs()
        filtered_symbols = []
        for symbol in symbols:
            try:
                if not self.is_market_active(symbol):
                    continue
                ticker = self.exchange.fetch_ticker(symbol)
                quote_volume = ticker['quoteVolume']
                if quote_volume >= CONFIG["min_volume"]:
                    filtered_symbols.append(symbol)
                    print(Fore.LIGHTYELLOW_EX + f"🟡 {symbol}: Обсяг {quote_volume:.6f} USDT, додано до фільтру.")
                else:
                    print(Fore.YELLOW + f"⚠️ {symbol}: Обсяг {quote_volume:.6f} USDT < {CONFIG['min_volume']} USDT, виключено.")
            except Exception as e:
                print(Fore.RED + f"❌ Помилка отримання обсягу для {symbol}: {e}")
        self.filtered_symbols = filtered_symbols
        self.last_volume_filter_update = datetime.now(KYIV_TZ)
        print(Fore.GREEN + f"✅ Фільтр оновлено: {len(self.filtered_symbols)} пар із достатнім обсягом.")

    def calculate_rma(self, data, period):
        rma = np.zeros(len(data))
        rma[0] = np.mean(data[:period]) if len(data) >= period else data[0]
        for i in range(1, len(data)):
            rma[i] = (rma[i-1] * (period - 1) + data[i]) / period
        return rma

    def calculate_rsi(self, closes, period=CONFIG["rsi_period"]):
        if len(closes) < period + 1:
            print(Fore.YELLOW + f"⚠️ Недостатньо даних для RSI: {len(closes)} свічок, потрібно {period + 1}.")
            return None 
        print(Fore.CYAN + f"🔍 Останні ціни закриття для RSI: {closes[-5:]}")
        
        changes = np.diff(closes)
        up = np.maximum(changes, 0)
        down = -np.minimum(changes, 0)
        
        avg_up = self.calculate_rma(up, period)
        avg_down = self.calculate_rma(down, period)

        rs = np.where(avg_down < 1e-10, np.inf, avg_up / avg_down)
        rsi = np.where(avg_down == 0, 100, np.where(avg_up == 0, 0, 100 - (100 / (1 + rs))))
        
        print(Fore.MAGENTA + f"🧠 RSI розрахований: {rsi[-1]:.6f}, RS: {rs[-1]:.6f}, avg_up: {avg_up[-1]:.6f}, avg_down: {avg_down[-1]:.6f}")
        return rsi

    def pivot_low(self, data, left, right):
        pivots = np.full(len(data), False)
        for i in range(left, len(data) - right):
            is_pivot = True
            for j in range(1, left + 1):
                if data[i] >= data[i - j]:
                    is_pivot = False
                    break
            for j in range(1, right + 1):
                if data[i] >= data[i + j]:
                    is_pivot = False
                    break
            if is_pivot:
                pivots[i] = True
        return pivots

    def pivot_high(self, data, left, right):
        pivots = np.full(len(data), False)
        for i in range(left, len(data) - right):
            is_pivot = True
            for j in range(1, left + 1):
                if data[i] <= data[i - j]:
                    is_pivot = False
                    break
            for j in range(1, right + 1):
                if data[i] <= data[i + j]:
                    is_pivot = False
                    break
            if is_pivot:
                pivots[i] = True
        return pivots

    def bars_since(self, condition):
        bars = np.full(len(condition), 0, dtype=int)
        count = 0
        for i in range(len(condition) - 1, -1, -1):
            if condition[i]:
                count = 0
            else:
                count += 1
            bars[i] = count
        return bars

    def in_range(self, bars):
        return (bars >= CONFIG["range_lower"]) & (bars <= CONFIG["range_upper"])

    def value_when(self, condition, source, occurrence):
        values = np.full(len(source), np.nan)
        count = 0
        for i in range(len(condition)):
            if condition[i]:
                count += 1
                if count == occurrence + 1:
                    values[i:] = source[i]
                    break
        return values

    def detect_divergence(self, symbol, closes, lows, highs, rsi_values):
        prev_pl_idx = None
        lookback_left = CONFIG["lookback_left"]
        lookback_right = CONFIG["lookback_right"]

        pl_found = self.pivot_low(rsi_values, lookback_left, lookback_right)
        ph_found = self.pivot_high(rsi_values, lookback_left, lookback_right)

        rsi_lbr = np.roll(rsi_values, lookback_right)
        low_lbr = np.roll(lows, lookback_right)
        high_lbr = np.roll(highs, lookback_right)

        pl_indices = np.where(pl_found)[0]
        bull_cond = np.full(len(rsi_values), False)
        if len(pl_indices) >= 2:
            for idx in pl_indices[-1:]:
                prev_pl_idx = pl_indices[pl_indices < idx][-1] if len(pl_indices[pl_indices < idx]) > 0 else None
                if prev_pl_idx is not None:
                    bars = idx - prev_pl_idx
                    if CONFIG["range_lower"] <= bars <= CONFIG["range_upper"]:
                        rsi_hl = rsi_lbr[idx] > rsi_lbr[prev_pl_idx]
                        price_ll = low_lbr[idx] < low_lbr[prev_pl_idx]
                        bull_cond[idx] = rsi_hl and price_ll
                        if bull_cond[idx]:
                            print(Fore.GREEN + f"✅ {symbol}: Виявлено Regular Bullish Divergence на свічці {idx}: RSI HL={rsi_lbr[idx]:.2f} > {rsi_lbr[prev_pl_idx]:.2f}, Price LL={low_lbr[idx]:.6f} < {low_lbr[prev_pl_idx]:.6f}")

        ph_indices = np.where(ph_found)[0]
        bear_cond = np.full(len(rsi_values), False)
        if len(ph_indices) >= 2:
            for idx in ph_indices[-1:]:
                prev_ph_idx = ph_indices[ph_indices < idx][-1] if len(ph_indices[ph_indices < idx]) > 0 else None
                if prev_ph_idx is not None:
                    bars = idx - prev_pl_idx
                    if CONFIG["range_lower"] <= bars <= CONFIG["range_upper"]:
                        rsi_lh = rsi_lbr[idx] < rsi_lbr[prev_pl_idx]
                        price_hh = high_lbr[idx] > high_lbr[prev_pl_idx]
                        bear_cond[idx] = rsi_lh and price_hh
                        if bear_cond[idx]:
                            print(Fore.LIGHTRED_EX + f"🔥 {symbol}: Виявлено Regular Bearish Divergence на свічці {idx}: RSI LH={rsi_lbr[idx]:.2f} < {rsi_lbr[prev_pl_idx]:.2f}, Price HH={high_lbr[idx]:.6f} > {high_lbr[prev_pl_idx]:.6f}")

        return bull_cond, bear_cond

    def get_rsi_and_divergence(self, symbol, timeframe=CONFIG["timeframe"], period=CONFIG["rsi_period"], bars_count=CONFIG["rsi_bars_count"]):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=bars_count)
            if not ohlcv or len(ohlcv) < period + 1:
                print(Fore.YELLOW + f"⚠️ {symbol}: Немає або недостатньо даних OHLCV ({len(ohlcv) if ohlcv else 0} свічок), RSI не розраховується.")
                return None, None, None

            timestamps = [datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc).astimezone(KYIV_TZ) for candle in ohlcv]
            closes = np.array([candle[4] for candle in ohlcv], dtype=np.float64)
            lows = np.array([candle[3] for candle in ohlcv], dtype=np.float64)
            highs = np.array([candle[2] for candle in ohlcv], dtype=np.float64)

            if np.all(closes == closes[0]) or np.any(np.isnan(closes)) or np.any(np.isinf(closes)):
                print(Fore.YELLOW + f"⚠️ {symbol}: Некоректні дані OHLCV, RSI не розраховується.")
                return None, None, None

            rsi_values = self.calculate_rsi(closes, period)
            if rsi_values is None:
                return None, None, None

            bull_cond, bear_cond = self.detect_divergence(symbol, closes, lows, highs, rsi_values)

            return rsi_values[-1], bull_cond[-1], bear_cond[-1]
        except Exception as e:
            print(Fore.RED + f"❌ Помилка отримання RSI для {symbol}: {e}")
            return None, None, None

    def get_order_book_price(self, symbol, amount_usdt, side):
        if amount_usdt <= 0 or (side == 'BUY' and self.balance["USDT"] <= 0):
            print(Fore.RED + f"❌ {symbol}: Недостатній баланс USDT: {self.balance['USDT']:.6f}")
            return None, 0
        try:
            order_book = self.exchange.fetch_order_book(symbol, limit=100)
            orders = order_book['asks'] if side == 'BUY' else order_book['bids']
            total_qty = 0.0
            total_cost = 0.0
            order_count = 0
            for price, qty in orders:
                order_count += 1
                if side == 'BUY':
                    qty_usdt = qty * price
                    if total_cost + qty_usdt > amount_usdt:
                        qty_usdt = amount_usdt - total_cost
                        qty = qty_usdt / price
                    total_cost += qty_usdt
                    total_qty += qty
                else:
                    qty_needed = amount_usdt / price
                    if total_qty + qty > qty_needed:
                        qty = qty_needed - total_qty
                    total_qty += qty
                    total_cost += qty * price
                if total_cost >= amount_usdt or (side == 'SELL' and total_qty >= amount_usdt / price):
                    break
            if total_qty <= 0 or order_count == 0:
                print(Fore.YELLOW + f"⚠️ {symbol}: Недостатньо ліквідності в книзі ордерів.")
                return None, 0
            return total_cost / total_qty, order_count
        except Exception as e:
            print(Fore.RED + f"❌ Помилка отримання книги ордерів для {symbol}: {e}")
            return None, 0

    def calculate_potential_profit(self, symbol, current_price, order_count):
        if not current_price or current_price <= 0 or self.holding_amount <= 0:
            print(Fore.YELLOW + f"⚠️ Некоректні дані для розрахунку прибутку: ціна={current_price}, кількість={self.holding_amount}")
            return 0.0
        qty = self.holding_amount
        sell_commission_qty = qty * CONFIG["commission_rate"] * order_count
        sell_amount_usdt = (qty - sell_commission_qty) * current_price
        buy_amount_usdt = self.buy_amount_usdt
        profit = sell_amount_usdt - buy_amount_usdt 
        return profit

    def calculate_profit_percent(self, current_price, order_count):
        if not current_price or current_price <= 0 or self.holding_amount <= 0:
            return 0.0
        qty = self.holding_amount
        sell_commission_qty = qty * CONFIG["commission_rate"] * order_count
        sell_amount_usdt = (qty - sell_commission_qty) * current_price
        buy_amount_usdt = self.buy_amount_usdt
        if buy_amount_usdt == 0:
            return 0.0
        profit_percent = (sell_amount_usdt - buy_amount_usdt) / buy_amount_usdt * 100
        return profit_percent

    def plot_chart(self, symbol, signal_type, signal_price, signal_time, profit=None, bar_count=None, buy_time=None):
        filename = None
        
        timeframe = CONFIG["timeframe"]

        print(Fore.BLUE + f"📘 Починаємо генерацію графіка для {symbol}, сигнал: {signal_type}, тривалість угоди: {(signal_time - buy_time).total_seconds() if buy_time else 0:.2f} секунд")

        # Визначаємо таймфрейм залежно від тривалості угоди Виявлено Regular Bullish Divergence
        if signal_type == 'SELL' and buy_time:
            if buy_time.tzinfo is None:
                buy_time = buy_time.astimezone(KYIV_TZ)
            duration_seconds = (signal_time - buy_time).total_seconds()
            if duration_seconds < 60:  # Менше 1 хвилини
                timeframe = "1s"
                print(Fore.BLUE + f"📘 Використовуємо 1-секундні свічки для угоди тривалістю {duration_seconds:.2f} секунд.")
            elif 60 <= duration_seconds < 600:  #з 1 до 10 хвилин
                timeframe = "1m"
                print(Fore.BLUE + f"📘 Використовуємо 1-хвилинні свічки для угоди тривалістю {duration_seconds/60:.2f} хвилин.")
            else:  # 10 хвилин і більше
                print(Fore.BLUE + f"📘 Використовуємо 1-хвилинні свічки для угоди тривалістю {duration_seconds/60:.2f} хвилин.")
            
            # Розширюємо діапазон запиту даних
            since = int(buy_time.timestamp() * 1000) 
            print(Fore.BLUE + f"📘 Запит OHLCV з since={since} для таймфрейму {timeframe}")
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=100)
            if not ohlcv or len(ohlcv) < CONFIG["candles"]:
                print(Fore.YELLOW + f"⚠️ {symbol}: Немає або недостатньо даних OHLCV ({len(ohlcv) if ohlcv else 0} свічок) для таймфрейму {timeframe}. Спробуємо 1m.")
                timeframe = "1m"
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=100)
                if not ohlcv or len(ohlcv) < CONFIG["candles"]:
                    print(Fore.YELLOW + f"⚠️ {symbol}: Немає даних OHLCV навіть для 1m. Генеруємо мінімальний графік.")
                    # Створюємо мінімальний DataFrame з однією свічкою
                    ticker = self.exchange.fetch_ticker(symbol)
                    current_price = ticker['last']
                    timestamp = int(signal_time.timestamp() * 1000)
                    ohlcv = [[timestamp, signal_price, signal_price, signal_price, signal_price, 0]]
        else:
            print(Fore.BLUE + f"📘 Запит OHLCV для сигналу купівлі, таймфрейм {timeframe}")
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=CONFIG["rsi_bars_count"])

        if not ohlcv or len(ohlcv) == 0:
            print(Fore.RED + f"❌ {symbol}: Немає даних OHLCV для створення графіка. Пропускаємо.")
            return

        print(Fore.BLUE + f"📘 Отримано {len(ohlcv)} свічок. Перший таймстемп: {ohlcv[0][0]}, Останній таймстемп: {ohlcv[-1][0]}")

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # Перевіряємо валідність даних OHLCV
        if df.empty or df['close'].dropna().empty or df['high'].dropna().empty or df['low'].dropna().empty:
            print(Fore.RED + f"❌ {symbol}: DataFrame порожній або не має валідних OHLC-даних. Генеруємо мінімальний графік.")
            timestamp = int(signal_time.timestamp() * 1000)
            df = pd.DataFrame([[timestamp, signal_price, signal_price, signal_price, signal_price, 0]],
                              columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Перевіряємо, чи всі значення OHLC однакові
        if (df['open'] == df['high']).all() and (df['high'] == df['low']).all() and (df['low'] == df['close']).all():
            print(Fore.YELLOW + f"⚠️ {symbol}: Усі значення OHLC однакові. Додаємо невеликий розкид для коректного графіка.")
            df['high'] = df['high'] + 0.000001
            df['low'] = df['low'] - 0.000001

        # Перевіряємо таймстемпи
        if df['timestamp'].iloc[0] < 1000000000000:
            print(Fore.YELLOW + f"⚠️ Некоректні таймстемпи, генеруємо нові на основі signal_time.")
            signal_time_ms = int(signal_time.timestamp() * 1000)
            timeframe_ms = 1000 if timeframe == "1s" else 60 * 1000
            timestamps = [signal_time_ms - (len(df) - 1 - i) * timeframe_ms for i in range(len(df))]
            df['timestamp'] = timestamps

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert('Europe/Kiev')
        df.set_index('timestamp', inplace=True)

        closes = df['close'].values
        if len(ohlcv) >= CONFIG["rsi_period"] + 1:
            rsi_values = self.calculate_rsi(closes, CONFIG["rsi_period"])
            if rsi_values is not None:
                expected_rsi_length = len(closes) - 1
                if len(rsi_values) == expected_rsi_length:
                    df = df.iloc[1:]
                    df['rsi'] = rsi_values
                else:
                    print(Fore.YELLOW + f"⚠️ Некоректна довжина RSI ({len(rsi_values)}), очікувалося {expected_rsi_length}. Пропускаємо RSI.")
                    df['rsi'] = 0.0
            else:
                df['rsi'] = 0.0
        else:
            print(Fore.YELLOW + f"⚠️ {symbol}: Недостатньо свічок ({len(ohlcv)}) для RSI, пропускаємо RSI.")
            df['rsi'] = 0.0

        support_resistance = []

        recent_high = df['high'][-20:].max() if len(df) >= 20 else df['high'].max() if not df['high'].empty else signal_price
        recent_low = df['low'][-20:].min() if len(df) >= 20 else df['low'].min() if not df['low'].empty else signal_price

        support_resistance = [
            mpf.make_addplot([recent_high] * len(df), color='red', linestyle='--'),
            mpf.make_addplot([recent_low] * len(df), color='green', linestyle='--')
        ]

        apd = []
        if signal_type == 'BUY':
            signal_idx = df.index[-1] if signal_time > df.index[-1] else signal_time
            apd.append(mpf.make_addplot([signal_price if idx == signal_idx else np.nan for idx in df.index],
                                        type='scatter', markersize=100, marker='^', color='green'))
        elif signal_type == 'SELL' and buy_time:
            buy_idx = df.index[0] if buy_time < df.index[0] else buy_time
            sell_idx = df.index[-1] if signal_time > df.index[-1] else signal_time
            if buy_idx in df.index and sell_idx in df.index and buy_idx < sell_idx:
                apd.append(mpf.make_addplot([self.holding_price if idx == buy_idx else np.nan for idx in df.index],
                                            type='scatter', markersize=100, marker='^', color='green'))
                apd.append(mpf.make_addplot([signal_price if idx == sell_idx else np.nan for idx in df.index],
                                            type='scatter', markersize=100, marker='v', color='red'))
                line_data = pd.Series(index=df.index, dtype=float)
                line_data[buy_idx:sell_idx] = np.linspace(self.holding_price, signal_price, len(line_data[buy_idx:sell_idx]))
                apd.append(mpf.make_addplot(line_data, color='blue', linestyle='--'))
            else:
                print(Fore.YELLOW + f"⚠️ Неможливо побудувати лінію купівлі-продажу: buy_idx={buy_idx}, sell_idx={sell_idx}, df.index[0]={df.index[0]}, df.index[-1]={df.index[-1]}")

        rsi_plot = mpf.make_addplot(df['rsi'], panel=1, color='purple', ylabel='RSI')
        rsi_overbought = mpf.make_addplot([CONFIG["rsi_overbought"]] * len(df), panel=1, color='red', linestyle='--')
        rsi_oversold = mpf.make_addplot([CONFIG["rsi_oversold"]] * len(df), panel=1, color='green', linestyle='--')

        total_volume = df['volume'].sum()
        bar_count = len(df) if bar_count is None else bar_count

        text = f"Монета: {symbol}\n"
        text += f"Сигнал: {signal_type}\n"
        text += f"Таймфрейм: {timeframe}\n"
        if signal_type == 'SELL' and buy_time:
            profit_percent = ((signal_price - self.holding_price) / self.holding_price) * 100
            text += f"Ціна купівлі: {self.holding_price:.6f} USDT\n"
            text += f"Ціна продажу: {signal_price:.6f} USDT\n"
            text += f"Профіт: {profit:.6f} USDT ({profit_percent:.2f}%)\n"
        else:
            text += f"Ціна: {signal_price:.6f} USDT\n"
        text += f"Обсяг: {total_volume:.6f}\n"
        text += f"Бари: {bar_count}\n"
        text += f"Підтримка: {recent_low:.6f}\n"
        text += f"Опір: {recent_high:.6f}\n"
        text += f"Час (Київ): {signal_time.astimezone(KYIV_TZ).strftime('%Y-%m-%d %H:%M:%S')}"

        custom_style = mpf.make_mpf_style(
            base_mpf_style='nightclouds',
            rc={'axes.labelcolor': 'white', 'xtick.color': 'white', 'ytick.color': 'white'},
            marketcolors=mpf.make_marketcolors(
                up='#00FF00',
                down='#FF0000',
                edge={'up': '#00FF00', 'down': '#FF0000'},
                wick={'up': 'white', 'down': 'white'},
                volume='gray'
            )
        )

        try:
            if signal_type == 'BUY':
                fig, axes = mpf.plot(df, type='candle', style=custom_style,
                                     addplot=apd + [rsi_plot, rsi_overbought, rsi_oversold] + support_resistance,
                                     volume=False, figsize=(12, 8), returnfig=True)
            else: 
                fig, axes = mpf.plot(df, type='candle', style=custom_style,
                                     addplot=apd + support_resistance,
                                     volume=False, figsize=(12, 8), returnfig=True)

        except Exception as e:
            print(Fore.RED + f"❌ Помилка побудови графіка для {symbol}: {e}")
            print(Fore.YELLOW + f"⚠️ Спробуємо створити мінімальний графік із однією свічкою.")
            # Створюємо мінімальний DataFrame
            timestamp = int(signal_time.timestamp() * 1000)
            df = pd.DataFrame([[timestamp, signal_price, signal_price + 0.000001, signal_price - 0.000001, signal_price, 0]],
                              columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert('Europe/Kiev')
            df.set_index('timestamp', inplace=True)
            df['rsi'] = np.nan
            apd = []
            if signal_type == 'BUY':
                apd.append(mpf.make_addplot([signal_price], type='scatter', markersize=100, marker='^', color='green'))
            elif signal_type == 'SELL' and buy_time:
                apd.append(mpf.make_addplot([self.holding_price], type='scatter', markersize=100, marker='^', color='green'))
                apd.append(mpf.make_addplot([signal_price], type='scatter', markersize=100, marker='v', color='red'))
            try:
                fig, axes = mpf.plot(df, type='candle', style=custom_style,
                                     addplot=apd + [rsi_plot, rsi_overbought, rsi_oversold],
                                     volume=False, figsize=(12, 8), returnfig=True)
            except Exception as e:
                print(Fore.RED + f"❌ Не вдалося створити навіть мінімальний графік для {symbol}: {e}")
                plt.close('all')
                return

        axes[0].tick_params(axis='x', rotation=45)
        axes[0].text(0.02, 0.98, text, transform=axes[0].transAxes, fontsize=10,
                     verticalalignment='top', bbox=dict(facecolor='black', alpha=0.8, edgecolor='white'))

        base_dir = CONFIG["signals_dir"]
        date_dir = datetime.now(KYIV_TZ).strftime('%Y_%m_%d')
        symbol_dir = symbol.replace('/', '_')
        chart_dir = os.path.join(base_dir, date_dir, symbol_dir)
        try:
            if not os.path.exists(base_dir):
                print(Fore.YELLOW + f"⚠️ Базова директорія {base_dir} не існує, створюємо її.")
            if not os.access(base_dir, os.W_OK):
                raise PermissionError(f"Немає прав на запис у директорію {base_dir}")
            os.makedirs(chart_dir, exist_ok=True)
            filename = os.path.join(chart_dir, f"{symbol_dir}_{signal_type.lower()}_{datetime.now(KYIV_TZ).strftime('%Hh_%Mm_%Ss')}.png")
            plt.savefig(filename)
            print(Fore.GREEN + f"✅ Графік збережено як {filename} для {symbol} із сигналом {signal_type}.")
        except Exception as e:
            print(Fore.RED + f"❌ Не вдалося зберегти графік {filename if 'filename' in locals() else 'None'}: {e}")
            plt.close(fig)
            return

        # Відправляємо зображення в Telegram
        telegram_caption = (
            f"📊 *Графік сигналу {signal_type} для {symbol}*\n"
            f"Таймфрейм: {timeframe}\n"
            f"Час (Київ): {signal_time.astimezone(KYIV_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if signal_type == 'SELL' and buy_time:
            profit_percent = ((signal_price - self.holding_price) / self.holding_price) * 100
            telegram_caption += f"\nПрофіт: {profit:.6f} USDT ({profit_percent:.2f}%)"
        if not self.send_telegram_photo(filename, telegram_caption):
            print(Fore.RED + f"❌ Не вдалося надіслати зображення в Telegram для {symbol}, сигнал {signal_type}.")

        plt.close(fig)
        plt.close('all')

    def execute_buy(self, symbol, price, order_count):
        if not self.is_market_active(symbol):
            print(Fore.YELLOW + f"⚠️ {symbol}: Ринок закритий, купівля неможлива.")
            return

        amount_usdt = self.balance["USDT"]
        if amount_usdt <= 0:
            print(Fore.RED + f"❌ Недостатній віртуальний баланс USDT: {amount_usdt:.6f}")
            return

        qty = amount_usdt / price
        commission_qty_per_order = qty * CONFIG["commission_rate"]
        total_commission_qty = commission_qty_per_order * order_count
        qty_net = qty - total_commission_qty

        buy_amount_usdt = qty_net * price

        self.balance["USDT"] -= amount_usdt
        self.balance["ASSET"] = qty_net
        self.holding_symbol = symbol
        self.holding_amount = qty_net
        self.holding_price = price
        self.holding_time = datetime.now(KYIV_TZ)
        self.asset = symbol.split('/')[0]
        self.buy_amount_usdt = buy_amount_usdt
        self.initial_buy_amount_usdt = buy_amount_usdt
        self.max_profit_percent = 0.0
        self.rsi_monitoring_mode = False
        self.rsi_buy_monitoring_mode = False
        self.previous_rsi = None
        self.previous_rsi_buy = None
        self.monitoring_symbol = None
        self.was_oversold = False

        print(
            Fore.GREEN + f"✅ \n[КУПІВЛЯ {symbol}] {self.holding_time}\n" +
            Fore.GREEN + f"Куплено на баланс: {amount_usdt:.6f} USDT\n" +
            Fore.GREEN + f"Кількість: {qty_net:.6f} {self.asset} за {price:.6f} USDT\n" +
            Fore.YELLOW + f"Комісія: {total_commission_qty:.6f} {self.asset} (за {order_count} ордер(ів))\n" +
            Fore.GREEN + f"Кількість ордерів: {order_count}\n" +
            Fore.GREEN + f"Баланс: {self.balance['USDT']:.6f} USDT, {self.balance['ASSET']:.6f} {self.asset}\n"
        )

        trade_record = {
            "type": "BUY",
            "symbol": symbol,
            "price": price,
            "quantity": qty_net,
            "amount_usdt": amount_usdt,
            "commission": total_commission_qty,
            "timestamp": self.holding_time,
            "balance_usdt": self.balance["USDT"],
            "balance_asset": self.balance["ASSET"]
        }
        self.trade_history.append(trade_record)

        self.plot_chart(symbol, 'BUY', price, self.holding_time)

        telegram_message = (
            f"📈 *СИГНАЛ КУПІВЛІ*\n"
            f"Монета: {symbol}\n"
            f"Ціна: {price:.6f} USDT\n"
            f"Кількість: {qty_net:.6f} {self.asset}\n"
            f"Сума: {amount_usdt:.6f} USDT\n"
            f"Комісія: {total_commission_qty:.6f} {self.asset}\n"
            f"Кількість ордерів: {order_count}\n"
            f"Баланс: {self.balance['USDT']:.6f} USDT, {self.balance['ASSET']:.6f} {self.asset}\n"
            f"Час (Київ): {self.holding_time.strftime('%m-%d %H:%M:%S')}"
        )
        self.send_telegram_message(telegram_message)

    def execute_sell(self, symbol, price, order_count):
        if not self.is_market_active(symbol):
            print(Fore.YELLOW + f"⚠️ {symbol}: Ринок закритий, продаж неможливий.")
            return

        qty = self.holding_amount
        if qty <= 0 or order_count <= 0:
            print(Fore.RED + f"❌ Немає активів для продажу: {qty:.6f} {self.asset}, або некоректна кількість ордерів: {order_count}")
            self.reset_position()
            return

        commission_qty_per_order = qty * CONFIG["commission_rate"]
        total_commission_qty = commission_qty_per_order * order_count
        qty_net = qty - total_commission_qty
        amount_usdt = qty_net * price
        profit = self.calculate_potential_profit(symbol, price, order_count)
        holding_duration = (datetime.now(KYIV_TZ) - self.holding_time).total_seconds() / 60.0
        bar_count = int(holding_duration) if holding_duration >= 1 else 1

        total_seconds = int(holding_duration * 60)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        duration_str = ""
        if hours > 0:
            duration_str += f"{hours} год "
        if minutes > 0 or hours > 0:
            duration_str += f"{minutes} хв "
        duration_str += f"{seconds} сек"

        self.balance["USDT"] += amount_usdt
        self.balance["ASSET"] -= qty
        if self.balance["ASSET"] < 0:
            self.balance["ASSET"] = 0.0

        profit_color = Fore.LIGHTGREEN_EX if profit > 0 else Fore.LIGHTRED_EX
        print(
            Fore.LIGHTRED_EX + f"🔥 \n[ПРОДАЖ {symbol}] {datetime.now(KYIV_TZ)}\n" +
            Fore.LIGHTRED_EX + f"Кількість: {qty_net:.6f} {self.asset} за {price:.6f} USDT\n" +
            Fore.YELLOW + f"Комісія: {total_commission_qty:.6f} {self.asset} (за {order_count} ордер(ів))\n" +
            Fore.LIGHTRED_EX + f"Кількість ордерів: {order_count}\n" +
            Fore.BLUE + f"Утримувано: {duration_str}\n" +
            profit_color + f"Чистий прибуток: {profit:.6f} USDT\n" +
            Fore.LIGHTRED_EX + f"Баланс: {self.balance['USDT']:.6f} USDT, {self.balance['ASSET']:.6f} {self.asset}\n"
        )

        trade_record = {
            "type": "SELL",
            "symbol": symbol,
            "price": price,
            "quantity": qty_net,
            "amount_usdt": amount_usdt,
            "profit": profit,
            "timestamp": datetime.now(KYIV_TZ),
            "balance_usdt": self.balance["USDT"],
            "balance_asset": self.balance["ASSET"]
        }
        self.trade_history.append(trade_record)

        total_profit = self.balance["USDT"] - CONFIG['initial_balance']
        profit_percent = (total_profit / CONFIG["initial_balance"]) * 100
        total_profit_color = Fore.LIGHTGREEN_EX if total_profit > 0 else Fore.LIGHTRED_EX
        print(total_profit_color + f"💹 Загальний прибуток: {total_profit:.6f} USDT ({profit_percent:.2f}%)")

        today = datetime.now(KYIV_TZ).date()
        trades_today = sum(1 for trade in self.trade_history if trade["timestamp"].date() == today and trade["type"] == "SELL")
        print(Fore.BLUE + f"📘 Кількість угод за день ({today}): {trades_today}")

        # Генеруємо графік навіть для коротких угод 
        try:
            self.plot_chart(symbol, 'SELL', price, datetime.now(KYIV_TZ), profit=profit, bar_count=bar_count, buy_time=self.holding_time)
        except Exception as e:
            print(Fore.RED + f"❌ Помилка генерації графіка для продажу {symbol}: {e}")

        # Надсилаємо повідомлення в Telegram
        telegram_message = (
            f"📉 *СИГНАЛ ПРОДАЖУ*\n"
            f"Монета: {symbol}\n"
            f"Ціна купівлі: {self.holding_price:.6f} USDT\n"
            f"Ціна продажу: {price:.6f} USDT\n"
            f"Кількість: {qty_net:.6f} {self.asset}\n"
            f"Комісія: {total_commission_qty:.6f} {self.asset}\n"
            f"Кількість ордерів: {order_count}\n"
            f"Профіт: {profit:.6f} USDT\n"
            f"Утримувано: {duration_str}\n"
            f"Баланс: {self.balance['USDT']:.6f} USDT, {self.balance['ASSET']:.6f} {self.asset}\n"
            f"Час (Київ): {datetime.now(KYIV_TZ).strftime('%m-%d %H:%M:%S')}"
        )
        self.send_telegram_message(telegram_message)

        self.reset_position()

    def reset_position(self):
        self.holding_symbol = None
        self.holding_amount = 0.0
        self.holding_price = 0.0
        self.holding_time = None
        self.asset = None
        self.buy_amount_usdt = 0.0
        self.initial_buy_amount_usdt = 0.0
        self.last_sell_time = datetime.now(KYIV_TZ)
        self.max_profit_percent = 0.0
        self.rsi_monitoring_mode = False
        self.rsi_buy_monitoring_mode = False
        self.min_sell_monitoring_mode = False
        self.previous_rsi = None
        self.previous_rsi_buy = None
        self.monitoring_symbol = None
        self.was_oversold = False

    def run(self):
        self.sync_time_with_server()
        if not self.test_authentication():
            print(Fore.RED + "❌ Зупинка через помилку автентифікації.")
            return

        print(Fore.WHITE + "💬 Запуск віртуального RSI трейдера Binance...")
        print(Fore.WHITE + f"💬 Початковий віртуальний баланс: {self.balance['USDT']:.6f} USDT")

        if not CONFIG["whitelist_symbols"]:
            self.update_volume_filter()
        else: 
            self.filtered_symbols = CONFIG["whitelist_symbols"]

        while True:
            if not CONFIG["whitelist_symbols"]:
                if self.last_volume_filter_update is None or \
                    (datetime.now(KYIV_TZ) - self.last_volume_filter_update).total_seconds() >= CONFIG["volume_filter_update_interval"]:
                    self.update_volume_filter()
            else:
                self.filtered_symbols = CONFIG["whitelist_symbols"]

            if not self.holding_symbol or self.holding_amount <= 0:
                self.holding_symbol = None
                if self.last_sell_time:
                    time_since_sell = (datetime.now(KYIV_TZ) - self.last_sell_time).total_seconds()
                    if time_since_sell < CONFIG["post_sell_delay"]:
                        print(Fore.YELLOW + f"⚠️ Чекаємо {CONFIG['post_sell_delay'] - time_since_sell:.2f} секунд після останнього продажу.")
                        time.sleep(CONFIG["post_sell_delay"] - time_since_sell)
                        continue

                if not self.filtered_symbols:
                    print(Fore.YELLOW + f"⚠️ Немає пар із достатнім обсягом для аналізу. Очікування наступного оновлення фільтра.")
                    time.sleep(CONFIG["check_interval"])
                    continue

                if self.rsi_buy_monitoring_mode and self.monitoring_symbol:
                    symbols_to_analyze = [self.monitoring_symbol]
                    print(Fore.CYAN + f"🔍 \n Моніторимо лише {self.monitoring_symbol}...")
                else:
                    symbols_to_analyze = self.filtered_symbols
                    print(Fore.CYAN + f"🔍 \n Аналіз відфільтрованих символів ({len(self.filtered_symbols)} пар)...")

                for symbol in symbols_to_analyze:
                    if symbol in CONFIG["blacklist_symbols"]:
                        print(Fore.YELLOW + f"⚠️ {symbol}: Пропущено через чорний список.")
                        continue

                    if not self.is_market_active(symbol):
                        continue

                    try:
                        ticker = self.exchange.fetch_ticker(symbol)
                        quote_volume = ticker['quoteVolume']
                        price, order_count = self.get_order_book_price(symbol, self.balance["USDT"], 'BUY')
                        current_price = price if price else ticker['last']
                        rsi, bull_divergence, bear_divergence = self.get_rsi_and_divergence(symbol)

                        if rsi is not None:
                            print(
                                Fore.MAGENTA + f"🧠 {symbol}: RSI={rsi:.6f}, " +
                                Fore.CYAN + f"Ціна={current_price:.6f} USDT, " +
                                Fore.LIGHTYELLOW_EX + f"Обсяг={quote_volume:.6f} USDT"
                            )
                        else:
                            print(Fore.YELLOW + f"⚠️ {symbol}: Не вдалося отримати RSI, " +
                                  Fore.LIGHTYELLOW_EX + f"Обсяг={quote_volume:.6f} USDT")
                            continue

                        if rsi <= CONFIG["rsi_low_threshold"]:
                            self.was_oversold = True
                            if not self.rsi_buy_monitoring_mode:
                                self.rsi_buy_monitoring_mode = True
                                self.monitoring_symbol = symbol
                                print(Fore.MAGENTA + f"🧠 RSI для {symbol} досяг {rsi:.6f}, переходимо в режим моніторингу купівлі кожну хвилину.")
                                break

                        if self.rsi_buy_monitoring_mode and self.monitoring_symbol == symbol and self.was_oversold:
                            if rsi <= CONFIG["rsi_buy_exit_threshold"]:
                                if self.previous_rsi_buy is not None and rsi > self.previous_rsi_buy:
                                    print(Fore.GREEN + f"✅ [СИГНАЛ КУПІВЛІ] RSI зріс з {self.previous_rsi_buy:.6f} до {rsi:.6f} після зони перепроданості.")
                                    price, order_count = self.get_order_book_price(symbol, self.balance["USDT"], 'BUY')
                                    if price and price > 0 and order_count > 0:
                                        self.execute_buy(symbol, price, order_count)
                                        break
                                else:
                                    print(
                                        Fore.CYAN + f"🔍 \n[МОНІТОРИНГ RSI після зони перепроданості {symbol}] {datetime.now(KYIV_TZ)}\n" +
                                        Fore.MAGENTA + f"RSI: {rsi:.6f} (Попередній RSI: {self.previous_rsi_buy if self.previous_rsi_buy is not None else 'N/A'})\n" +
                                        Fore.CYAN + f"Поточна ціна: {current_price:.6f} USDT\n" +
                                        Fore.LIGHTYELLOW_EX + f"Обсяг: {quote_volume:.6f} USDT\n"
                                    )
                            else:
                                self.rsi_buy_monitoring_mode = False
                                self.monitoring_symbol = None
                                self.previous_rsi_buy = None
                                self.was_oversold = False
                                print(Fore.YELLOW + f"⚠️ RSI для {symbol} досяг {rsi:.6f}, що вище {CONFIG['rsi_buy_exit_threshold']}, режим моніторингу купівлі вимкнено.")

                        if bull_divergence:
                            print(Fore.GREEN + f"✅ [СИГНАЛ КУПІВЛІ] Виявлено Regular Bullish Divergence для {symbol}.")
                            price, order_count = self.get_order_book_price(symbol, self.balance["USDT"], 'BUY')
                            if price and price > 0 and order_count > 0:
                                self.execute_buy(symbol, price, order_count)
                                break

                        if self.rsi_buy_monitoring_mode and self.monitoring_symbol == symbol:
                            self.previous_rsi_buy = rsi

                    except Exception as e:
                        print(Fore.RED + f"❌ Помилка обробки {symbol}: {e}")

                time.sleep(CONFIG["monitor_interval"])

            else:
                if self.holding_symbol and self.holding_amount > 0:
                    if not self.is_market_active(self.holding_symbol):
                        print(Fore.YELLOW + f"⚠️ {self.holding_symbol}: Торгівля закрита. Продаємо позицію.")
                        price, order_count = self.get_order_book_price(self.holding_symbol, self.holding_amount * self.holding_price, 'SELL')
                        if price and price > 0 and order_count > 0:
                            self.execute_sell(self.holding_symbol, price, order_count)
                        continue

                    try:
                        rsi, bull_divergence, bear_divergence = self.get_rsi_and_divergence(self.holding_symbol)
                        if rsi is None:
                            print(Fore.YELLOW + f"⚠️ {self.holding_symbol}: Не вдалося отримати RSI, пропускаємо моніторинг.")
                            time.sleep(CONFIG["monitor_interval"])
                            continue

                        ticker = self.exchange.fetch_ticker(self.holding_symbol)
                        last_price = ticker['last']
                        price, order_count = self.get_order_book_price(self.holding_symbol, self.holding_amount * self.holding_price, 'SELL')
                        price = min(price, last_price) if price and last_price else (price or last_price)

                        if price and price > 0 and order_count > 0:
                            potential_profit = self.calculate_potential_profit(self.holding_symbol, price, order_count)
                            profit_percent = self.calculate_profit_percent(price, order_count)
                            holding_duration = (datetime.now(KYIV_TZ) - self.holding_time).total_seconds() / 60.0
                            stop_loss_price = self.holding_price * (1 - CONFIG["stop_loss_percent"] / 100)
                            price_change_percent = (price - self.holding_price) / self.holding_price * 100
                            price_change_usdt = price - self.holding_price

                            if profit_percent > self.max_profit_percent:
                                self.max_profit_percent = profit_percent

                            profit_color = Fore.LIGHTGREEN_EX if potential_profit > 0 else Fore.LIGHTRED_EX
                            price_change_color = Fore.LIGHTGREEN_EX if price_change_percent > 0 else Fore.LIGHTRED_EX
                            profit_percent_color = Fore.LIGHTGREEN_EX if profit_percent > 0 else Fore.LIGHTRED_EX
                            print(
                                Fore.CYAN + f"🔍 \n[МОНІТОРИНГ ПОЗИЦІЇ {self.holding_symbol}] {datetime.now(KYIV_TZ)}\n" +
                                Fore.MAGENTA + f"RSI: {rsi:.6f}\n" +
                                Fore.CYAN + f"Ціна купівлі: {self.holding_price:.6f} USDT\n" +
                                Fore.CYAN + f"Поточна ціна: {price:.6f} USDT\n" +
                                price_change_color + f"Зміна ціни: {price_change_percent:+.6f}%\n" +
                                price_change_color + f"Зміна ціни: {price_change_usdt:+.6f} USDT\n" +
                                Fore.YELLOW + f"Стоп-лос ціна: {stop_loss_price:.6f} USDT\n" +
                                Fore.BLUE + f"Утримувано: {holding_duration:.2f} хвилин\n" +
                                profit_color + f"Потенційний прибуток: {potential_profit:.6f} USDT\n" +
                                profit_percent_color + f"Чистий профіт: {profit_percent:.6f}%\n" +
                                profit_percent_color + f"Максимальний профіт: {self.max_profit_percent:.6f}%\n" +
                                Fore.CYAN + f"Баланс: {self.balance['USDT']:.6f} USDT, {self.balance['ASSET']:.6f} {self.asset}\n"
                            )

                            if price <= stop_loss_price:
                                print(f"[СТОП-ЛОС] Ціна впала до {price:.6f} <= {stop_loss_price:.6f} ({CONFIG['stop_loss_percent']}% збитку).")
                                self.execute_sell(self.holding_symbol, price, order_count)

                            if profit_percent >= CONFIG["min_profit_percent"]:
                                self.min_sell_monitoring_mode = True
                                print(Fore.YELLOW + f"⚠️🔥Профіт досяг {profit_percent:.6f}, переходимо в режим моніторингу падіння на 0.10% і більше.")

                            if self.min_sell_monitoring_mode:
                                profit_drop = self.max_profit_percent - profit_percent
                                if profit_drop >= CONFIG["profit_drop_percent"]:
                                    print(f"[СИГНАЛ ПРОДАЖУ] Профіт упав на {profit_drop:.6f}% від максимуму {self.max_profit_percent:.6f}%.")
                                    self.execute_sell(self.holding_symbol, price, order_count)

                            if self.holding_symbol and self.holding_amount > 0:
                                if rsi >= CONFIG["rsi_high_threshold"] and not self.rsi_monitoring_mode:
                                    self.rsi_monitoring_mode = True
                                    print(Fore.MAGENTA + f"🧠 RSI досяг {rsi:.6f}, переходимо в режим моніторингу кожну хвилину.")

                                if self.rsi_monitoring_mode:
                                    if CONFIG["rsi_high_threshold"] <= rsi <= CONFIG["rsi_high_max"]:
                                        if self.previous_rsi is not None and rsi < self.previous_rsi:
                                            print(Fore.LIGHTRED_EX + f"🔥 [СИГНАЛ ПРОДАЖУ] RSI впав з {self.previous_rsi:.6f} до {rsi:.6f} у діапазоні {CONFIG['rsi_high_threshold']}–{CONFIG['rsi_high_max']}.")
                                            self.execute_sell(self.holding_symbol, price, order_count)
                                        else:
                                            print(
                                                Fore.CYAN + f"🔍 \n[МОНІТОРИНГ RSI {CONFIG['rsi_high_threshold']}–{CONFIG['rsi_high_max']} {self.holding_symbol}] {datetime.now(KYIV_TZ)}\n" +
                                                Fore.MAGENTA + f"RSI: {rsi:.6f} (Попередній RSI: {self.previous_rsi if self.previous_rsi is not None else 'N/A'})\n" +
                                                Fore.CYAN + f"Ціна купівлі: {self.holding_price:.6f} USDT\n" +
                                                Fore.CYAN + f"Поточна ціна: {price:.6f} USDT\n" +
                                                price_change_color + f"Зміна ціни: {price_change_percent:+.6f}%\n" +
                                                price_change_color + f"Зміна ціни: {price_change_usdt:+.6f} USDT\n" +
                                                Fore.YELLOW + f"Стоп-лос ціна: {stop_loss_price:.6f} USDT\n" +
                                                Fore.BLUE + f"Утримувано: {holding_duration:.2f} хвилин\n" +
                                                profit_color + f"Потенційний прибуток: {potential_profit:.6f} USDT\n" +
                                                profit_percent_color + f"Чистий профіт: {profit_percent:.6f}%\n" +
                                                profit_percent_color + f"Максимальний профіт: {self.max_profit_percent:.6f}%\n" +
                                                Fore.CYAN + f"Баланс: {self.balance['USDT']:.6f} USDT, {self.balance['ASSET']:.6f} {self.asset}\n"
                                            )
                                    elif rsi < CONFIG["rsi_high_threshold"] and self.previous_rsi is not None and self.previous_rsi >= CONFIG["rsi_high_threshold"]:
                                        print(Fore.LIGHTRED_EX + f"🔥 [СИГНАЛ ПРОДАЖУ] RSI впав нижче порогу {CONFIG['rsi_high_threshold']} (з {self.previous_rsi:.6f} до {rsi:.6f}).")
                                        self.execute_sell(self.holding_symbol, price, order_count)

                                if self.rsi_monitoring_mode:
                                    self.previous_rsi = rsi

                        else:
                            print(Fore.YELLOW + f"⚠️ {self.holding_symbol}: Не вдалося отримати ціну для моніторингу.")

                    except Exception as e:
                        print(Fore.RED + f"❌ Помилка моніторингу {self.holding_symbol}: {e}")
                        time.sleep(CONFIG["monitor_interval"])
                        continue

                time.sleep(CONFIG["monitor_interval"])

if __name__ == "__main__":
    trader = VirtualBinanceRSITrader()
    trader.run()