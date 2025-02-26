#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
USD/JPY Micro Futures Hedging Strategy

An advanced implementation for automatic order execution on USD/JPY Micro Futures (M6J)
through Interactive Brokers API. Specializes in bracket orders for NFP releases
with 17 pip stop loss and 76 pip take profit.

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
        """Called when the next valid order ID is received"""
        super().nextValidId(orderId)
        self.next_order_id = orderId
        logger.info(f"Next valid order ID: {orderId}")
        self.order_event.set()
        
    def getNextOrderId(self):
        """Get the next valid order ID and increment the counter"""
        with self.lock:
            current_id = self.next_order_id
            self.next_order_id += 1
            return current_id
            
    def getNextReqId(self):
        """Get the next request ID"""
        with self.req_id_lock:
            current_id = self.next_req_id
            self.next_req_id += 1
            return current_id
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Called when an error occurs"""
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
        
        # Only log errors, not warnings
        if errorCode >= 2000:
            logger.error(f"Error {errorCode}: {errorString}")
            if advancedOrderRejectJson:
                logger.error(f"Advanced rejection: {advancedOrderRejectJson}")
        else:
            logger.warning(f"Warning {errorCode}: {errorString}")
            
        # Handle specific error cases
        if errorCode == 202:  # Order cancelled
            if reqId in self.orders:
                self.orders[reqId]['status'] = OrderStatus.CANCELLED
        elif errorCode == 201:  # Order rejected
            if reqId in self.orders:
                self.orders[reqId]['status'] = OrderStatus.ERROR
                self.orders[reqId]['error_message'] = errorString
    
    def position(self, account, contract, position, avgCost):
        """Called when a position update is received"""
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
        """Called when all positions have been received"""
        super().positionEnd()
        logger.info("Positions download completed")
        self.position_event.set()
    
    def accountSummary(self, reqId, account, tag, value, currency):
        """Called when account summary data is received"""
        super().accountSummary(reqId, account, tag, value, currency)
        
        with self.lock:
            if account not in self.account_summary:
                self.account_summary[account] = {}
            
            self.account_summary[account][tag] = {
                'value': value,
                'currency': currency
            }
    
    def accountSummaryEnd(self, reqId):
        """Called when account summary data has been received"""
        super().accountSummaryEnd(reqId)
        logger.info("Account summary download completed")
        self.account_event.set()
    
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, 
                   permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        """Called when order status is updated"""
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
            
            # Update our orders dictionary if this order ID exists
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
        """Called when contract details are received"""
        super().contractDetails(reqId, contractDetails)
        
        with self.lock:
            if reqId not in self.contract_details:
                self.contract_details[reqId] = []
            
            self.contract_details[reqId].append(contractDetails)
    
    def contractDetailsEnd(self, reqId):
        """Called when all contract details have been received"""
        super().contractDetailsEnd(reqId)
        logger.info(f"Contract details request {reqId} completed")
        self.contract_event.set()
    
    def tickPrice(self, reqId: TickerId, tickType: TickTypeEnum, price: float, attrib: TickAttrib):
        """Called when price data is received"""
        super().tickPrice(reqId, tickType, price, attrib)
        
        with self.lock:
            if reqId not in self.market_data:
                self.market_data[reqId] = {}
            
            # Store the tick data
            self.market_data[reqId][tickType] = price
            
            # For convenience, store common tick types with names
            if tickType == 1:  # Bid
                self.market_data[reqId]['bid'] = price
            elif tickType == 2:  # Ask
                self.market_data[reqId]['ask'] = price
            elif tickType == 4:  # Last
                self.market_data[reqId]['last'] = price
            elif tickType == 9:  # Close
                self.market_data[reqId]['close'] = price
        
        # Signal that market data is available
        self.market_data_event.set()
            
    def historicalData(self, reqId, bar):
        """Called when historical data is received"""
        super().historicalData(reqId, bar)
        
        with self.lock:
            if reqId not in self.historical_data:
                self.historical_data[reqId] = []
            
            # Convert to dictionary for easier handling
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
        """Called when all historical data has been received"""
        super().historicalDataEnd(reqId, start, end)
        logger.info(f"Historical data request {reqId} completed")
        
        # Convert to DataFrame for easier analysis
        with self.lock:
            if reqId in self.historical_data and self.historical_data[reqId]:
                self.historical_data[reqId] = pd.DataFrame(self.historical_data[reqId])
                if 'date' in self.historical_data[reqId].columns:
                    self.historical_data[reqId].set_index('date', inplace=True)
    
    def pnlSingle(self, reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value):
        """Called when PnL data is received"""
        super().pnlSingle(reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value)
        
        with self.lock:
            self.pnl[reqId] = {
                'position': pos,
                'dailyPnL': dailyPnL,
                'unrealizedPnL': unrealizedPnL, 
                'realizedPnL': realizedPnL,
                'value': value
            }


class USDMicroFuturesStrategy:
    """Main strategy class for USD/JPY Micro Futures trading"""
    
    def __init__(self, config_path='config.json'):
        """Initialize the strategy with configuration"""
        
        # Load configuration
        self.config = self._load_config(config_path)
        
        # Initialize API connection
        self.api = IBApi()
        self.connected = False
        self.api_thread = None
        
        # Strategy state
        self.account = self.config["account"]["ib_account"]
        self.active_orders = {}
        self.active_brackets = {}
        self.active_symbols = []
        
        # USD/JPY Micro futures contract specifications
        # M6J has different tick sizing than other currencies
        self.tick_values = {
            'M6J': 0.000001  # Tick value for M6J is 0.000001 (different scale)
        }
        
        # Contract multiplier for USD/JPY Micro Futures
        self.contract_multipliers = {
            'M6J': 1250000  # M6J has a contract value of 1,250,000 JPY
        }
        
        # Pip conversion for USD/JPY - important for SL/TP calculation
        # In M6J, 1 full pip (0.01) equals 10,000 ticks (0.000001)
        self.pip_to_ticks = {
            'M6J': 10000  # 1 pip = 10000 ticks for USD/JPY
        }
        
        # Market data subscriptions
        self.market_data_subscriptions = {}
        
        # Initialize monitor thread
        self.stop_event = Event()
        self.monitor_thread = None
        
    def _load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            # Provide reasonable defaults
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
        """Connect to IB API"""
        if self.connected:
            logger.info("Already connected to IB API")
            return True
        
        try:
            host = self.config["account"]["host"]
            port = self.config["account"]["port"]
            client_id = self.config["account"]["client_id"]
            
            # Connect with retry mechanism
            max_attempts = self.config["strategy"].get("max_retry_attempts", 3)
            reconnect_delay = self.config["strategy"].get("reconnect_delay", 60)
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Connecting to IB API at {host}:{port} (Attempt {attempt}/{max_attempts})")
                    
                    # Reset events before connecting
                    self.api.order_event.clear()
                    
                    # Connect
                    self.api.connect(host, port, client_id)
                    
                    # Start message processing in a thread
                    self.api_thread = Thread(target=self._run_client, daemon=True)
                    self.api_thread.start()
                    
                    # Wait for valid order ID with a timeout
                    if not self.api.order_event.wait(30):
                        raise TimeoutError("Timeout waiting for connection")
                    
                    self.connected = True
                    logger.info(f"Connected to IB API: {host}:{port}")
                    
                    # Start the monitoring thread
                    self.stop_event.clear()
                    self.monitor_thread = Thread(target=self._monitor_connection, daemon=True)
                    self.monitor_thread.start()
                    
                    # Request account updates
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
        """Disconnect from IB API"""
        if not self.connected:
            return True
        
        try:
            # Signal monitor thread to stop
            self.stop_event.set()
            
            # Cancel market data subscriptions
            for req_id in self.market_data_subscriptions:
                self.api.cancelMktData(req_id)
            
            # Disconnect from IB
            self.api.disconnect()
            self.connected = False
            
            # Wait for threads to terminate
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
        """Run the IB API client message loop"""
        try:
            self.api.run()
        except Exception as e:
            logger.error(f"Error in API client thread: {e}")
            logger.debug(traceback.format_exc())
            self.connected = False
    
    def _monitor_connection(self):
        """Monitor and maintain connection to IB API"""
        while not self.stop_event.is_set():
            try:
                # If connection is lost and auto-reconnect is enabled
                if not self.connected and self.config["strategy"].get("auto_reconnect", True):
                    logger.warning("Connection lost. Attempting to reconnect...")
                    self.connect()
                
                # Check for active orders and their status
                self._check_active_orders()
                
                # Sleep for a bit
                time.sleep(15)
                
            except Exception as e:
                logger.error(f"Error in monitor thread: {e}")
                logger.debug(traceback.format_exc())
                time.sleep(30)  # Sleep longer on error
    
    def _check_active_orders(self):
        """Check the status of active orders"""
        if not self.connected:
            return
        
        with self.api.lock:
            for order_id, order_info in list(self.active_orders.items()):
                # Check if we have status for this order
                if order_id in self.api.order_status_details:
                    status = self.api.order_status_details[order_id]["status"]
                    
                    # Update order info
                    order_info["current_status"] = status
                    order_info["last_update"] = datetime.datetime.now()
                    
                    # Log filled orders
                    if status == "Filled" and not order_info.get("fill_logged", False):
                        filled = self.api.order_status_details[order_id]["filled"]
                        avg_price = self.api.order_status_details[order_id]["avgFillPrice"]
                        logger.info(f"Order {order_id} filled: {filled} @ {avg_price}")
                        order_info["fill_logged"] = True
    
    def _request_account_updates(self):
        """Request account and position updates"""
        if not self.connected:
            return
        
        try:
            # Reset events
            self.api.position_event.clear()
            self.api.account_event.clear()
            
            # Request positions
            self.api.reqPositions()
            
            # Request account summary
            req_id = self.api.getNextReqId()
            self.api.reqAccountSummary(req_id, "All", "$LEDGER")
            
            # Wait for data
            logger.info("Waiting for account data...")
            self.api.position_event.wait(timeout=10)
            self.api.account_event.wait(timeout=10)
            
            # Log account info
            with self.api.lock:
                avail_funds = self.api.account_summary.get(self.account, {}).get("AvailableFunds", {}).get("value", "Unknown")
                net_liq = self.api.account_summary.get(self.account, {}).get("NetLiquidation", {}).get("value", "Unknown")
                logger.info(f"Account {self.account} - Available Funds: {avail_funds}, Net Liquidation: {net_liq}")
                
                # Log positions
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
        """Create a contract object specifically for USD/JPY micro futures"""
        contract = Contract()
        contract.symbol = "M6J"
        contract.secType = "FUT"
        contract.exchange = "CME"
        contract.currency = "USD"
        
        # USD futures typically follow quarterly cycle (Mar, Jun, Sep, Dec)
        today = datetime.datetime.now()
        month = today.month
        year = today.year
        
        # Determine the contract month based on quarterly cycle
        if month < 3:
            contract_month = 3  # March
        elif month < 6:
            contract_month = 6  # June
        elif month < 9:
            contract_month = 9  # September
        elif month < 12:
            contract_month = 12  # December
        else:
            contract_month = 3  # March of next year
            year += 1
            
        # Format the expiration date as YYYYMM
        contract.lastTradeDateOrContractMonth = f"{year}{contract_month:02d}"
        
        logger.info(f"Created USD/JPY micro futures contract expiring {year}{contract_month:02d}")
        
        return contract
    
    def get_market_price(self, contract, timeout=10):
        """Get current market price for a contract"""
        if not self.connected:
            return None
    
    def get_next_nfp_release(self):
        """
        Calculate the next NFP (Non-Farm Payroll) release date and time
        
        Returns:
        - datetime object for the next NFP release (8:30 AM ET on first Friday of month)
        """
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        first_day = datetime.datetime(now.year, now.month, 1, tzinfo=pytz.timezone('US/Eastern'))
        
        # Find the first Friday of the month
        days_until_friday = (4 - first_day.weekday()) % 7
        first_friday = first_day + datetime.timedelta(days=days_until_friday)
        
        # Set the time to 8:30 AM ET
        nfp_release = first_friday.replace(hour=8, minute=30, second=0, microsecond=0)
        
        # If NFP has already occurred this month, move to next month
        if now > nfp_release:
            if now.month == 12:
                first_day = datetime.datetime(now.year + 1, 1, 1, tzinfo=pytz.timezone('US/Eastern'))
            else:
                first_day = datetime.datetime(now.year, now.month + 1, 1, tzinfo=pytz.timezone('US/Eastern'))
                
            days_until_friday = (4 - first_day.weekday()) % 7
            first_friday = first_day + datetime.timedelta(days=days_until_friday)
            nfp_release = first_friday.replace(hour=8, minute=30, second=0, microsecond=0)
        
        return nfp_release
    
    def place_nfp_hedge_orders(self, quantity=None, sl_pips=None, tp_pips=None, minutes_before=None):
        """
        Place hedge orders (both buy and sell) before NFP release for USD/JPY micro futures
        
        Parameters:
        - quantity: Number of contracts (if None, uses config)
        - sl_pips: Stop loss in pips (if None, uses config)
        - tp_pips: Take profit in pips (if None, uses config)
        - minutes_before: Minutes before NFP release (if None, uses config)
        
        Returns:
        - Dictionary with order information
        """
        if not self.connected:
            logger.error("Not connected to IB API")
            return None
        
        try:
            # Get parameters from config if not specified
            if quantity is None:
                quantity = self.config["strategy"]["quantity"].get("M6J", 2)
            
            if sl_pips is None:
                sl_pips = self.config["strategy"]["sl_pips"]
                
            if tp_pips is None:
                tp_pips = self.config["strategy"]["tp_pips"]
                
            if minutes_before is None:
                minutes_before = self.config["strategy"]["minutes_before_nfp"]
            
            # Calculate NFP release date and time
            nfp_release = self.get_next_nfp_release()
            
            # Calculate when to place orders (e.g., 2 minutes before NFP)
            order_time = nfp_release - datetime.timedelta(minutes=minutes_before)
            
            # Current time
            now = datetime.datetime.now(pytz.timezone('US/Eastern'))
            
            logger.info(f"Next NFP release: {nfp_release.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info(f"Order placement time: {order_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            
            # Calculate wait time in seconds
            wait_seconds = (order_time - now).total_seconds()
            
            if wait_seconds < 0:
                logger.error("Order time is in the past. Check system clock.")
                return None
            
            # Print order details
            logger.info(f"Preparing NFP hedge orders for M6J (USD/JPY Micro)")
            logger.info(f"Quantity: {quantity}, SL: {sl_pips} pips, TP: {tp_pips} pips")
            logger.info(f"Waiting {wait_seconds:.1f} seconds until order placement time")
            
            # Wait until the specified time to place orders
            # For immediate testing, you can comment this out
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            
            logger.info(f"Placing NFP hedge orders for M6J now")
            
            # Place buy order with brackets
            buy_bracket = self.place_usd_jpy_bracket_order(
                action="BUY",
                quantity=quantity,
                entry_price=0,  # Market order
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                transmit=True
            )
            
            # Place sell order with brackets
            sell_bracket = self.place_usd_jpy_bracket_order(
                action="SELL",
                quantity=quantity,
                entry_price=0,  # Market order
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                transmit=True
            )
            
            # Store order information
            nfp_orders = {
                "symbol": "M6J",
                "nfp_date": nfp_release.strftime('%Y-%m-%d'),
                "order_time": order_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
                "sl_pips": sl_pips,
                "tp_pips": tp_pips,
                "buy_orders": buy_bracket,
                "sell_orders": sell_bracket,
                "status": "ACTIVE"
            }
            
            # Store in active orders
            self.active_brackets[nfp_release.strftime('%Y-%m-%d')] = nfp_orders
            
            logger.info(f"NFP hedge orders placed successfully for M6J")
            return nfp_orders
        
        except Exception as e:
            logger.error(f"Error placing NFP hedge orders: {e}")
            logger.debug(traceback.format_exc())
            return None
    
    def schedule_nfp_orders(self):
        """
        Schedule the placement of NFP orders for the next release
        
        Returns:
        - datetime of the scheduled order placement
        """
        # Get the next NFP release time
        nfp_release = self.get_next_nfp_release()
        
        # Get minutes before from config
        minutes_before = self.config["strategy"]["minutes_before_nfp"]
        
        # Calculate order placement time
        order_time = nfp_release - datetime.timedelta(minutes=minutes_before)
        
        # Current time
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        
        # Calculate wait time in seconds
        wait_seconds = (order_time - now).total_seconds()
        
        if wait_seconds < 0:
            logger.error("Order time is in the past. Check system clock.")
            return None
        
        logger.info(f"Next NFP release: {nfp_release.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info(f"Scheduled order time: {order_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info(f"Will place orders in {wait_seconds:.1f} seconds")
        
        # Schedule the order placement
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


# Main execution when script is run directly
if __name__ == "__main__":
    try:
        # Load configuration
        config_path = 'config.json'
        if len(sys.argv) > 1:
            config_path = sys.argv[1]
            
        logger.info(f"Starting USD/JPY Micro Futures NFP Hedging Strategy with config: {config_path}")
        
        # Create and initialize strategy
        strategy = USDMicroFuturesStrategy(config_path)
        
        # Connect to IB
        connected = strategy.connect()
        if not connected:
            logger.error("Failed to connect to Interactive Brokers. Exiting.")
            sys.exit(1)
        
        # Schedule NFP orders
        next_order_time = strategy.schedule_nfp_orders()
        
        if next_order_time:
            logger.info(f"NFP orders scheduled for {next_order_time}")
            
            # Keep the script running
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
        
        try:
            # Create a request ID and subscribe to market data
            req_id = self.api.getNextReqId()
            self.market_data_subscriptions[req_id] = contract.symbol
            
            # Reset market data event
            self.api.market_data_event.clear()
            
            # Request market data
            self.api.reqMktData(req_id, contract, "", False, False, [])
            
            # Wait for data
            if not self.api.market_data_event.wait(timeout=timeout):
                logger.warning(f"Timeout waiting for market data for {contract.symbol}")
                return None
            
            # Get the price
            with self.api.lock:
                if req_id in self.api.market_data:
                    # Prefer mid price if available
                    if 'bid' in self.api.market_data[req_id] and 'ask' in self.api.market_data[req_id]:
                        bid = self.api.market_data[req_id]['bid']
                        ask = self.api.market_data[req_id]['ask']
                        price = (bid + ask) / 2
                    # Fall back to last price
                    elif 'last' in self.api.market_data[req_id]:
                        price = self.api.market_data[req_id]['last']
                    # Fall back to close price
                    elif 'close' in self.api.market_data[req_id]:
                        price = self.api.market_data[req_id]['close']
                    else:
                        logger.warning(f"No usable price data for {contract.symbol}")
                        return None
                    
                    logger.info(f"Market price for {contract.symbol}: {price}")
                    return price
                else:
                    logger.warning(f"No market data received for {contract.symbol}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting market price: {e}")
            return None
        finally:
            # Cancel market data subscription
            if req_id in self.market_data_subscriptions:
                self.api.cancelMktData(req_id)
                del self.market_data_subscriptions[req_id]
    
    def calculate_usd_jpy_stop_loss(self, entry_price, sl_pips, is_buy):
        """
        Calculate stop loss price for USD/JPY with proper pip conversion
        
        Parameters:
        - entry_price: Entry price 
        - sl_pips: Stop loss in pips
        - is_buy: True if buy order, False if sell
        
        Returns:
        - Stop loss price
        """
        # For USD/JPY, pip is 0.01, but each tick is 0.000001
        # So we need to convert pips to ticks first
        sl_ticks = sl_pips * self.pip_to_ticks['M6J']
        
        # Each tick is 0.000001
        tick_size = self.tick_values['M6J']
        
        # Calculate actual price difference
        price_difference = sl_ticks * tick_size
        
        # Calculate stop loss price (opposite direction for buy vs sell)
        if is_buy:
            # For buy orders, stop loss is below entry
            sl_price = entry_price - price_difference
        else:
            # For sell orders, stop loss is above entry
            sl_price = entry_price + price_difference
        
        # Round to appropriate number of decimal places
        sl_price = round(sl_price, 6)
        
        logger.debug(f"USD/JPY SL calculation: Entry={entry_price}, SL pips={sl_pips}, "
                    f"SL ticks={sl_ticks}, Price diff={price_difference}, Final SL={sl_price}")
        
        return sl_price
    
    def calculate_usd_jpy_take_profit(self, entry_price, tp_pips, is_buy):
        """
        Calculate take profit price for USD/JPY with proper pip conversion
        
        Parameters:
        - entry_price: Entry price 
        - tp_pips: Take profit in pips
        - is_buy: True if buy order, False if sell
        
        Returns:
        - Take profit price
        """
        # For USD/JPY, pip is 0.01, but each tick is 0.000001
        # So we need to convert pips to ticks first
        tp_ticks = tp_pips * self.pip_to_ticks['M6J']
        
        # Each tick is 0.000001
        tick_size = self.tick_values['M6J']
        
        # Calculate actual price difference
        price_difference = tp_ticks * tick_size
        
        # Calculate take profit price (opposite direction for buy vs sell)
        if is_buy:
            # For buy orders, take profit is above entry
            tp_price = entry_price + price_difference
        else:
            # For sell orders, take profit is below entry
            tp_price = entry_price - price_difference
        
        # Round to appropriate number of decimal places
        tp_price = round(tp_price, 6)
        
        logger.debug(f"USD/JPY TP calculation: Entry={entry_price}, TP pips={tp_pips}, "
                    f"TP ticks={tp_ticks}, Price diff={price_difference}, Final TP={tp_price}")
        
        return tp_price
    
    def place_usd_jpy_bracket_order(self, action, quantity, entry_price=0.0, 
                                   sl_pips=17, tp_pips=76, transmit=True):
        """
        Place a bracket order specifically for USD/JPY micro futures
        
        Parameters:
        - action: 'BUY' or 'SELL'
        - quantity: Number of contracts
        - entry_price: Entry price (0 for market order)
        - sl_pips: Stop loss in pips (default 17 pips)
        - tp_pips: Take profit in pips (default 76 pips)
        - transmit: Whether to transmit orders immediately
        
        Returns:
        - Dictionary with order IDs and status
        """
        if not self.connected:
            logger.error("Not connected to IB API")
            return None
        
        try:
            # Create USD/JPY contract
            contract = self.create_usd_jpy_micro_futures_contract()
            symbol = contract.symbol
            
            # Get market price if entry price not specified
            if entry_price <= 0:
                entry_price = self.get_market_price(contract)
                if entry_price is None:
                    logger.error(f"Failed to get market price for {symbol}")
                    return None
            
            # Calculate opposite action for SL and TP
            is_buy = action == "BUY"
            reverse_action = "SELL" if is_buy else "BUY"
            
            # Calculate SL and TP prices with special handling for USD/JPY
            sl_price = self.calculate_usd_jpy_stop_loss(entry_price, sl_pips, is_buy)
            tp_price = self.calculate_usd_jpy_take_profit(entry_price, tp_pips, is_buy)
            
            logger.info(f"Placing USD/JPY bracket order: {action} {quantity} @ {entry_price}")
            logger.info(f"  Stop Loss: {sl_price} ({sl_pips} pips)")
            logger.info(f"  Take Profit: {tp_price} ({tp_pips} pips)")
            
            # Create parent order (entry)
            parent_id = self.api.getNextOrderId()
            parent = Order()
            parent.orderId = parent_id
            parent.action = action
            parent.totalQuantity = quantity
            parent.orderType = "LMT" if entry_price > 0 else "MKT"
            
            if parent.orderType == "LMT":
                parent.lmtPrice = entry_price
            
            parent.transmit = False  # Don't transmit yet
            parent.tif = "GTC"  # Good till cancel
            
            # Create stop loss order
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
            
            # Create take profit order
            tp_id = self.api.getNextOrderId()
            tp = Order()
            tp.orderId = tp_id
            tp.action = reverse_action
            tp.totalQuantity = quantity
            tp.orderType = "LMT"
            tp.lmtPrice = tp_price
            tp.parentId = parent_id
            tp.transmit = transmit  # Transmit all orders if requested
            tp.tif = "GTC"
            
            # Place the orders
            self.api.placeOrder(parent_id, contract, parent)
            self.api.placeOrder(sl_id, contract, sl)
            self.api.placeOrder(tp_id, contract, tp)
            
            # Store order details
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
            
            # Store in active orders
            self.active_orders[parent_id] = bracket_info
            
            return bracket_info
            
        except Exception as e:
            logger.error(f"Error placing USD/JPY bracket order: {e}")
            logger.debug(traceback.format_exc())
            return None
