#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
USD/JPY Micro Futures NFP Hedging Strategy with Trading Economics API Integration

This strategy uses Interactive Brokers API to trade USD/JPY Micro Futures (M6J) on the CME.
It now integrates the Trading Economics API to fetch the latest Non-Farm Payroll (NFP) data
immediately after release. The strategy then decides whether to go long or short based on
whether the actual NFP figure is greater or less than the forecast.
    
- If Actual > Forecast: Go LONG (BUY)
- If Actual < Forecast: Go SHORT (SELL)
- If Actual == Forecast: No trade is executed

Make sure to set your Trading Economics API key in your environment variables
(e.g., TRADING_ECONOMICS_API_KEY).
    
Author: brkfx team
License: MIT
"""

import os
import sys
import time
import datetime
import json
import logging
import threading
import traceback
from enum import Enum
import pytz
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import requests
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import BarData, TickerId, TickAttrib
from ibapi.ticktype import TickTypeEnum
from threading import Thread, Lock, Event

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("usd-micro.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('usd-micro')

# ----------------- IB API Classes -----------------

class OrderStatus(Enum):
    """Enumeration for order statuses"""
    PENDING = 0
    SUBMITTED = 1
    FILLED = 2
    CANCELLED = 3
    ERROR = 4

class IBApi(EWrapper, EClient):
    """Interactive Brokers API wrapper and client class"""
    
    def __init__(self):
        EClient.__init__(self, self)
        self.next_order_id = None
        self.next_req_id = 1
        
        # Data storage
        self.positions = {}
        self.orders = {}
        self.market_data = {}
        self.account_summary = {}
        self.order_status_details = {}
        self.contract_details = {}
        self.historical_data = {}
        self.pnl = {}
        
        # Synchronization
        self.order_event = Event()
        self.position_event = Event()
        self.account_event = Event()
        self.contract_event = Event()
        self.market_data_event = Event()
        
        # Thread safety
        self.lock = Lock()
        self.req_id_lock = Lock()
        
    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.next_order_id = orderId
        logger.info(f"Next valid order ID: {orderId}")
        self.order_event.set()
        
    def getNextOrderId(self):
        with self.lock:
            current_id = self.next_order_id
            self.next_order_id += 1
            return current_id
            
    def getNextReqId(self):
        with self.req_id_lock:
            current_id = self.next_req_id
            self.next_req_id += 1
            return current_id
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
        if errorCode >= 2000:
            logger.error(f"Error {errorCode}: {errorString}")
            if advancedOrderRejectJson:
                logger.error(f"Advanced rejection: {advancedOrderRejectJson}")
        else:
            logger.warning(f"Warning {errorCode}: {errorString}")
        if errorCode == 202:  # Order cancelled
            if reqId in self.orders:
                self.orders[reqId]['status'] = OrderStatus.CANCELLED
        elif errorCode == 201:  # Order rejected
            if reqId in self.orders:
                self.orders[reqId]['status'] = OrderStatus.ERROR
                self.orders[reqId]['error_message'] = errorString
    
    def position(self, account, contract, position, avgCost):
        super().position(account, contract, position, avgCost)
        symbol = contract.symbol
        with self.lock:
            self.positions[symbol] = {
                'account': account,
                'symbol': symbol,
                'position': position,
                'avgCost': avgCost,
                'contract': contract
            }
    
    def positionEnd(self):
        super().positionEnd()
        logger.info("Positions download completed")
        self.position_event.set()
    
    def accountSummary(self, reqId, account, tag, value, currency):
        super().accountSummary(reqId, account, tag, value, currency)
        with self.lock:
            if account not in self.account_summary:
                self.account_summary[account] = {}
            self.account_summary[account][tag] = {
                'value': value,
                'currency': currency
            }
    
    def accountSummaryEnd(self, reqId):
        super().accountSummaryEnd(reqId)
        logger.info("Account summary download completed")
        self.account_event.set()
    
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, 
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        super().orderStatus(orderId, status, filled, remaining, avgFillPrice, 
                            permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)
        logger.info(f"Order {orderId} status: {status}, Filled: {filled}/{filled+remaining}, "
                    f"Average Fill Price: {avgFillPrice}")
        with self.lock:
            self.order_status_details[orderId] = {
                'status': status,
                'filled': filled,
                'remaining': remaining,
                'avgFillPrice': avgFillPrice,
                'permId': permId,
                'parentId': parentId,
                'lastFillPrice': lastFillPrice,
                'clientId': clientId,
                'whyHeld': whyHeld,
                'mktCapPrice': mktCapPrice
            }
            if orderId in self.orders:
                if status == "Filled":
                    self.orders[orderId]['status'] = OrderStatus.FILLED
                    self.orders[orderId]['filled'] = filled
                    self.orders[orderId]['avgFillPrice'] = avgFillPrice
                elif status == "Submitted" or status == "PreSubmitted":
                    self.orders[orderId]['status'] = OrderStatus.SUBMITTED
                    self.orders[orderId]['filled'] = filled
                elif status == "Cancelled":
                    self.orders[orderId]['status'] = OrderStatus.CANCELLED
    
    def contractDetails(self, reqId, contractDetails):
        super().contractDetails(reqId, contractDetails)
        with self.lock:
            if reqId not in self.contract_details:
                self.contract_details[reqId] = []
            self.contract_details[reqId].append(contractDetails)
    
    def contractDetailsEnd(self, reqId):
        super().contractDetailsEnd(reqId)
        logger.info(f"Contract details request {reqId} completed")
        self.contract_event.set()
    
    def tickPrice(self, reqId: TickerId, tickType: TickTypeEnum, price: float, attrib: TickAttrib):
        super().tickPrice(reqId, tickType, price, attrib)
        with self.lock:
            if reqId not in self.market_data:
                self.market_data[reqId] = {}
            self.market_data[reqId][tickType] = price
            if tickType == 1:  # Bid
                self.market_data[reqId]['bid'] = price
            elif tickType == 2:  # Ask
                self.market_data[reqId]['ask'] = price
            elif tickType == 4:  # Last
                self.market_data[reqId]['last'] = price
            elif tickType == 9:  # Close
                self.market_data[reqId]['close'] = price
        self.market_data_event.set()
            
    def historicalData(self, reqId, bar):
        super().historicalData(reqId, bar)
        with self.lock:
            if reqId not in self.historical_data:
                self.historical_data[reqId] = []
            bar_dict = {
                'date': bar.date,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
                'wap': bar.wap,
                'count': bar.barCount
            }
            self.historical_data[reqId].append(bar_dict)
    
    def historicalDataEnd(self, reqId, start, end):
        super().historicalDataEnd(reqId, start, end)
        logger.info(f"Historical data request {reqId} completed")
        with self.lock:
            if reqId in self.historical_data and self.historical_data[reqId]:
                self.historical_data[reqId] = pd.DataFrame(self.historical_data[reqId])
                if 'date' in self.historical_data[reqId].columns:
                    self.historical_data[reqId].set_index('date', inplace=True)
    
    def pnlSingle(self, reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value):
        super().pnlSingle(reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value)
        with self.lock:
            self.pnl[reqId] = {
                'position': pos,
                'dailyPnL': dailyPnL,
                'unrealizedPnL': unrealizedPnL, 
                'realizedPnL': realizedPnL,
                'value': value
            }

# ----------------- Strategy Class -----------------

class USDMicroFuturesStrategy:
    """Main strategy class for USD/JPY Micro Futures trading using NFP data"""
    
    def __init__(self, config_path='config.json'):
        self.config = self._load_config(config_path)
        self.api = IBApi()
        self.connected = False
        self.api_thread = None
        self.account = self.config["account"]["ib_account"]
        self.active_orders = {}
        self.active_brackets = {}
        self.active_symbols = []
        
        # USD/JPY Micro futures specifics
        self.tick_values = {'M6J': 0.000001}  # Tick value for M6J
        self.contract_multipliers = {'M6J': 1250000}  # Contract value in JPY
        self.pip_to_ticks = {'M6J': 10000}  # 1 pip = 10000 ticks for USD/JPY
        
        self.market_data_subscriptions = {}
        self.stop_event = Event()
        self.monitor_thread = None
    
    def _load_config(self, config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            # Provide defaults if config not found
            return {
                "account": {
                    "ib_account": os.getenv("IB_ACCOUNT", ""),
                    "port": int(os.getenv("IB_PORT", "4001")),
                    "host": os.getenv("IB_HOST", "127.0.0.1"),
                    "client_id": int(os.getenv("IB_CLIENT_ID", "1"))
                },
                "strategy": {
                    "symbols": ["M6J"],
                    "quantity": {"M6J": 2},
                    "sl_pips": 17,
                    "tp_pips": 76,
                    "minutes_before_nfp": 2,
                    "auto_reconnect": True,
                    "max_retry_attempts": 3,
                    "reconnect_delay": 60
                },
                "logging": {
                    "level": "INFO",
                    "file_path": "usd-micro.log"
                }
            }
    
    def connect(self):
        if self.connected:
            logger.info("Already connected to IB API")
            return True
        try:
            host = self.config["account"]["host"]
            port = self.config["account"]["port"]
            client_id = self.config["account"]["client_id"]
            max_attempts = self.config["strategy"].get("max_retry_attempts", 3)
            reconnect_delay = self.config["strategy"].get("reconnect_delay", 60)
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Connecting to IB API at {host}:{port} (Attempt {attempt}/{max_attempts})")
                    self.api.order_event.clear()
                    self.api.connect(host, port, client_id)
                    self.api_thread = Thread(target=self._run_client, daemon=True)
                    self.api_thread.start()
                    if not self.api.order_event.wait(30):
                        raise TimeoutError("Timeout waiting for connection")
                    self.connected = True
                    logger.info(f"Connected to IB API: {host}:{port}")
                    self.stop_event.clear()
                    self.monitor_thread = Thread(target=self._monitor_connection, daemon=True)
                    self.monitor_thread.start()
                    self._request_account_updates()
                    return True
                except Exception as e:
                    logger.error(f"Connection attempt {attempt} failed: {e}")
                    if attempt < max_attempts:
                        logger.info(f"Retrying in {reconnect_delay} seconds...")
                        time.sleep(reconnect_delay)
                    else:
                        logger.error("Maximum connection attempts reached")
                        raise
        except Exception as e:
            logger.error(f"Failed to connect to IB API: {e}")
            logger.debug(traceback.format_exc())
            return False
    
    def disconnect(self):
        if not self.connected:
            return True
        try:
            self.stop_event.set()
            for req_id in self.market_data_subscriptions:
                self.api.cancelMktData(req_id)
            self.api.disconnect()
            self.connected = False
            if self.api_thread and self.api_thread.is_alive():
                self.api_thread.join(timeout=5)
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=5)
            logger.info("Disconnected from IB API")
            return True
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")
            return False
    
    def _run_client(self):
        try:
            self.api.run()
        except Exception as e:
            logger.error(f"Error in API client thread: {e}")
            logger.debug(traceback.format_exc())
            self.connected = False
    
    def _monitor_connection(self):
        while not self.stop_event.is_set():
            try:
                if not self.connected and self.config["strategy"].get("auto_reconnect", True):
                    logger.warning("Connection lost. Attempting to reconnect...")
                    self.connect()
                self._check_active_orders()
                time.sleep(15)
            except Exception as e:
                logger.error(f"Error in monitor thread: {e}")
                logger.debug(traceback.format_exc())
                time.sleep(30)
    
    def _check_active_orders(self):
        if not self.connected:
            return
        with self.api.lock:
            for order_id, order_info in list(self.active_orders.items()):
                if order_id in self.api.order_status_details:
                    status = self.api.order_status_details[order_id]["status"]
                    order_info["current_status"] = status
                    order_info["last_update"] = datetime.datetime.now()
                    if status == "Filled" and not order_info.get("fill_logged", False):
                        filled = self.api.order_status_details[order_id]["filled"]
                        avg_price = self.api.order_status_details[order_id]["avgFillPrice"]
                        logger.info(f"Order {order_id} filled: {filled} @ {avg_price}")
                        order_info["fill_logged"] = True
    
    def _request_account_updates(self):
        if not self.connected:
            return
        try:
            self.api.position_event.clear()
            self.api.account_event.clear()
            self.api.reqPositions()
            req_id = self.api.getNextReqId()
            self.api.reqAccountSummary(req_id, "All", "$LEDGER")
            logger.info("Waiting for account data...")
            self.api.position_event.wait(timeout=10)
            self.api.account_event.wait(timeout=10)
            with self.api.lock:
                avail_funds = self.api.account_summary.get(self.account, {}).get("AvailableFunds", {}).get("value", "Unknown")
                net_liq = self.api.account_summary.get(self.account, {}).get("NetLiquidation", {}).get("value", "Unknown")
                logger.info(f"Account {self.account} - Available Funds: {avail_funds}, Net Liquidation: {net_liq}")
                if self.api.positions:
                    logger.info("Current positions:")
                    for symbol, pos_info in self.api.positions.items():
                        logger.info(f"  {symbol}: {pos_info['position']} @ {pos_info['avgCost']}")
                else:
                    logger.info("No positions found")
            return True
        except Exception as e:
            logger.error(f"Error requesting account updates: {e}")
            return False
    
    def create_usd_jpy_micro_futures_contract(self):
        contract = Contract()
        contract.symbol = "M6J"
        contract.secType = "FUT"
        contract.exchange = "CME"
        contract.currency = "USD"
        today = datetime.datetime.now()
        month = today.month
        year = today.year
        if month < 3:
            contract_month = 3
        elif month < 6:
            contract_month = 6
        elif month < 9:
            contract_month = 9
        elif month < 12:
            contract_month = 12
        else:
            contract_month = 3
            year += 1
        contract.lastTradeDateOrContractMonth = f"{year}{contract_month:02d}"
        logger.info(f"Created USD/JPY micro futures contract expiring {year}{contract_month:02d}")
        return contract
    
    def get_market_price(self, contract, timeout=10):
        # (Implementation omitted for brevity; same as original)
        pass
    
    def get_next_nfp_release(self):
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        first_day = datetime.datetime(now.year, now.month, 1, tzinfo=pytz.timezone('US/Eastern'))
        days_until_friday = (4 - first_day.weekday()) % 7
        first_friday = first_day + datetime.timedelta(days=days_until_friday)
        nfp_release = first_friday.replace(hour=8, minute=30, second=0, microsecond=0)
        if now > nfp_release:
            if now.month == 12:
                first_day = datetime.datetime(now.year + 1, 1, 1, tzinfo=pytz.timezone('US/Eastern'))
            else:
                first_day = datetime.datetime(now.year, now.month + 1, 1, tzinfo=pytz.timezone('US/Eastern'))
            days_until_friday = (4 - first_day.weekday()) % 7
            first_friday = first_day + datetime.timedelta(days=days_until_friday)
            nfp_release = first_friday.replace(hour=8, minute=30, second=0, microsecond=0)
        return nfp_release

    def get_nfp_report(self):
        """
        Fetch the latest NFP report from Trading Economics API.
        Expected JSON format (example):
        {
            "Event": "Non-Farm Payrolls",
            "Actual": "325000",
            "Forecast": "210000",
            "Previous": "210000",
            "Date": "2025-04-04",
            "Impact": "high"
        }
        """
        api_key = os.getenv("TRADING_ECONOMICS_API_KEY")
        if not api_key:
            logger.error("Trading Economics API key not set in environment variables.")
            return None
        url = f"https://api.tradingeconomics.com/calendar?category=employment&c={api_key}&f=json"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for event in data:
                    if "Non-Farm Payrolls" in event.get("Event", ""):
                        return event
                logger.error("Non-Farm Payrolls event not found in Trading Economics data.")
                return None
            else:
                logger.error(f"Trading Economics API returned status code {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching NFP data: {e}")
            return None

    def place_nfp_hedge_orders(self, quantity=None, sl_pips=None, tp_pips=None, minutes_before=None):
        """
        At a scheduled time before NFP release, wait until the data is updated,
        fetch the NFP report via Trading Economics API, and then decide whether to
        go long (BUY) or short (SELL) based on whether the actual NFP figure is greater than
        or less than the forecast.
        """
        if not self.connected:
            logger.error("Not connected to IB API")
            return None
        try:
            if quantity is None:
                quantity = self.config["strategy"]["quantity"].get("M6J", 2)
            if sl_pips is None:
                sl_pips = self.config["strategy"]["sl_pips"]
            if tp_pips is None:
                tp_pips = self.config["strategy"]["tp_pips"]
            if minutes_before is None:
                minutes_before = self.config["strategy"]["minutes_before_nfp"]
            
            nfp_release = self.get_next_nfp_release()
            order_time = nfp_release - datetime.timedelta(minutes=minutes_before)
            now = datetime.datetime.now(pytz.timezone('US/Eastern'))
            logger.info(f"Next NFP release: {nfp_release.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info(f"Scheduled order time: {order_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            wait_seconds = (order_time - now).total_seconds()
            if wait_seconds < 0:
                logger.error("Order time is in the past. Check system clock.")
                return None
            logger.info(f"Waiting {wait_seconds:.1f} seconds until order placement time")
            time.sleep(wait_seconds)
            
            # Wait until shortly after the release to allow data to update (e.g., 10 seconds after release)
            now_after = datetime.datetime.now(pytz.timezone('US/Eastern'))
            if now_after < nfp_release:
                delay = (nfp_release - now_after).total_seconds() + 10
                logger.info(f"Waiting additional {delay:.1f} seconds for NFP data update")
                time.sleep(delay)
            
            # Fetch NFP data
            nfp_data = self.get_nfp_report()
            if not nfp_data:
                logger.error("No NFP data available, aborting trade placement.")
                return None
            
            actual = float(nfp_data.get("Actual", 0))
            forecast = float(nfp_data.get("Forecast", 0))
            logger.info(f"NFP Data fetched: Actual = {actual}, Forecast = {forecast}")
            
            # Determine direction based on actual vs forecast
            if actual > forecast:
                action = "BUY"
                logger.info("NFP result is better than expected. Going LONG (BUY).")
            elif actual < forecast:
                action = "SELL"
                logger.info("NFP result is worse than expected. Going SHORT (SELL).")
            else:
                logger.info("NFP result is as expected. No clear directional bias, aborting trade.")
                return None
            
            bracket_order = self.place_usd_jpy_bracket_order(
                action=action,
                quantity=quantity,
                entry_price=0,  # Market order
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                transmit=True
            )
            if bracket_order:
                logger.info(f"NFP hedge order placed successfully for M6J: {bracket_order}")
            else:
                logger.error("Failed to place NFP hedge order.")
            return bracket_order
        
        except Exception as e:
            logger.error(f"Error placing NFP hedge orders: {e}")
            logger.debug(traceback.format_exc())
            return None

    def schedule_nfp_orders(self):
        """
        Schedule the placement of NFP orders for the next release.
        """
        nfp_release = self.get_next_nfp_release()
        minutes_before = self.config["strategy"]["minutes_before_nfp"]
        order_time = nfp_release - datetime.timedelta(minutes=minutes_before)
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        wait_seconds = (order_time - now).total_seconds()
        if wait_seconds < 0:
            logger.error("Order time is in the past. Check system clock.")
            return None
        logger.info(f"Next NFP release: {nfp_release.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info(f"Scheduled order time: {order_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info(f"Will place orders in {wait_seconds:.1f} seconds")
        timer = threading.Timer(
            wait_seconds,
            self.place_nfp_hedge_orders,
            kwargs={
                "quantity": self.config["strategy"]["quantity"].get("M6J", 2),
                "sl_pips": self.config["strategy"]["sl_pips"],
                "tp_pips": self.config["strategy"]["tp_pips"]
            }
        )
        timer.daemon = True
        timer.start()
        return order_time

    def calculate_usd_jpy_stop_loss(self, entry_price, sl_pips, is_buy):
        sl_ticks = sl_pips * self.pip_to_ticks['M6J']
        tick_size = self.tick_values['M6J']
        price_difference = sl_ticks * tick_size
        sl_price = entry_price - price_difference if is_buy else entry_price + price_difference
        sl_price = round(sl_price, 6)
        logger.debug(f"USD/JPY SL calculation: Entry={entry_price}, SL pips={sl_pips}, SL ticks={sl_ticks}, Price diff={price_difference}, Final SL={sl_price}")
        return sl_price
    
    def calculate_usd_jpy_take_profit(self, entry_price, tp_pips, is_buy):
        tp_ticks = tp_pips * self.pip_to_ticks['M6J']
        tick_size = self.tick_values['M6J']
        price_difference = tp_ticks * tick_size
        tp_price = entry_price + price_difference if is_buy else entry_price - price_difference
        tp_price = round(tp_price, 6)
        logger.debug(f"USD/JPY TP calculation: Entry={entry_price}, TP pips={tp_pips}, TP ticks={tp_ticks}, Price diff={price_difference}, Final TP={tp_price}")
        return tp_price
    
    def place_usd_jpy_bracket_order(self, action, quantity, entry_price=0.0, sl_pips=17, tp_pips=76, transmit=True):
        if not self.connected:
            logger.error("Not connected to IB API")
            return None
        try:
            contract = self.create_usd_jpy_micro_futures_contract()
            symbol = contract.symbol
            if entry_price <= 0:
                entry_price = self.get_market_price(contract)
                if entry_price is None:
                    logger.error(f"Failed to get market price for {symbol}")
                    return None
            is_buy = action == "BUY"
            reverse_action = "SELL" if is_buy else "BUY"
            sl_price = self.calculate_usd_jpy_stop_loss(entry_price, sl_pips, is_buy)
            tp_price = self.calculate_usd_jpy_take_profit(entry_price, tp_pips, is_buy)
            logger.info(f"Placing USD/JPY bracket order: {action} {quantity} @ {entry_price}")
            logger.info(f"  Stop Loss: {sl_price} ({sl_pips} pips)")
            logger.info(f"  Take Profit: {tp_price} ({tp_pips} pips)")
            parent_id = self.api.getNextOrderId()
            parent = Order()
            parent.orderId = parent_id
            parent.action = action
            parent.totalQuantity = quantity
            parent.orderType = "LMT" if entry_price > 0 else "MKT"
            if parent.orderType == "LMT":
                parent.lmtPrice = entry_price
            parent.transmit = False
            parent.tif = "GTC"
            sl_id = self.api.getNextOrderId()
            sl = Order()
            sl.orderId = sl_id
            sl.action = reverse_action
            sl.totalQuantity = quantity
            sl.orderType = "STP"
            sl.auxPrice = sl_price
            sl.parentId = parent_id
            sl.transmit = False
            sl.tif = "GTC"
            tp_id = self.api.getNextOrderId()
            tp = Order()
            tp.orderId = tp_id
            tp.action = reverse_action
            tp.totalQuantity = quantity
            tp.orderType = "LMT"
            tp.lmtPrice = tp_price
            tp.parentId = parent_id
            tp.transmit = transmit
            tp.tif = "GTC"
            self.api.placeOrder(parent_id, contract, parent)
            self.api.placeOrder(sl_id, contract, sl)
            self.api.placeOrder(tp_id, contract, tp)
            bracket_info = {
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sl_pips": sl_pips,
                "tp_pips": tp_pips,
                "parent_id": parent_id,
                "sl_id": sl_id,
                "tp_id": tp_id,
                "creation_time": datetime.datetime.now(),
                "status": "SUBMITTED"
            }
            self.active_orders[parent_id] = bracket_info
            return bracket_info
        except Exception as e:
            logger.error(f"Error placing USD/JPY bracket order: {e}")
            logger.debug(traceback.format_exc())
            return None

# ----------------- Main Execution -----------------

if __name__ == "__main__":
    try:
        config_path = 'config.json'
        if len(sys.argv) > 1:
            config_path = sys.argv[1]
        logger.info(f"Starting USD/JPY Micro Futures NFP Hedging Strategy with config: {config_path}")
        strategy = USDMicroFuturesStrategy(config_path)
        connected = strategy.connect()
        if not connected:
            logger.error("Failed to connect to Interactive Brokers. Exiting.")
            sys.exit(1)
        next_order_time = strategy.schedule_nfp_orders()
        if next_order_time:
            logger.info(f"NFP orders scheduled for {next_order_time}")
            try:
                logger.info("Strategy running. Press Ctrl+C to exit.")
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                logger.info("Shutting down gracefully...")
            finally:
                strategy.disconnect()
        else:
            logger.error("Failed to schedule NFP orders. Exiting.")
            strategy.disconnect()
            sys.exit(1)
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
