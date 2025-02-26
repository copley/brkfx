# brkfx Repository Structure

This document explains the organization of the brkfx repository files and their purposes.

## Core Files

| File | Description |
|------|-------------|
| `usd-micro.py` | Main Python script containing the trading logic for USD/JPY micro futures |
| `config.json` | Configuration file for account settings, trading parameters, and logging options |
| `.env.example` | Template for environment variables (credentials) |
| `requirements.txt` | Python dependencies for the project |

## Deployment Files

| File | Description |
|------|-------------|
| `server-setup.sh` | Automated server setup script for Ubuntu systems |
| `ib-trading.service` | Systemd service definition for running as a system service |
| `DEPLOYMENT.md` | Detailed deployment guide with step-by-step instructions |

## Documentation

| File | Description |
|------|-------------|
| `README.md` | Main project documentation and overview |
| `LICENSE` | MIT license file |
| `REPOSITORY_STRUCTURE.md` | This file - explains repository organization |

## Directory Structure After Deployment

When deployed, the following directory structure is created:

```
/home/ibtrader/brkfx/
├── usd-micro.py          # Main trading script
├── config.json           # Configuration file
├── .env                  # Environment variables with credentials
├── requirements.txt      # Python dependencies
├── venv/                 # Python virtual environment
├── logs/                 # Log files
│   ├── trading_YYYYMMDD.log  # Daily trading logs
│   └── monitor.log           # Monitoring logs
├── data/                 # Data directory for storing trading data
├── start_trading.sh      # Script to start trading manually
└── monitor.sh            # Script to monitor and restart if needed
```

## Key Code Components

The main script (`usd-micro.py`) contains the following key components:

1. `IBApi` class - Extends the Interactive Brokers API with specialized wrapper functions
2. `USDMicroFuturesStrategy` class - Contains the primary trading logic:
   - `create_usd_jpy_micro_futures_contract` - Creates the USD/JPY micro futures contract
   - `calculate_usd_jpy_stop_loss` and `calculate_usd_jpy_take_profit` - Handle pip calculations specifically for USD/JPY
   - `place_usd_jpy_bracket_order` - Places bracket orders with SL/TP
   - `get_next_nfp_release` - Calculates the next NFP release date
   - `place_nfp_hedge_orders` - Places both buy and sell orders before NFP
   - `schedule_nfp_orders` - Schedules the order placement 2 minutes before NFP

## Configuration Components

The configuration file (`config.json`) is organized in the following sections:

1. `account` - Interactive Brokers account settings
2. `strategy` - Trading parameters including:
   - Symbols to trade (fixed to M6J for USD/JPY)
   - Contract quantities
   - Stop-loss and take-profit values in pips (17 and 76)
   - Minutes before NFP to place orders (2)
3. `logging` - Log file settings
4. `notifications` - Email and Telegram notification options

## Service Management

The systemd service file (`ib-trading.service`) manages:

1. Automatic startup of the trading script
2. Restart on failure
3. Proper environment loading
4. Log handling

## Development and Extension

If you want to extend the system:

1. Key areas for extension are in the `USDMicroFuturesStrategy` class
2. The pip calculation logic is specifically tuned for USD/JPY (M6J)
3. The NFP timing code is in the `get_next_nfp_release` function
