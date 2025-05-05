# Примусове встановлення з параметром --break-system-packages
import math
import ccxt.async_support as ccxt
import talib
import asyncio
import time
import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy.stats import linregress
import random 
from datetime import datetime, timezone
import pytz
import sys
import os
import Pairs_exchanges as pairs
import getting_pairs
import requests

from config import TOKEN, CHANNEL_ID  # Імпортуємо токен та CHAT_ID з config.py
#Перевірка наявності токенів
if TOKEN and CHANNEL_ID:
    bot = Bot(token=TOKEN)
else:
    print("Токени бота або ID чату не вказано, працюю в тестовому режимі...")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Київська часова зона
kiev_timezone = pytz.timezone('Europe/Kiev')

# Поточний час у Київському часовому поясі
kiev_time = datetime.now(kiev_timezone)

# Форматування часу у вигляді рядка
formatted_time = kiev_time.strftime('%Y-%m-%d %H:%M:%S')

print("Поточний час у Київському часі:", formatted_time)

#___Змінні для збереження___
# Змінні для відслідковування останніх бірж для купівлі та продажу
last_sell_exchange = None
balance = 100 # Початковий баланс

# Оголошуємо змінну для лічильника сигналів
signal_counter = 0

#Змінна обороту
daily_income = balance

#Відслідковування статистики 
signal_counts = 0 
profit_for_signals = 0 #Сума прибутку за 10 сигналів
profit_for_past_24h = 0 #сума прибутку за 24 години

profits_list_10_signals = [] #Список з прибутками за 10 сигналів для візуалізації
profits_list_24_hours = [] #Список зі всіма прибутками за 24 години для візуалізації

start_hour = kiev_time.hour
start_minute = kiev_time.minute

#Змінні для таймстемпів
buy_timestamp = None
sell_timestamp = None

#Біржі, використовувані за сесію
used_exchanges = set()

# Ініціалізація бота
bot = Bot(token=TOKEN)

#___СИСТЕМНІ_ФУНКЦІЇ_____________________________________________________________________________________________________________________

semaphore = asyncio.Semaphore(20)  # Обмеження кількості одночасних завдань

#___Функція для оброблення корутин з уникненням помилки___
async def fetch_with_retries(coro_func_or_coro, *args, retries=5, delay=10):
    async with semaphore:
        for i in range(retries):
           try:
                # Якщо передано функцію, викликаємо її з аргументами
               if callable(coro_func_or_coro):
                   coro = coro_func_or_coro(*args)
               else:
                    # Якщо передано корутину, використовуємо її без виклику
                   coro = coro_func_or_coro
            
                # Очікуємо результат корутини
               return await coro
           except Exception as e:
                if type(e) == ccxt.errors.BadSymbol:
                   raise
                if i < retries - 1:
                    print(f"Спроба {i + 1} не вдалася, повторюємо...: {e}")
                    await asyncio.sleep(delay)
                else:
                    print("Усі спроби вичерпано.")
                    raise

# Функція для виконання вашого оновлення
async def update_exchange_cache():
    print("Оновлення кешу бірж...")
    pairs.pairs_good_exchanges.clear()
async def update_spread_cache():
    print("Оновлення кешу даних про пари на біржах...")
    pairs.pairs_spread_volume_volatility.clear()

#Функція для закриття бірж в кінці циклу
async def close_exchanges(exchanges):
    for exchange in exchanges.values():
        try:
            await exchange.close()  # Закриття HTTP-з'єднань, сесій тощо
        except Exception as e:
            print(f"Помилка під час закриття біржі: {e}")

#___________________________________________________________________________________________________________________________________________
#Завантаження ринків для кожної біржі
async def loading_markets(exchanges):
    for x in range(1, 4):  # Три спроби
        print("Завантажуються ринки для кожної біржі...")
        try:
            tasks = [fetch_with_retries(exchange.load_markets) for exchange in exchanges.values()]
            await asyncio.gather(*tasks, return_exceptions=True)
            print("Завантажено ринки для всіх бірж")
            break  # Виходимо з циклу, якщо успішно
        except Exception as e:
            print(f"Помилка при завантаженні ринків (спроба {x}/3): {e}")
            if x == 3:
                raise
            await asyncio.sleep(10)
        finally:
            await close_exchanges(exchanges)

# Функція для перетворення секунд в години, хвилини та секунди
def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours}г.{minutes}хв.{seconds}сек."

#___Функція для фільтрації бірж для кожної пари___________________________
async def exchanges_for_pairs(exchanges, batch_size=10):
    print("Пошук і зберігання бірж торгівлі для кожної пари...")
    try:
        start_time = time.time()  # Час початку виконання функції
        total_pairs = len(pairs.currency_pairs)

        async def process_exchanges_pair(pair):
            exchanges_for_trade = {}

            # Список завдань для всіх бірж
            tasks = [
                process_exchange_for_pair(exchange_name, exchange, pair)
                for exchange_name, exchange in exchanges.items()
            ]

            # Виконання всіх завдань паралельно
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Обробка результатів
            for exchange_name, result in zip(exchanges.keys(), results):
                if isinstance(result, Exception):
                    print(f"Помилка для біржі {exchange_name}: {result}")
                elif result is not None:  # Якщо результат валідний
                    exchanges_for_trade.update(result)

            pairs.pairs_good_exchanges.update({pair: exchanges_for_trade})

        # Поділ пар на батчі
        for i in range(0, len(pairs.currency_pairs), batch_size):
            batch = pairs.currency_pairs[i:i + batch_size]

            # Створюємо завдання для батча
            pair_tasks = [process_exchanges_pair(pair) for pair in batch]

            # Виконуємо завдання для батча
            await asyncio.gather(*pair_tasks)

            # Обчислення часу виконання для цієї пари
            elapsed_time = time.time() - start_time
            remaining_time = (elapsed_time / (i + 1)) * (total_pairs - (i + 1))

            # Форматування часу
            formatted_remaining_time = format_time(remaining_time)

            print(f"{i + len(batch)}/{total_pairs} пар оброблено: Залишилось приблизно {formatted_remaining_time}")

        elapsed_time = time.time() - start_time
        print(f"Пошук і сортування бірж завершено за {elapsed_time:.2f} секунд, інформація актуалізована")
    finally:
        await close_exchanges(exchanges)

async def fetch_with_cache(cache, key, fetch_func, *args):
    """Функція для роботи з кешем із підтримкою TTL."""
    now = time.time()
    if key in cache and (now - cache[key]['timestamp'] < pairs.CACHE_TTL):
        return cache[key]['data']
    # Якщо кеш неактуальний або ключ відсутній, виконуємо API-запит
    data = await fetch_func(*args)
    cache[key] = {'data': data, 'timestamp': now}
    return data

async def fetch_with_cache_exchanges(cache, key, fetch_func, *args):
    """Перевіряє кеш перед виконанням API-запиту."""
    if key not in cache:
        cache[key] = await fetch_func(*args)
    return cache[key]

async def process_exchange_for_pair(exchange_name, exchange, pair):
    try:
        # Використання кешу для ринків і валют
        markets_key = f"{exchange}_markets"
        markets = await fetch_with_cache_exchanges(pairs.markets_cache, markets_key, fetch_with_retries, exchange.fetch_markets)

        currencies_key = f"{exchange}_currencies"
        currencies = await fetch_with_cache_exchanges(pairs.currencies_cache, currencies_key, fetch_with_retries, exchange.fetch_currencies)

        # Перевірка на наявність інформації
        if currencies is None:
            return None

        # Знаходження базової валюти
        base = None
        for market in markets:
            if market["symbol"] == pair:
                base = market["base"]
                break
        if not base or base not in currencies:
            return None

        # Перевірка мереж
        if not currencies[base].get("networks"):
            return None

        # Використання кешу для ордер буку
        orders = await fetch_with_retries(exchange.fetch_order_book, pair)
        bid = orders["bids"][0][0] if len(orders["bids"]) > 0 else None
        ask = orders["asks"][0][0] if len(orders["asks"]) > 0 else None
        spread = (ask - bid) if (bid and ask) else None
        if spread is None or spread >= 0.5:
            return None

        # Використання кешу для тікера
        ticker_key = f"{exchange_name}_{pair}_ticker"
        ticker = await fetch_with_cache(pairs.ticker_cache, ticker_key, fetch_with_retries, exchange.fetch_ticker, pair)
        quote_volume = ticker["quoteVolume"]
        if quote_volume is None or quote_volume < 200000:
            return None

        # Повертаємо результат у вигляді словника
        return {exchange_name: exchange}

    except Exception as e:
        print(f"Помилка під час обробки біржі {exchange_name} для пари {pair}: {e}")
        return None

#___________________________________________________________________________________________________________________

#___Функція для знаходження спреду, об`ємів і волатильності для кожної пари на доступних біржах і заповнення кешу___

async def spread_volume_volatility(batch_size=500):
    print("Оновлення інформації про об`єм, спред і волатильність валют...")

    async def process_exchange(pair, exchange_name, exchange):
        try:
            pair_info = {}
            # Ключі для кешу
            ticker_key = f"{exchange_name}_{pair}_ticker"

            # ___Обчислення спреду з кешуванням___
            order_book = await fetch_with_retries(exchange.fetch_order_book, pair)
            bid = order_book['bids'][0][0] if order_book['bids'] else None
            ask = order_book['asks'][0][0] if order_book['asks'] else None
            spread = (ask - bid) if ask and bid else None
            pair_info['spread'] = spread

            # ___Знаходження об'ємів з кешуванням___
            ticker = await fetch_with_cache(pairs.ticker_cache, ticker_key, fetch_with_retries, exchange.fetch_ticker, pair)
            pair_info['base_volume'] = ticker.get('baseVolume', None)  # Об'єм базової валюти
            pair_info['quote_volume'] = ticker.get('quoteVolume', None)  # Об'єм квоти

            # Повертаємо результат для конкретної біржі
            return exchange_name, pair_info

        except Exception as e:
            print(f"Помилка на біржі {exchange_name} для пари {pair}: {e}")
            return exchange_name, None

    async def process_every_pair(pair):
        exchanges_info = {}

        # Створюємо список завдань для кожної біржі, що підтримує дану пару
        tasks = [
            process_exchange(pair, exchange_name, exchange)
            for exchange_name, exchange in pairs.pairs_good_exchanges.get(pair, {}).items()
        ]

        # Виконуємо всі запити паралельно
        results = await asyncio.gather(*tasks)

        # Обробляємо результати
        for exchange_name, pair_info in results:
            if pair_info:  # Якщо немає помилки
                exchanges_info[exchange_name] = pair_info

        # Зберігаємо інформацію в кеш
        pairs.pairs_spread_volume_volatility[pair] = exchanges_info

    # Поділ пар на батчі
    for i in range(0, len(pairs.currency_pairs), batch_size):
        batch = pairs.currency_pairs[i:i + batch_size]

        # Створюємо список завдань для поточного батча
        pair_tasks = [process_every_pair(pair) for pair in batch]

        # Виконуємо обробку завдань для батча
        await asyncio.gather(*pair_tasks)

        print(f"Оброблено {i + len(batch)}/{len(pairs.currency_pairs)} пар.")

    print("Оновлення закінчено, інформація актуалізована.")

#__________________________________________________________________________________________________________

#Функція визначення профіту
async def orders_transaction(buy_order_book, sell_order_book, main_balance, withdraw_fee, deposit_fee):
    old_balance = main_balance #Старий баланс для порівняння
    new_balance = 0
    amount_to_buy = 0

    buy_prices = [] #Словник для збереження цін купівлі
    sell_prices = [] #Словник для збереження цін продажу

    for order in buy_order_book['asks']:
        if main_balance <= 1:
            break
        amount_in_order_usdt = order[1] * order[0]
        if amount_in_order_usdt >= main_balance:
            amount_to_buy += (main_balance / order[0]) - ((main_balance / order[0]) * 0.002) 

            buy_prices.append(order[0]) #Додавання ціни до списку
            break
        else:
            amount_to_buy += order[1] - order[1] * 0.002
            main_balance -= amount_in_order_usdt

            buy_prices.append(order[0]) #Додавання ціни до списку

    amount_to_buy -= withdraw_fee 
    amount_to_buy -= deposit_fee

    for order in sell_order_book['bids']:
        if order[1] >= amount_to_buy:
            new_balance += (amount_to_buy * order[0]) - ((amount_to_buy * order[0]) * 0.002)

            sell_prices.append(order[0]) #Додавання ціни до списку
            break
        else:
            new_balance += (order[1] * order[0]) - ((order[1] * order[0]) * 0.002)
            amount_to_buy -= order[1]

            sell_prices.append(order[0]) #Додавання ціни до списку

    return new_balance - old_balance, sum(buy_prices)/len(buy_prices), sum(sell_prices)/len(sell_prices)

# Функція для перевірки наявності пари на біржі
async def check_pair_on_exchange(exchange, pair):
    try:
        markets = await fetch_with_retries(exchange.load_markets)  # Завантажуємо ринки для біржі
        if pair in markets:
            return True
    except Exception as e:
        print(f"Помилка при перевірці пари {pair}: {e}")
    finally:
        if hasattr(markets, 'close'):
            await markets.close()
    return False

async def get_price(exchange, symbol):
    try:
        order_book = await fetch_with_retries(exchange.fetch_order_book, symbol)
        # Вибираємо найкращу ціну покупки (bids) або продажу (asks)
        best_bid = order_book['bids'][0][0] if order_book['bids'] else None
        best_ask = order_book['asks'][0][0] if order_book['asks'] else None
        return best_ask, best_bid  # Повертаємо найкращу ціну покупки та продажу
    except Exception as e:
        return None, None

#___Функція для визначення ціни на всіх біржах___
async def get_all_prices(pair):
    async def fetch_price(exchange_name, exchange):
        try:
            buy_price, sell_price = await get_price(exchange, pair)
            return exchange_name, buy_price, sell_price
        except Exception as e:
            print(f"Помилка на біржі {exchange_name} для {pair}: {e}")
            return exchange_name, None, None

    # Створюємо список завдань для кожної біржі
    tasks = [
        fetch_price(exchange_name, exchange)
        for exchange_name, exchange in pairs.pairs_good_exchanges.get(pair, {}).items()
    ]

    # Виконуємо всі завдання паралельно
    results = await asyncio.gather(*tasks)

    # Обробляємо результати
    valid_prices = {}
    prices = []
    for exchange_name, buy_price, sell_price in results:
        if buy_price is not None and sell_price is not None:
            valid_prices[exchange_name] = (buy_price, sell_price)
            prices.append((buy_price, sell_price))

    return valid_prices, prices

async def get_volatility_message(exchange, exchange_object, pair, pairs_data, high_threshold, low_threshold):
    """
    Обчислює волатильність для біржі та повертає відповідне повідомлення.
    
    :param exchange: назва біржі (наприклад, 'binance')
    :param exchange_object: об'єкт біржі
    :param pair: валютна пара (наприклад, 'BTC/USDT')
    :param pairs_data: словник даних про пари, включно з волатильністю
    :param high_threshold: поріг високої волатильності
    :param low_threshold: поріг низької волатильності
    :return: кортеж (волатильність, повідомлення)
    """
    if "volatility" in pairs_data[pair][exchange]:
        volatility = pairs_data[pair][exchange]["volatility"]
    else:
        prices = await get_historical_prices(exchange_object, pair)
        volatility = calculate_volatility(prices)
        pairs_data[pair][exchange]["volatility"] = volatility

    # Перевірка волатильності та формування повідомлення
    if volatility > high_threshold:
        message = "Висока волатильність — торгівля небезпечна!"
    elif volatility > low_threshold:
        message = "Волатильність допустима для торгівлі."
    else:
        message = "Низька волатильність — торгівля можлива."

    return volatility, message

# Функція для відправки повідомлення в Telegram канал з повідомленням про волатильність
async def send_telegram_message(message, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }

    # Якщо передано ID повідомлення, додаємо його до параметрів
    if reply_to_message_id:
        params['reply_to_message_id'] = reply_to_message_id

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()  # Отримуємо JSON-відповідь
                message_id = data.get("result", {}).get("message_id")  # Витягуємо ID повідомлення
                print(f"Повідомлення на Telegram канал відправлено: {message}")
                return message_id
            else:
                print(f"Помилка при відправці повідомлення: {response.status}")
                return None

#Функція відправки зображень в телеграм канал
async def send_telegram_image(image_path, reply_to_message_id=None):
    """Функція для надсилання зображення у Telegram"""
    try:
        with open(image_path, "rb") as photo:
            image_data = photo.read()
        input_file = BufferedInputFile(image_data, filename="profits_24h.png")

        async with Bot(token=TOKEN) as bot:
            await bot.send_photo(chat_id=CHANNEL_ID, photo=input_file, reply_to_message_id=reply_to_message_id)

        print('Графік успішно надіслано')
        os.remove(image_path)

    except Exception as e:
        print(f"Помилка під час надсилання зображення: {e}")

def load_background(image_path, width, height):
    """Завантажує фонове зображення та змінює його розмір"""
    bg = Image.open(image_path).convert("RGB")
    bg = bg.resize((width, height), Image.LANCZOS)
    return bg

def add_text(img, position, text, font, fill_color, outline_color="black", outline_width=2):
    """Додає текст з окантовкою на зображення"""
    draw = ImageDraw.Draw(img)
    x, y = position
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    draw.text(position, text, font=font, fill=fill_color)
    return img

def generate_trading_image(image_path, balance, profit, profit_percentage, buy_price, sell_price, buy_exchange, sell_exchange, pair):
    width, height = 800, 500
    img = load_background(image_path, width, height)  # Використовуємо фон

    balance = round(balance, 2)
    profit = round(profit, 2)
    profit_percentage = round(profit_percentage, 2)
    sell_price = round(sell_price, 4)
    buy_price = round(buy_price, 4)

    font_large = ImageFont.truetype("Montserrat-ExtraBold.ttf", 55)
    font_medium = ImageFont.truetype("Poppins-Bold.ttf", 40)
    font_small = ImageFont.truetype("Montserrat-Bold.ttf", 35)

    text_color = "lime" if profit_percentage > 0 else "red"

    add_text(img, (30, 20), "!Сигнал продажу!", font_large, "yellow")
    add_text(img, (80, 80), pair, font_medium, "white")
    add_text(img, (80, 130), f"{buy_exchange.capitalize()}-{sell_exchange.capitalize()}", font_medium, "white")
    add_text(img, (80, 180), f"{profit_percentage}%", font_medium, text_color)
    add_text(img, (80, 230), f"{profit} USDT", font_medium, text_color)
    add_text(img, (30, 305), f"Купівля за: {buy_price} USDT", font_small, text_color)
    add_text(img, (30, 340), f"Продаж за: {sell_price} USDT", font_small, text_color)
    add_text(img, (30, 390), f"Баланс: {balance} USDT", font_small, text_color)

    img.save("trading_result.png")

async def generate_price_chart(buy_exchange, sell_exchange, sell_exchange_name, buy_price, sell_price, pair, buy_timestamp, sell_timestamp):
    """Функція для отримання історичних цін і створення графіка від моменту купівлі до моменту продажу."""
    try:
        print(buy_timestamp)
        print(sell_timestamp)

        # Отримуємо історичні ціни у вказаному діапазоні часу
        sell_ohlcv = await sell_exchange.fetch_ohlcv(pair, timeframe='1m', since=buy_timestamp, limit=1000)
        sell_prices = [x[4] for x in sell_ohlcv]  # Ціна закриття (close)
        sell_timestamps = [x[0] for x in sell_ohlcv]  # Час у мілісекундах

        prices = [buy_price] + sell_prices[1:len(sell_prices)-1] + [sell_price]

        if not prices:
            print(f"Не вдалося отримати історичні ціни для {pair}")
            return None

        # Конвертуємо таймстемпи в datetime
        times = [datetime.fromtimestamp(t / 1000, timezone.utc).astimezone(kiev_timezone) for t in sell_timestamps if t]
        buy_time = datetime.fromtimestamp(buy_timestamp / 1000, timezone.utc).astimezone(kiev_timezone)
        
        # Використання темного стилю
        plt.style.use("dark_background")

        # Створюємо графік
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.patch.set_alpha(0.6)  # Прозорість області графіка
        ax.plot(times, prices, linestyle='-', color='cyan', label='Ціна')

        # Лінія купівлі
        ax.axhline(y=buy_price, color='lime', linestyle='--', linewidth=1.5, label='Рівень купівлі')

        # Визначаємо межі графіка
        max_price = max(prices) + (max(prices) * 0.02)  # Додаємо 20% запасу зверху
        min_price = min(prices) - (min(prices) * 0.02)  # Додаємо 20% запасу знизу

        # Заповнення області над або під лінією купівлі
        if prices[-1] > buy_price:
            ax.fill_between(times, buy_price, max_price, color='green', alpha=0.4)  # Зелене заповнення вище рівня купівлі
        else:
            ax.fill_between(times, min_price, buy_price, color='red', alpha=0.4)  # Червоне заповнення нижче рівня купівлі

        # Вертикальна лінія купівлі
        ax.axvline(x=times[-1], color='gray', linestyle='--', linewidth=1.5, label='Час купівлі')

        # Відзначаємо точки купівлі та продажу
        ax.scatter(times[0], buy_price, color='lime', s=100, label='Купівля')
        ax.scatter(times[-1], prices[-1], color='red', s=100, label='Продаж')

        # Додаємо текстові підписи
        ax.text(times[0], buy_price, f'{buy_price}', color='gold', fontsize=20, ha='left')
        ax.text(times[-1], prices[-1], f'{prices[-1]}', color='gold', fontsize=20, ha='left')

        # Налаштування формату часу на осі X (лише години та хвилини)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S', tz=kiev_timezone))

        # Оформлення
        ax.set_xlabel('Час', color='white')
        ax.set_ylabel('Ціна (USDT)', color='white')
        ax.set_title(f'Графік цін {pair} на біржі {sell_exchange_name} і точка купівлі', color='white')
        ax.grid(True, linestyle='--', linewidth=0.5, alpha=0.5)
        ax.tick_params(axis='x', colors='white', rotation=45)
        ax.tick_params(axis='y', colors='white')

        # Збереження графіка
        chart_path = "price_chart.png"
        plt.savefig(chart_path, dpi=300, bbox_inches='tight')

        plt.close()

    except Exception as e:
        print(f"Помилка під час створення графіка: {e}")
        return None
#_______________________________________________________________________________________________________

# Формуємо URL для біржі
def generate_exchange_url(exchange_name, pair):
    if exchange_name == 'binance':
        pair_for_url = pair.replace('/', '_')
        return f"https://www.binance.com/en/trade/{pair_for_url}"
    elif exchange_name == 'ascendex':
        pair_for_url = pair.lower()
        return f"https://ascendex.com/en/cashtrade-spottrading/{pair_for_url}"
    elif exchange_name == 'bingx':
        pair_for_url = pair.replace('/', '') 
        return f"https://bingx.com/en/spot/{pair_for_url}"
    elif exchange_name == 'bitget':
        pair_for_url = pair.replace('/', '') 
        return f"https://www.bitget.com/uk/spot/{pair_for_url}"
    elif exchange_name == 'bitmart':
        pair_for_url = pair.replace('/', '_')
        return f"https://www.bitmart.com/trade/en-US?symbol={pair_for_url}"
    elif exchange_name == 'bitrue':
        pair_for_url = pair.replace('/', '_')  
        pair_for_url = pair_for_url.lower()
        return f"https://www.bitrue.com/trade/{pair_for_url}"
    elif exchange_name == 'coinbase':
        pair_for_url = pair.replace('/', '-')
        return f"https://www.coinbase.com/advanced-trade/spot/{pair_for_url}"
    elif exchange_name == 'cryptocom':
        pair_for_url = pair.replace('/', '_')
        return f"https://crypto.com/exchange/trade/{pair_for_url}"
    elif exchange_name == 'exmo':
        pair_for_url = pair.replace('/', '_')
        return f"https://exmo.com/trade/pro/{pair_for_url}"
    elif exchange_name == 'gateio':
        pair_for_url = pair.replace('/', '_')
        return f"https://www.gate.io/uk/trade/{pair_for_url}"
    elif exchange_name == 'hitbtc':
        pair_for_url = pair.replace('/', '_for_')
        pair_for_url = pair_for_url.lower()
        return f"https://hitbtc.com/{pair_for_url}"
    elif exchange_name == 'htx':
        pair_for_url = pair.replace('/', '_')
        pair_for_url = pair_for_url.lower()
        return f"https://www.htx.com/trade/{pair_for_url}?type=spot"
    elif exchange_name == 'kucoin':
        pair_for_url = pair.replace('/', '-')  
        return f"https://www.kucoin.com/uk/trade/{pair_for_url}"
    elif exchange_name == 'mexc':
        pair_for_url = pair.replace('/', '_')
        return f"https://futures.mexc.com/uk-UA/exchange/{pair_for_url}"
    elif exchange_name == 'okx':
        pair_for_url = pair.replace('/', '-')
        pair_for_url = pair_for_url.lower()
        return f"https://www.okx.com/ua/trade-spot/btc-usdt{pair_for_url}"
    elif exchange_name == 'poloniex':
        pair_for_url = pair.replace('/', '_')
        return f"https://poloniex.com/trade/{pair_for_url}"
    elif exchange_name == 'whitebit':
        pair_for_url = pair.replace('/', '-')
        return f"https://whitebit.com/trade/{pair_for_url}?type=spot&tab=open-orders"
    elif exchange_name == 'xt':
        pair_for_url = pair.replace('/', '_')
        pair_for_url = pair_for_url.lower()
        return f"https://www.xt.com/en/trade/{pair_for_url}"
    else:
        return "Exchange not found."

# Функція для отримання історичних цін на валютну пару
async def get_historical_prices(exchange, symbol, timeframe='1m', limit=60):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [x[4] for x in ohlcv]  # Остання ціна закриття (close)
    except Exception as e:
        print(f"Помилка при отриманні історичних даних: {e}")
        return []

#___Розрахунок індикаторів_________________________________

def calculate_ema(prices, short_period=5, long_period=15, trend_period=5, threshold=0.001):
    prices = np.array(prices, dtype=float)
    
    # Функція для розрахунку тренду на основі нахилу лінійної регресії
    def calculate_trend(ema_series):
        if len(ema_series) >= trend_period:
            recent_ema = ema_series[-trend_period:]  # Останні значення EMA
            x = np.arange(trend_period)  # Відрізок часу (0,1,2,3...)
            slope, _, _, _, _ = linregress(x, recent_ema)  # Обчислення нахилу
            
            if slope > 0:
                return "uptrend"
            elif slope < 0:
                return "downtrend"
            else:
                return "sideways"
        return "not enough data"

    # Розрахунок двох EMA з різними періодами
    short_ema = talib.EMA(prices, timeperiod=short_period)
    long_ema = talib.EMA(prices, timeperiod=long_period)

    # Визначення напрямку на основі перехрестя EMA
    if short_ema[-2] < long_ema[-2] and short_ema[-1] > long_ema[-1]:
        direction = "LONG"  # Перехрестя вгору, сигнал на покупку
    elif short_ema[-2] > long_ema[-2] and short_ema[-1] < long_ema[-1]:
        direction = "SHORT"  # Перехрестя вниз, сигнал на продажу
    else:
        # Якщо перехрестя немає, визначаємо напрямок за допомогою нахилу EMA
        short_slope = calculate_trend(short_ema)
        long_slope = calculate_trend(long_ema)

        # Якщо обидва тренди висхідні або спадні
        if short_slope == "uptrend" and long_slope == "uptrend":
            direction = "LONG"
        elif short_slope == "downtrend" and long_slope == "downtrend":
            direction = "SHORT"
        else:
            # Якщо тренди змішані, визначаємо на основі нахилу більш важливого EMA
            if short_slope == "uptrend":
                direction = "LONG"
            else:
                direction = "SHORT"

    # Розрахунок тренду для короткої і довгої EMA
    short_trend = calculate_trend(short_ema)
    long_trend = calculate_trend(long_ema)

    # Визначення загального тренду на основі обох EMA
    if short_trend == "uptrend" and long_trend == "uptrend":
        overall_trend = "Сильний висхідний тренд 📈"
    elif short_trend == "downtrend" and long_trend == "downtrend":
        overall_trend = "Сильний спадний тренд 📉"
    elif short_trend == "uptrend" and long_trend == "sideways":
        overall_trend = "Слабкий висхідний тренд 📈"
    elif short_trend == "downtrend" and long_trend == "sideways":
        overall_trend = "Слабкий спадний тренд 📉"
    else:
        overall_trend = "Змішаний або боковий тренд ⚖️"

    return {
        "last_short_ema": short_ema[-1],  # Останнє значення короткої EMA
        "last_long_ema": long_ema[-1],    # Останнє значення довгої EMA
        "short_ema_series": short_ema,    # Уся серія короткої EMA
        "long_ema_series": long_ema,      # Уся серія довгої EMA
        "direction": direction,           # Напрямок (long, short)
        "short_trend": short_trend,       # Тренд короткої EMA
        "long_trend": long_trend,         # Тренд довгої EMA
        "overall_trend": overall_trend    # Загальний тренд на основі обох EMA
    }

# Розрахунок AO (Awesome Oscillator)
def calculate_ao(high_prices, low_prices, short_period=5, long_period=34):
    # Обчислення медіанної ціни (середнє значення між high та low)
    median_prices = (np.array(high_prices) + np.array(low_prices)) / 2

    # Розрахунок двох SMA на медіанних цінах
    short_sma = talib.SMA(median_prices, timeperiod=short_period)
    long_sma = talib.SMA(median_prices, timeperiod=long_period)
    
    # Обчислення Awesome Oscillator
    ao_series = short_sma - long_sma
    
    if ao_series.size == 0:
        return {"ao_series": ao_series, 
                "last_ao": 0, 
                "direction": "SHORT", 
                "trend": "downtrend"
                }

    # Визначення напрямку (long або short) на основі останнього значення AO
    direction = "LONG" if ao_series[-1] > 0 else "SHORT"
    
    # Визначення тренду: якщо останнє значення AO > 0, то це висхідний тренд
    trend = "uptrend" if direction == "LONG" else "downtrend"
    
    # Повертаємо результати
    return {
        "last_ao": ao_series[-1],  # Останнє значення AO
        "ao_series": ao_series,    # Уся серія AO
        "direction": direction,    # Напрямок (long або short)
        "trend": trend             # Тренд (висхідний або низхідний)
    }

# Розрахунок Supertrend
def calculate_supertrend(high, low, close, period=2, multiplier=2):
    high = np.array(high)
    low = np.array(low)
    close = np.array(close)

    atr = talib.ATR(high, low, close, timeperiod=period)  # ATR для визначення волатильності
    hl2 = (high + low) / 2  # Середнє значення (High + Low) / 2

    if atr.size == 0:
        return {
                "supertrend_series": [], 
                "last_supertrend": None,  
                "signals": [],               
                "direction": "SHORT" 
                }

    # Лінії Supertrend
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)

    supertrend = np.zeros(len(close))  # Массив для Supertrend
    signals = []  # Сигнали "buy" або "sell"

    for i in range(1, len(close)):
        if close[i] > supertrend[i - 1]:
            supertrend[i] = lower_band[i]  # Тренд зміщується вгору
            if len(signals) == 0 or signals[-1] != "buy":  # Тільки якщо попередній сигнал не був buy
                signals.append("buy")
        elif close[i] < supertrend[i - 1]:
            supertrend[i] = upper_band[i]  # Тренд зміщується вниз
            if len(signals) == 0 or signals[-1] != "sell":  # Тільки якщо попередній сигнал не був sell
                signals.append("sell")
        else:
            supertrend[i] = supertrend[i - 1]
            if len(signals) == 0 or signals[-1] != "hold":  # Тільки якщо попередній сигнал не був hold
                signals.append("hold")

    # Останній напрямок (long або short)
    direction = "LONG" if close[-1] > supertrend[-1] else "SHORT"

    return {
        "supertrend_series": supertrend,  # Уся серія Supertrend
        "last_supertrend": supertrend[-1],  # Останнє значення Supertrend
        "signals": signals,               # Сигнали (купити/продати/тримати)
        "direction": direction            # Напрямок (long або short)
    }

# Розрахунок ADX (Average Directional Index)
def calculate_adx(high, low, close, period=14):
    high = np.array(high, dtype=float)
    low = np.array(low, dtype=float)
    close = np.array(close, dtype=float)

    adx = talib.ADX(high, low, close, timeperiod=period)
    plus_di = talib.PLUS_DI(high, low, close, timeperiod=period)
    minus_di = talib.MINUS_DI(high, low, close, timeperiod=period)

    if adx.size == 0 and plus_di.size == 0 and minus_di.size == 0:
        return {
        "last_adx": 0,
        "adx_series": [],
        "trend_direction": 'weak_trend',
        "direction": 'SHORT'
    }

    trend_direction = "strong trend" if adx[-1] > 25 else "weak trend"
    direction = "LONG" if plus_di[-1] > minus_di[-1] else "SHORT"

    return {
        "last_adx": adx[-1],
        "adx_series": adx,
        "trend_direction": trend_direction,
        "direction": direction  # Додаємо напрямок тренду
    }

# Розрахунок Volume Profile (графік обсягу за ціною)
def calculate_volume_profile(prices, volumes, price_buckets=50):
    min_price = min(prices)
    max_price = max(prices)
    price_range = max_price - min_price
    price_interval = price_range / price_buckets

    volume_profile = np.zeros(price_buckets)

    for price, volume in zip(prices, volumes):
        bucket_idx = int((price - min_price) / price_interval)
        if 0 <= bucket_idx < price_buckets:
            volume_profile[bucket_idx] += volume

    max_volume_index = np.argmax(volume_profile)  # Знаходимо індекс з найбільшим об'ємом
    volume_price_level = min_price + (max_volume_index * price_interval)  # Відповідна ціна

    # Визначаємо напрямок
    if volume_price_level > (min_price + max_price) / 2:
        direction = "LONG"  # Найбільший об'єм вище середини діапазону цін
    else:
        direction = "SHORT"  # Найбільший об'єм нижче середини

    return {
        "volume_profile": volume_profile,
        "min_price": min_price,
        "max_price": max_price,
        "direction": direction  # Додаємо напрямок тренду
    }

# Розрахунок усіх індикаторів разом
def calculate_indicators(prices, high, low, close, volumes):
    # Розрахунок EMA
    ema = calculate_ema(prices)
    # Розрахунок Awesome Oscillator (AO)
    ao = calculate_ao(high, low)
    # Розрахунок Supertrend
    supertrend = calculate_supertrend(high, low, close, period=2, multiplier=2)
    # Розрахунок ADX
    adx = calculate_adx(high, low, close)
    # Розрахунок Volume Profile
    volume_profile = calculate_volume_profile(prices, volumes)
    
    return {
        "ema": ema,
        "ao": ao,
        "supertrend": supertrend,
        "adx": adx,
        "volume_profile": volume_profile
    }

#__________________________________________________________

# Функція для обчислення волатильності
def calculate_volatility(prices):
    return np.std(prices)  # Стандартне відхилення

# Порогові значення волатильності для торгівлі
LOW_VOLATILITY_THRESHOLD = 0.01  # Допустима волатильність
HIGH_VOLATILITY_THRESHOLD = 0.05  # Небезпечна волатильність

# Основна функція для порівняння валютних пар
def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours}г.{minutes}хв.{seconds}сек."

# Функція для отримання ID токена за його символом через CoinGecko API
def get_contract_address(symbol, exchange):
    """Отримує контрактну адресу токена для конкретної біржі через CoinGecko API."""
    time.sleep(5)
    
    try:
        # 1. Отримуємо список всіх токенів з CoinGecko
        url = "https://api.coingecko.com/api/v3/coins/list"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        tokens = response.json()

        # 2. Знаходимо правильний ID токена за його символом
        token_id = None
        for token in tokens:
            if token["symbol"].lower() == symbol.lower():
                token_id = token["id"]
                break

        if not token_id:
            print(f"⚠️ Не знайдено ID токена для {symbol} на {exchange}")
            return None

        # 3. Отримуємо деталі токена, включаючи контрактні адреси
        url = f"https://api.coingecko.com/api/v3/coins/{token_id}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # 4. Перевіряємо, чи є токен на цій біржі
        if "tickers" in data:
            for ticker in data["tickers"]:
                if ticker["market"]["identifier"] == exchange.lower():
                    # Якщо знаходимо біржу, повертаємо контрактну адресу
                    contract_address = data["platforms"]
                    print(f"✅ Знайдено контрактну адресу для {symbol} на {exchange}: {contract_address}")
                    return contract_address

        print(f"⚠️ Токен {symbol} не знайдено на біржі {exchange} за даними CoinGecko")
        return None

    except requests.exceptions.RequestException as e:
        print(f"❌ Помилка при отриманні даних з CoinGecko: {e}")
        return None

# Функція для отримання контрактної адреси за ID токена
def get_token_details(token_id):
    if not token_id:
        return None

    url = f"https://api.coingecko.com/api/v3/coins/{token_id}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Отримання списку контрактних адрес
        contract_data = data.get("platforms", {})
        if contract_data:
            return contract_data  # Повертаємо словник з контрактами на різних блокчейнах

    except requests.exceptions.RequestException as e:
        print(f"Помилка при отриманні контрактної адреси {token_id} з CoinGecko: {e}")

    return None

async def compare_currency_pairs(currency_pairs, exchanges):
    global last_sell_exchange, balance, signal_counts, profit_for_signals, profit_for_past_24h, \
           signal_counter, start_hour, profits_list_24_hours, start_minute, daily_income,        \
           buy_timestamp, sell_timestamp, used_exchanges

    best_pair_data = {"pair": None, "profit": 0}  #Структура для збереження найкращої пари
    
    best_pairs_of_the_best = [] # Список найкращих пар на час першого аналізу

    percent_profit_from_balance = (balance*0.25) / 100 # Мінімальна сума профіту
    
    #___Розрахунок мінімальної суми профіту в залежності від балансу___

    print('Аналізування цін валютних пар...')

    async def finding_best_pair(batch_size=500):

        start_time = time.time()  # Початковий час
        lock = asyncio.Lock()

        async def process_compared_pair(pair):
            global buy_timestamp
            try:
                # Визначення ціни серед перевірених бірж
                valid_prices, prices = await get_all_prices(pair)

                #Словники з цінами
                exchanges_buy_prices = {x:valid_prices[x][0] for x in valid_prices}
                #exchanges_sell_prices = {y:valid_prices[y][1] for y in valid_prices}

                if len(valid_prices) < 2:
                     return  # Пропускаємо пару, якщо ціни доступні менше ніж на двох біржах

                # Вибір біржі для купівлі
                buy_exchange = last_sell_exchange if last_sell_exchange else min(exchanges_buy_prices, key=valid_prices.get)
                buy_object = pairs.pairs_good_exchanges[pair][buy_exchange]

                # ___Знаходження базової валюти___
                base = pair.split("/")[0] 

                # Завантаження валют з кешем
                buy_currencies_key = f"{buy_object}_currencies"
                buy_currencies = await fetch_with_cache_exchanges(pairs.currencies_cache, buy_currencies_key, fetch_with_retries, buy_object.fetch_currencies)

                buy_networks = buy_currencies[base]["networks"]

                vp_copy = valid_prices.copy()
                vp_copy.pop(buy_exchange, None)

                # Пошук найбільш вигідної біржі для продажу
                sell_exchange = None
                joint_networks = []
                for exchange in list(vp_copy.keys()):
                    sell_object = pairs.pairs_good_exchanges[pair][exchange]

                    # Завантаження валют для продажу з кешем
                    sell_currencies_key = f"{sell_object}_currencies"
                    sell_currencies = await fetch_with_cache_exchanges(pairs.currencies_cache, sell_currencies_key, fetch_with_retries, sell_object.fetch_currencies)

                    sell_networks = sell_currencies[base]["networks"]

                    # Пошук спільних мереж
                    common_networks = [n for n in buy_networks if buy_networks[n]['withdraw'] and buy_networks[n]['fee'] and n in sell_networks and sell_networks[n]['deposit'] and sell_networks[n]['fee']]
                    if common_networks:
                        sell_exchange = exchange
                        joint_networks = common_networks
                        break

                if not sell_exchange:
                    return

                # Отримання часу купівлі (момент отримання ціни купівлі на біржі)
                buy_timestamp = int(time.time() * 1000)  # Поточний час у мілісекундах

                #___розрахунки комісій_______________________________________________________________________
                withdraw_fee = min([buy_networks[n]["fee"] for n in joint_networks])
                deposit_fee = min([sell_networks[n]["fee"] for n in joint_networks])
                #____________________________________________________________________________________________

                #Взяття з кешу ордербуків або їхній запит
                buy_order_book = await fetch_with_retries(buy_object.fetch_order_book, pair)
                sell_order_book = await fetch_with_retries(sell_object.fetch_order_book, pair)

                if not buy_order_book or not sell_order_book:
                    return

                # Розрахунок прибутку(профіт, середня ціна купівлі, середня ціна продажу)
                profit, buy_price, sell_price = await orders_transaction(buy_order_book, sell_order_book, balance, withdraw_fee, deposit_fee)

                if profit > balance * 0.1:
                    # Отримання контрактних адрес через CoinGecko API
                    buy_contracts = get_contract_address(base, buy_exchange)
                    sell_contracts = get_contract_address(base, sell_exchange)

                    if not buy_contracts or not sell_contracts:
                        print(f"⚠️ Не вдалося знайти контрактні адреси токена {base} на CoinGecko, пропускаємо пару")
                        return

                    # Беремо першу адресу з кожного словника
                    buy_address = next(iter(buy_contracts.values()))
                    sell_address = next(iter(sell_contracts.values()))

                    print(f"🔍 Перевіряємо {base}:")
                    print(f"   Контрактні адреси купівлі ({buy_exchange}): {buy_contracts}")
                    print(f"   Контрактні адреси продажу ({sell_exchange}): {sell_contracts}")

                    if buy_address != sell_address:
                        print(f"❌ Контрактні адреси НЕ співпадають для {pair} ({base}):")
                        return  # Пропускаємо пару
                    else:
                        print(f"✅ Контрактні адреси співпадають для {pair} ({base}), перевірку пройдено")

                # Оновлення найкращої пари
                async with lock:
                    if profit > best_pair_data["profit"] and profit > percent_profit_from_balance: 
                       
                        #___Додавання пари до списку з найкращими___
                        best_pairs_of_the_best.append(pair)

                        #___Обчислення індикаторів для найкращої пари на біржі продажу___
                        prices = await get_historical_prices(sell_object, pair)

                        if len(prices) > 50:
                            ohlcv = await fetch_with_retries(sell_object.fetch_ohlcv, pair, '1m', 60)

                            high_prices = [x[2] for x in ohlcv]  # Максимальні ціни
                            low_prices = [x[3] for x in ohlcv]   # Мінімальні ціни
                            close_prices = [x[4] for x in ohlcv] # Ціни закриття
                            volume = [x[5] for x in ohlcv] # Об'єм торгів

                            indicators = calculate_indicators(prices, high_prices, low_prices, close_prices, volume)
                        else:
                            return

                        # Логіка рекомендацій
                        recommendation = "продаж відразу" 
                        directions = [indicators['ema']['direction'], 
                                      indicators['ao']['direction'], 
                                      indicators['supertrend']['direction'], 
                                      indicators['adx']['direction'], 
                                      indicators['volume_profile']['direction']
                                      ]
                        longs = 0
                        for x in directions:
                            if x == 'LONG':
                                longs += 1
                        if longs >= 3:
                            recommendation = "продаж по сигналу"
                        #_________________________________________________________________

                        best_pair_data.update({
                            "pair": pair,
                            "profit": profit,

                            "buy_exchange": buy_exchange,
                            "sell_exchange": sell_exchange,

                            "buy_price": buy_price,
                            "sell_price": sell_price,

                            "joint_networks": joint_networks,
                            # ___Об'єкти найкращих бірж___
                            'buy_networks': buy_networks,
                            'sell_networks': sell_networks,
                            #___Індикатори___
                            'sell_ema_direction': indicators['ema']['direction'],
                            'sell_ao_direction': indicators['ao']['direction'],
                            'sell_supertrend_direction': indicators['supertrend']['direction'],
                            'sell_adx_direction': indicators['adx']['direction'],
                            'sell_volume_profile_direction': indicators['volume_profile']['direction'],
                            # Рекомендація
                            'recommendation': recommendation
                            })
                        print('')
                        print(f'Пара з найкращим прибутком на цей момент: {best_pair_data["pair"]}')
                        print(f'Прибуток: {best_pair_data["profit"]} \n')
                        print(f'спільні мережі: {best_pair_data["joint_networks"]}')
                        print('')
            except Exception as e:
                print(f"Помилка для пари {pair}: {e}")

        # Поділ валютних пар на батчі
        for i in range(0, len(currency_pairs), batch_size):
            current_time = time.time()  # Поточний час

            # Визначаємо час, що залишився для аналізу
            elapsed_time = current_time - start_time
            time_left = (len(currency_pairs) - (i + 1)) * (elapsed_time / (i + 1))  # Приблизний час до кінця

            # Форматування часу до завершення
            formatted_time_left = format_time(time_left)

            batch = currency_pairs[i:i + batch_size]

            # Створення завдань для батча
            tasks = [process_compared_pair(pair) for pair in batch]

            # Виконання завдань паралельно для поточного батча
            await asyncio.gather(*tasks)

            print(f"Проаналізовано ціни {i + len(batch)}/{len(currency_pairs)} пар: залишилось приблизно {formatted_time_left}")

        if best_pairs_of_the_best:

            best_pair_data.update({"pair": None, "profit": 0})

            #___Аналіз і вибір найкращої пари після першого аналізу___
            print('---> Ціни всіх валют проаналізовано, починається аналіз найкращих пар...')

            # Створення завдань для всіх пар
            tasks = [process_compared_pair(pair) for pair in best_pairs_of_the_best]

            # Виконання всіх завдань паралельно
            await asyncio.gather(*tasks)


        elapsed_time = time.time() - start_time
        print(f"Оновлення закінчено за {elapsed_time:.2f} секунд")
        return best_pair_data

    try:
        best_pair_data = await finding_best_pair()
    finally:
        # Закриття ресурсів бірж
        for exchange_name, exchange_object in exchanges.items():
            try:
                await exchange_object.close()
            except Exception as e:
                print(f"Помилка при закритті {exchange_name}: {e}")

    best_pair = best_pair_data['pair']

    if best_pair:

        #Надсилання першого повідомлення про пару
        message = f"Знайдено пару {best_pair_data["pair"]} на біржі купівлі {best_pair_data["buy_exchange"]}"
        await send_telegram_message(message)
        await asyncio.sleep(30)

        buy_ex = exchanges[best_pair_data['buy_exchange']]
        sell_ex = exchanges[best_pair_data['sell_exchange']] 

        buy_ticker_key = f"{best_pair_data['buy_exchange']}_{best_pair}_ticker"
        buy_ticker = await fetch_with_cache(pairs.ticker_cache, buy_ticker_key, fetch_with_retries, buy_ex.fetch_ticker, best_pair)

        sell_ticker_key = f"{best_pair_data['sell_exchange']}_{best_pair}_ticker"
        sell_ticker = await fetch_with_cache(pairs.ticker_cache, sell_ticker_key, fetch_with_retries, sell_ex.fetch_ticker, best_pair)

        # Отримання кількості валюти у першому ордері

        base, quote = best_pair.split('/') 
        
        #__Повідомлення про мережі______________________________________________

        withdraw_nets = []
        deposit_nets = []

        #___Заповнення списків для повідомлень___
        for x in best_pair_data['joint_networks']:
            fee = best_pair_data['buy_networks'][x]["fee"]
            withdraw_nets.append(f'{x} --- Комісія за вивід: {fee: 6f} {base} ({fee*best_pair_data["buy_price"]: 2f}$)')

            
        for y in best_pair_data['joint_networks']:
            fee = best_pair_data['sell_networks'][y]["fee"]
            deposit_nets.append(f'{y} --- Комісія за депозит: {fee: 6f} {base} ({fee*best_pair_data["sell_price"]: 2f}$)')
            #________________________________________________________________________
        
        # Використання функції для біржі купівлі та продажу
        buy_volatility, buy_volatility_message = await get_volatility_message(
        best_pair_data['buy_exchange'], buy_ex, best_pair, pairs.pairs_spread_volume_volatility, HIGH_VOLATILITY_THRESHOLD, LOW_VOLATILITY_THRESHOLD
        )

        sell_volatility, sell_volatility_message = await get_volatility_message(
        best_pair_data['sell_exchange'], sell_ex, best_pair, pairs.pairs_spread_volume_volatility, HIGH_VOLATILITY_THRESHOLD, LOW_VOLATILITY_THRESHOLD
        )
        #__________________________________________________

        #__Об'єми__
        #Об'єм БАЗОВОЇ валюти для біржі купівлі
        base_buy_volume = pairs.pairs_spread_volume_volatility[best_pair][best_pair_data['buy_exchange']]['base_volume']
        #Об'єм КВОТИ для біржі купівлі
        quote_buy_volume = pairs.pairs_spread_volume_volatility[best_pair][best_pair_data['buy_exchange']]['quote_volume']

        #Об'єм БАЗОВОЇ валюти для біржі продажу
        base_sell_volume = pairs.pairs_spread_volume_volatility[best_pair][best_pair_data['sell_exchange']]['base_volume']
        #Об'єм КВОТИ для біржі продажу
        quote_sell_volume = pairs.pairs_spread_volume_volatility[best_pair][best_pair_data['sell_exchange']]['quote_volume']

        #__Зміни за 24 години__
        buy_percentage = buy_ticker['percentage'] #Зміна за 24 години на біржі купівлі
        sell_percentage = sell_ticker['percentage'] #Зміна за 24 години на біржі продажу

        #__Спред__
        #Спреж на біржі купівлі
        spread_buy = pairs.pairs_spread_volume_volatility[best_pair][best_pair_data['buy_exchange']]['spread'] 
        #Спред на біржі продажу
        spread_sell = pairs.pairs_spread_volume_volatility[best_pair][best_pair_data['sell_exchange']]['spread'] 

        #___Повідомлення___

        #__Перехоплення якщо None в об'ємах і процентах__
        base_vol1 = f"Не знайдено даних за об'єм {base}" if base_buy_volume == None else f"об'єм {base} за 24 години: {base_buy_volume}"
        quote_vol1 = f"Не знайдено даних за об'єм {quote}" if quote_buy_volume == None else f"об'єм {quote} за 24 години: {quote_buy_volume}"

        base_vol2 = f"Не знайдено даних за об'єм {base}" if base_sell_volume == None else f"об'єм {base} за 24 години: {base_sell_volume}"
        quote_vol2 = f"Не знайдено даних за об'єм {quote}" if quote_sell_volume == None else f"об'єм {quote} за 24 години: {quote_sell_volume}"

        buy_perc = f"Не знайдено даних за зміну {base} протягом 24 годин" if buy_percentage == None else f"зміна {base} за 24 години:{buy_percentage: 2f}%"
        sell_perc = f"Не знайдено даних за зміну {base} протягом 24 годин" if sell_percentage == None else f"зміна {base} за 24 години:{sell_percentage: 2f}%"

        #__Перехоплення якщо None в ордербуках__
        buy_spread_message = f"Не знайдено даних за ордери на {best_pair}" if spread_buy == None else f"Спред для {best_pair}: {spread_buy: .10f}"
        sell_spread_message = f"Не знайдено даних за ордери на {best_pair}" if spread_sell == None else f"Спред для {best_pair}: {spread_sell: .10f}"

        #_____________________________________________________________________________________________

        kiev_time = datetime.now(kiev_timezone)
        formatted_time = kiev_time.strftime('%Y-%m-%d %H:%M:%S')

        profit_percentage = (best_pair_data['profit'] / balance) * 100
        # Змінні для відслідковування останніх повідомлень для кожного сигналу
        last_signal_message_id = None
        #__Формуємо повідомлення для Telegram__
        message1 = (f"🪙 Монета:🪙 \n"
                f"\n"
                f"💎#{best_pair}💎\n"
                f"{'━' * 24}\n"        
                f"📈 Купити на [{best_pair_data['buy_exchange']}]({generate_exchange_url(best_pair_data['buy_exchange'], best_pair)}) за {best_pair_data['buy_price']: .4f} USDT\n"
                f"{'━' * 24}\n"  
                f"   Доступні мережі для виводу \n"
                f"{withdraw_nets}\n"
                f"{'━' * 24}\n"
                f"📉 Продати на [{best_pair_data['sell_exchange']}]({generate_exchange_url(best_pair_data['sell_exchange'], best_pair)}) за {best_pair_data['sell_price']: .4f} USDT\n"
                f"{'━' * 24}\n"
                f"   Доступні мережі для депозиту \n"
                f"{deposit_nets}\n"
                f"{'━' * 24}\n"
                f"📊 Волатильність на {best_pair_data['buy_exchange']}: {buy_volatility: .5f}\n"
                f"📊 {buy_volatility_message}\n"
                f"📊 Волатильність на {best_pair_data['sell_exchange']}: {sell_volatility: .5f}\n"
                f"📊 {sell_volatility_message}\n"
                f"{'━' * 24}\n"        
                f"📊 Спред на {best_pair_data['buy_exchange']}: {buy_spread_message}%\n"
                f"{'━' * 24}\n"        
                f"📊 Спред на  {best_pair_data['sell_exchange']}: {sell_spread_message}%\n"
                f"{'━' * 24}\n"        
                f"📊 Обсяг {best_pair_data['buy_exchange']}: {base_vol1},\n"
                f"{buy_perc}\n"
                f"{quote_vol1}\n"
                f"{'━' * 24}\n"        
                f"📊 Обсяг  {best_pair_data['sell_exchange']}: {base_vol2},\n"
                f"{sell_perc}\n"
                f"{quote_vol2}\n"
                f"{'━' * 24}\n"        
                f"💰 Розрахунковий баланс: {(balance+best_pair_data['profit']):.2f} USDT\n"
                f"{'━' * 24}\n"   
                f"💵 Розрахунковий прибуток на: {best_pair_data['profit']:.2f} USDT\n"
                f"{profit_percentage:.2f}%\n"
                f"{'━' * 24}\n"        
                f"📌 Оновлено: {formatted_time}\n")
        # Відправляємо повідомлення в Telegram
        #print(message1)
        sent_message1 = await send_telegram_message(message1)
        last_signal_message_id = sent_message1  # Зберігаємо message_id для цього сигналу

        message2 = (f"📌Рекомендація для пари {best_pair}: {best_pair_data['recommendation']} 📌\n"
                    f"━━━> Напрямок  EMA: {best_pair_data['sell_ema_direction']}\n"
                    f"━━━> Напрямок AO: {best_pair_data['sell_ao_direction']}\n"
                    f"━━━> Напрямок  Supertrend: {best_pair_data['sell_supertrend_direction']}\n"
                    f"━━━> Напрямок  ADX: {best_pair_data['sell_adx_direction']}\n"
                    f"━━━> Напрямок  Volume Profile: {best_pair_data['sell_volume_profile_direction']}\n"
                )
        # Відправляємо повідомлення в Telegram
        #print(message2)
        # Відправляємо друге повідомлення, відповідаючи на перше
        await send_telegram_message(message2, reply_to_message_id=last_signal_message_id)

        #Додавання бірж до списку використаних бірж
        used_exchanges.add(best_pair_data['buy_exchange'])
        used_exchanges.add(best_pair_data['sell_exchange'])

        #______Генерація випадкової затримки від 6 до 9 хвилин______
        random_delay = random.randint(360,540)
        print(f"Час до наступного оновлення: {random_delay} секунд")
        await asyncio.sleep(random_delay)

        kiev_time = datetime.now(kiev_timezone)
        formatted_time = kiev_time.strftime('%Y-%m-%d %H:%M:%S')

        withdraw_fee = min([best_pair_data['buy_networks'][n]["fee"] for n in best_pair_data['joint_networks'] if best_pair_data['buy_networks'][n]["fee"]])
        deposit_fee = min([best_pair_data['sell_networks'][n]["fee"] for n in best_pair_data['joint_networks'] if best_pair_data['sell_networks'][n]["fee"]])

        #___Розрахунок метрик на основі нової ціни після очікування___
        #Взяття з кешу ордербуків або їхній запит
        buy_order_book = await fetch_with_retries(buy_ex.fetch_order_book, best_pair)
        sell_order_book = await fetch_with_retries(sell_ex.fetch_order_book, best_pair)

        # Розрахунок прибутку
        profit, buy_price, sell_price = await orders_transaction(buy_order_book, sell_order_book, balance, withdraw_fee, deposit_fee)

        # Отримання часу продажу (момент другого отримання ціни продажу)
        sell_timestamp = int(time.time() * 1000)  # Поточний час у мілісекундах

        #Оновлення балансу після операції
        balance += profit
        profit_percentage = (profit / balance) * 100

        #Додавання балансу до обороту
        daily_income += balance 
        
        trading_image_path = r"C:\programmes\other\Arbitrage\Arbitrage\Long.jpg" if profit_percentage > 0 else r"C:\programmes\other\Arbitrage\Arbitrage\SHORT01.jpg" #шлях до картинки фону

        # Генерація та надсилання зображення, відповідаючи на перше повідомлення
        generate_trading_image(trading_image_path, balance, profit, profit_percentage, best_pair_data['buy_price'], sell_price, best_pair_data['buy_exchange'], best_pair_data['sell_exchange'], best_pair)
        await send_telegram_image("trading_result.png", reply_to_message_id=last_signal_message_id)

        #Створення графіку цін для пари
        await generate_price_chart(buy_ex, sell_ex, best_pair_data['sell_exchange'], best_pair_data['buy_price'], sell_price, best_pair, buy_timestamp, sell_timestamp)
        await send_telegram_image("price_chart.png", reply_to_message_id=last_signal_message_id)

        signal_counter += 1
        # Виводимо номер сигналу в консоль
        print(f"Сигнал №{signal_counter}: Продано {best_pair} — Прибуток: {profit:.2f} USDT")
        
        #___Повідомлення за останні 10 сигналів___

        profit_for_signals += profit
        profit_for_past_24h += profit

        signal_counts += 1
        profits_list_10_signals.append(profit)  # Додаємо прибуток до списку за 10 сигналів
        profits_list_24_hours.append(profit) # Додаємо прибуток до списку за 24 години

        if signal_counts == 10:

            profit_history = [0]  # Початковий прибуток (0 на початку)

            # Обчислюємо прибуток за кожен сигнал
            for profit in profits_list_10_signals:
                new_profit = profit_history[-1] + profit  # Додаємо прибуток поточного сигналу
                profit_history.append(new_profit)  # Додаємо новий прибуток в історію

            # Формуємо дані для японських свічок
            open_prices = [profit_history[i] for i in range(len(profit_history)-1)]  # Початковий прибуток
            close_prices = [profit_history[i+1] for i in range(len(profit_history)-1)]  # Прибуток після поточного сигналу
            high_prices = [max(profit_history[i], profit_history[i+1]) for i in range(len(profit_history)-1)]  # Максимальний прибуток
            low_prices = [min(profit_history[i], profit_history[i+1]) for i in range(len(profit_history)-1)]  # Мінімальний прибуток
 
            # Створюємо DataFrame для свічок
            df = pd.DataFrame({
                              "Open": open_prices,
                              "High": high_prices,
                              "Low": low_prices,
                              "Close": close_prices
            })

            # Використовуємо фіктивні дати (вони будуть приховані)
            df.index = pd.date_range(start="2024-01-01", periods=len(profits_list_10_signals), freq="D")

            # Налаштування стилю
            mc = mpf.make_marketcolors(up="lime", down="red", edge="black", wick="black", volume="black")
            s = mpf.make_mpf_style(base_mpl_style="dark_background", marketcolors=mc)

            # Побудова свічкового графіка з темним фоном
            fig, ax = plt.subplots(figsize=(10, 6))
            mpf.plot(df, type="candle", style=s, ax=ax, ylabel="Прибуток (USDT)", xlabel="Номер сигналу")

            # Замінюємо підписи осі X (дати) на номери сигналів (1-10)
            ax.set_xticks(range(len(profits_list_10_signals)))
            ax.set_xticklabels(range(1, len(profits_list_10_signals) + 1))

            #Гор. лінія на останньому значенні профіту
            ax.axhline(y=profit_for_signals, color="yellow", linestyle="--", linewidth=1.5, alpha=0.7, label="Профіт")

            # Додаємо горизонтальну лінію на рівні 0
            ax.axhline(y=0, color="white", linestyle="-", linewidth=1.5, alpha=0.7, label="Нульовий рівень")

            # Додаємо легенду
            ax.legend()

            # Збереження графіка
            plt.savefig("candlestick_chart.png", dpi=300, bbox_inches="tight")
            plt.close()

            # Надсилаємо графік у Telegram
            await send_telegram_image("candlestick_chart.png")

            message = (f"💰 Профіт за 10 сигналів: {profit_for_signals:.2f} USDT\n"
                       f"{'━' * 24}\n"
                       f"📈 Прибуток у відсотках: {((profit_for_signals / (balance - profit_for_signals)) * 100):.5f}%\n"
                       f"{'━' * 24}\n")
            print(message)
            await send_telegram_message(message)

            # Очищення змінних
            signal_counts = 0
            profit_for_signals = 0
            profits_list_10_signals.clear()
        #__________________________________________

        last_sell_exchange = best_pair_data['sell_exchange']  # Оновлюємо останню біржу для продажу
    else:
        if last_sell_exchange:
            #___надсилання повідомлення в телеграм___
            message = ('Не знайдено вигідних пар для останньої біржі продажу. Пошук по всіх біржах...')
            await send_telegram_message(message)
            #________________________________________
            print(message)
            last_sell_exchange = None
            await compare_currency_pairs(pairs.currency_pairs, exchanges)
        else:
            #___надсилання повідомлення в телеграм___
            message = ("Повторний аналіз розпочато")
            #await send_telegram_message(message)
            #________________________________________
            print(message)

# Викликаємо основну функцію
async def main():
    global start_hour, start_minute, profits_list_24_hours, profit_for_past_24h, daily_income, balance, used_exchanges
    try:
        # Ініціалізація бірж
        exchanges = await pairs.init_exchanges()
        await loading_markets(exchanges)

        print('Знаходження валідних пар для торгівлі...')
        all_pairs = await getting_pairs.load_all_currency_pairs()
        pairs.currency_pairs = await getting_pairs.find_currency_pairs(all_pairs)

        while True:
            start_time = time.time()  # Заміри часу початку
            if not pairs.pairs_good_exchanges:
                await exchanges_for_pairs(exchanges)
            if not pairs.pairs_spread_volume_volatility:
                await spread_volume_volatility()

            # Перевірка часу, щоб розпізнати неробочий час
            kiev_time = datetime.now(kiev_timezone)
            # Перевірка, чи зараз неробочий час
            if kiev_time.hour >= 22 or (kiev_time.hour < 6 and kiev_time.minute in [n for n in range(0,31)]):
                #Віднімання від обороту останнього значення 
                daily_income -= balance

                #___Надсилання графіку за останні 24 години___
                if start_hour < 22:
                    statistic_hour = 22 - start_hour
                else:
                    statistic_hour = 24 - (start_hour - 22)
                statistic_minute = 60 - start_minute

                # Генеруємо графік прибутку за всі сигнали
                if profits_list_24_hours:
                    # Формуємо список накопиченого прибутку
                    profit_history = [0]  # Початковий прибуток
                    for profit in profits_list_24_hours:
                        profit_history.append(profit_history[-1] + profit)

                    # Визначаємо Open, High, Low, Close для кожного сигналу
                    open_prices = profit_history[:-1]
                    close_prices = profit_history[1:]
                    high_prices = [max(o, c) for o, c in zip(open_prices, close_prices)]
                    low_prices = [min(o, c) for o, c in zip(open_prices, close_prices)]

                    # Створюємо DataFrame
                    df = pd.DataFrame({
                        "Open": open_prices,
                        "High": high_prices,
                        "Low": low_prices,
                        "Close": close_prices
                    })

                    # Використовуємо номер сигналу як індекс
                    df.index = pd.date_range(start="2024-01-01", periods=len(df), freq="s")

                    # Стиль графіка
                    mc = mpf.make_marketcolors(up="lime", down="red", edge="black", wick="black", volume="black")
                    s = mpf.make_mpf_style(base_mpl_style="dark_background", marketcolors=mc)

                    # Побудова графіка
                    fig, ax = plt.subplots(figsize=(12, 6))
                    mpf.plot(df, type="candle", style=s, ax=ax, ylabel="Прибуток (USDT)", xlabel="Номер сигналу")

                    # Налаштовуємо осі
                    ax.set_xticks(range(0, len(df), max(1, len(df) // 10)))  # Робимо підписи рідшими
                    ax.set_xticklabels(range(1, len(df) + 1, max(1, len(df) // 10)))

                    # Горизонтальні лінії
                    ax.axhline(y=0, color="white", linestyle="-", linewidth=1.5, alpha=0.7, label="Нульовий рівень")
                    ax.axhline(y=profit_for_past_24h, color="yellow", linestyle="--", linewidth=1.5, alpha=0.7, label="Профіт")

                    # Додаємо легенду
                    ax.legend()

                    # Збереження графіка у файл
                    plt.savefig("profits_24h_candlestick.png", dpi=300, bbox_inches="tight")
                    plt.close()

                    # Відправка графіка у Telegram
                    await send_telegram_image("profits_24h_candlestick.png")

                    # Формування повідомлення про статистику
                    message = (f"━━> Статистика за {statistic_hour} годин і {statistic_minute} хвилин <━━\n"
                               f"💰 Прибуток за торгову сесію: {profit_for_past_24h:.2f} USDT\n"
                               f"{'━' * 24}\n"
                               f"📈 Прибуток у відсотках: {((profit_for_past_24h / (balance - profit_for_past_24h)) * 100):.5f}%\n"
                               f"{'━' * 24}\n"
                               f"Оборот за день: {daily_income} USDT")
                    print(message)
                    await send_telegram_message(message)

                    # Скидання змінних
                    profit_for_past_24h = 0
                    start_hour = 22
                    start_minute = 60
                    profits_list_24_hours.clear()
                    daily_income = balance
                if used_exchanges:
                    message = (f'Біржі, які використовувались за сесію:\n'
                               f'{used_exchanges}')

                print('Зараз неробочий час, чекаю до 6:30...')
                while True:
                    await asyncio.sleep(300)  # Чекаємо 5 хвилин
                    kiev_time = datetime.now(kiev_timezone)

                    # Якщо зараз час між 02:00-02:10, виконуємо оновлення
                    if kiev_time.hour == 2 and kiev_time.minute in [n for n in range(0, 11)]:
                        await update_exchange_cache()
                        await update_spread_cache()

                    if kiev_time.hour >= 6 and kiev_time.hour < 22:
                        print('Вже 6 година ранку, починаю роботу')
                        break
                continue
            else:
                print('Зараз робочий час')
            #________________________________________________________
            await compare_currency_pairs(pairs.currency_pairs, exchanges)
            end_time = time.time()  # Заміри часу закінчення
            elapsed_time = end_time - start_time  # Час, що пройшов
    
            print(f"Поточний баланс: {balance:.2f} USDT\n")
            print(f"Оновлення тривало {elapsed_time:.2f} секунд.\n")
            #print("Оновлення завершено. Чекаємо 5 хвилин перед наступним оновленням...\n")

            #random_delay_end = random.randint(60,240)
            #await asyncio.sleep(random_delay_end)        
            # Поточний час у Київському часовому поясі
            kiev_time = datetime.now(kiev_timezone)

            # Форматування часу у вигляді рядка
            formatted_time = kiev_time.strftime('%Y-%m-%d %H:%M:%S')
            print("Поточний час у Київському часі:", formatted_time)

            # Якщо зараз парна година, виконуємо оновлення даних про валюти на біржах
            if kiev_time.hour != 22 and kiev_time.hour % 2 == 0 and kiev_time.minute in [n for n in range(0, 11)]:
                await update_spread_cache()
            #Очищення кешу запитів до бірж кожну годину
            if kiev_time.minute in [n for n in range(0, 11)]:
                pairs.markets_cache.clear()
                pairs.currencies_cache.clear()
    finally:
        await close_exchanges(exchanges)

# Запуск основної асинхронної програми
if __name__ == "__main__":
    asyncio.run(main())
