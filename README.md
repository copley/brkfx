# brkfx

A high-performance USD/JPY Micro Futures (M6J) hedging system for Interactive Brokers, specializing in bracket orders timed with NFP (Non-Farm Payroll) releases.

## Overview

brkfx automatically places bracket orders (entry + stop loss + take profit) on USD/JPY Micro Futures contracts exactly 2 minutes before NFP release. The system handles both buy and sell sides simultaneously, creating a perfect hedging strategy for high-volatility NFP events.

## Features

- Precisely timed bracket orders for NFP releases
- Specifically optimized for USD/JPY Micro Futures (M6J)
- Hardcoded 17 pip stop loss and 76 pip take profit parameters
- Automatic calculation of next NFP release date
- Proper handling of USD/JPY pip scaling (different from other pairs)
- Continuous monitoring and automatic recovery
- Detailed logging and optional notifications
- Complete server deployment scripts

## Requirements

- Linux server (Ubuntu 20.04+ recommended)
- Python 3.8+
- Interactive Brokers account with API access
- IB Gateway or TWS (Trader Workstation)

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/brkfx.git
cd brkfx
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Settings

Copy and edit the configuration files:

```bash
cp config.json.example config.json
nano config.json  # Edit with your settings

cp .env.example .env
nano .env  # Add your IB credentials
```

### 4. Run Locally

```bash
python usd-micro.py
```

## Server Deployment

### Automatic Deployment

The fastest way to deploy on a fresh server:

```bash
# Make the script executable
chmod +x server-setup.sh

# Run the setup script
sudo ./server-setup.sh
```

This script will:
- Install all system dependencies
- Create a dedicated trading user
- Set up IB Gateway
- Configure the Python environment
- Set up monitoring and auto-restart

### Manual Deployment Steps

If you prefer to deploy manually:

1. Install system dependencies:

```bash
sudo apt-get update
sudo apt-get install -y build-essential python3 python3-pip python3-dev python3-venv openjdk-11-jre
```

2. Create a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Install IB Gateway:

```bash
wget https://download2.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh
chmod +x ibgateway-latest-standalone-linux-x64.sh
sudo ./ibgateway-latest-standalone-linux-x64.sh
```

4. Set up the systemd service:

```bash
sudo cp ib-trading.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ib-trading.service
sudo systemctl start ib-trading.service
```

## Systemd Service Management

Start the service:
```bash
sudo systemctl start ib-trading.service
```

Check status:
```bash
sudo systemctl status ib-trading.service
```

View logs:
```bash
journalctl -u ib-trading.service -f
```

## Configuration

### Core Settings (config.json)

```json
{
  "account": {
    "ib_account": "YOUR_ACCOUNT_ID",
    "port": 4001,
    "host": "127.0.0.1"
  },
  "strategy": {
    "symbols": ["M6J"],
    "quantity": {
      "M6J": 2
    },
    "sl_pips": 17,
    "tp_pips": 76,
    "minutes_before_nfp": 2
  }
}
```

### Environment Variables (.env)

```
IB_USERNAME=your_username
IB_PASSWORD=your_password
IB_ACCOUNT=your_account
```

## Understanding USD/JPY Pip Values

For USD/JPY Micro Futures (M6J):
- 1 pip = 0.01 price movement
- 1 pip = 10,000 ticks (each tick is 0.000001)
- Contract multiplier is 1,250,000 JPY

The system automatically handles the special pip scaling for USD/JPY, converting the 17 and 76 pip values correctly.

## Security Considerations

- Never run as root
- Use a dedicated user account
- Store credentials in .env (not hardcoded)
- Restrict file permissions on credential files
- Consider using a VPN for remote access

## Monitoring and Maintenance

The setup script installs a monitoring cron job that runs every 10 minutes to ensure the trading script is running. If it detects the script has crashed, it will automatically restart it.

Check monitoring logs:
```bash
cat /home/ibtrader/ib_trading/monitor.log
```

## Troubleshooting

### Common Issues

1. **Connection Errors**
   - Verify IB Gateway is running
   - Check port and host settings
   - Ensure API access is enabled in IB account

2. **Authentication Failures**
   - Verify credentials in .env file
   - Check IB account permissions

3. **Order Placement Failures**
   - Verify account has sufficient margin
   - Check contract symbols match IB specifications

4. **NFP Timing Issues**
   - Ensure server time is synchronized (use NTP)
   - Check timezone settings (should be US/Eastern for NFP calculations)

## Next Steps & Customization

### Modifying Stop Loss and Take Profit

If you need to modify the default 17 pip SL and 76 pip TP values:

1. Update the `config.json` file:
```json
"strategy": {
  "sl_pips": 20,  // Change to your desired SL
  "tp_pips": 80   // Change to your desired TP
}
```

### Adding More Currency Pairs

The system is currently optimized for USD/JPY, but can be extended to other currencies by:

1. Adding proper pip handling for that currency
2. Updating the config to include the new symbol
3. Implementing any special handling required

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

Trading futures involves substantial risk of loss and is not suitable for all investors. Past performance is not indicative of future results. This software is provided for educational and informational purposes only and should not be considered financial advice.

## Acknowledgments

- Interactive Brokers API
- Python ib_insync library contributors
