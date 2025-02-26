#!/bin/bash

# Server setup script for IB USD/JPY Micro Futures Hedging Strategy
# This script will install all dependencies and setup a dedicated user to run the strategy

# Set to exit on error
set -e

# Create log file
LOGFILE="setup_log_$(date +%Y%m%d_%H%M%S).log"
touch $LOGFILE
exec > >(tee -a "$LOGFILE") 2>&1

echo "=== USD/JPY Micro Futures Hedging Strategy - Server Setup ==="
echo "Starting setup at $(date)"

# Update system
echo "Updating system packages..."
apt-get update
apt-get upgrade -y

# Install necessary system dependencies
echo "Installing system dependencies..."
apt-get install -y build-essential python3 python3-pip python3-dev python3-venv openjdk-11-jre screen cron unzip htop nano git tmux ntp

# Configure NTP for accurate time
echo "Configuring NTP for accurate time synchronization..."
systemctl enable ntp
systemctl start ntp
sleep 2
echo "Current time: $(date)"

# Create a dedicated user to run the trading script
echo "Creating dedicated trading user..."
useradd -m -s /bin/bash ibtrader || echo "User ibtrader already exists"
usermod -aG sudo ibtrader

# Create the trading directory structure
echo "Creating directory structure..."
mkdir -p /home/ibtrader/brkfx/logs
mkdir -p /home/ibtrader/brkfx/data

# Copy script files to the trading directory
echo "Copying trading files..."
cp usd-micro.py /home/ibtrader/brkfx/
cp config.json /home/ibtrader/brkfx/
cp requirements.txt /home/ibtrader/brkfx/
cp .env.example /home/ibtrader/brkfx/.env
cp ib-trading.service /home/ibtrader/brkfx/

# Set proper ownership
chown -R ibtrader:ibtrader /home/ibtrader/brkfx

# Switch to ibtrader user directory
cd /home/ibtrader/

# Create virtual environment
echo "Setting up Python virtual environment..."
sudo -u ibtrader python3 -m venv /home/ibtrader/brkfx/venv
sudo -u ibtrader bash -c "source /home/ibtrader/brkfx/venv/bin/activate && pip install --upgrade pip"

# Install IB Gateway 
echo "Installing IB Gateway..."
mkdir -p /opt/IBGateway
cd /opt/IBGateway

# Please note that the exact URL may change. Check for the latest version.
wget -q https://download2.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh
chmod +x ibgateway-latest-standalone-linux-x64.sh
./ibgateway-latest-standalone-linux-x64.sh -q -dir /opt/IBGateway

# Install Python dependencies
echo "Installing Python dependencies..."
cd /home/ibtrader/brkfx/
sudo -u ibtrader bash -c "source venv/bin/activate && pip install -r requirements.txt"

# Create environment file for credentials
echo "Creating environment file for credentials..."
sudo -u ibtrader bash -c "cat > /home/ibtrader/brkfx/.env << EOL
# Interactive Brokers Credentials
IB_USERNAME=your_username
IB_PASSWORD=your_password
IB_ACCOUNT=your_account_id

# API Connection Settings
IB_HOST=127.0.0.1
IB_PORT=4001
IB_CLIENT_ID=1
EOL"

# Create a startup script
echo "Creating startup script..."
sudo -u ibtrader bash -c "cat > /home/ibtrader/brkfx/start_trading.sh << EOL
#!/bin/bash
# Startup script for brkfx USD/JPY Micro trading

# Start IB Gateway in the background
cd /opt/IBGateway
./ibgateway &

# Wait for IB Gateway to start
sleep 30

# Activate virtual environment
cd /home/ibtrader/brkfx
source venv/bin/activate

# Start trading script
python usd-micro.py > logs/trading_\$(date +%Y%m%d).log 2>&1
EOL"

sudo -u ibtrader chmod +x /home/ibtrader/brkfx/start_trading.sh

# Setup systemd service
echo "Setting up systemd service..."
cp /home/ibtrader/brkfx/ib-trading.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ib-trading.service

# Setup a monitoring script
echo "Setting up monitoring..."
sudo -u ibtrader bash -c "cat > /home/ibtrader/brkfx/monitor.sh << EOL
#!/bin/bash
# Monitor script for brkfx trading

# Check if process is running
if ! pgrep -f 'python usd-micro.py' > /dev/null; then
  echo \"[\$(date)] Trading script not running. Restarting...\" >> /home/ibtrader/brkfx/logs/monitor.log
  systemctl restart ib-trading.service
else
  echo \"[\$(date)] Trading script is running normally.\" >> /home/ibtrader/brkfx/logs/monitor.log
fi
EOL"

sudo -u ibtrader chmod +x /home/ibtrader/brkfx/monitor.sh

# Add monitoring to cron
sudo -u ibtrader bash -c "(crontab -l 2>/dev/null; echo '*/10 * * * * /home/ibtrader/brkfx/monitor.sh') | crontab -"

echo "=== Setup completed ==="
echo "Please modify the credentials in the .env file before starting the strategy."
echo "Start the strategy manually with: sudo systemctl start ib-trading.service"
echo "Check status with: sudo systemctl status ib-trading.service"
echo "View logs with: sudo journalctl -u ib-trading.service -f"
