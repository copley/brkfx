# brkfx Deployment Guide

This guide provides step-by-step instructions for deploying the brkfx USD/JPY micro futures trading system on a production server.

## Server Requirements

- Ubuntu 20.04 LTS or newer
- At least 2GB RAM
- 20GB disk space
- Reliable internet connection

## Initial Server Setup

1. Update your server:

```bash
sudo apt update && sudo apt upgrade -y
```

2. Set the correct timezone for accurate NFP timing:

```bash
sudo timedatectl set-timezone America/New_York
```

3. Install NTP to ensure accurate time:

```bash
sudo apt install -y ntp
sudo systemctl start ntp
sudo systemctl enable ntp
```

## Deployment Options

There are two ways to deploy the system:

### Option 1: Automated Deployment (Recommended)

1. Clone the repository:

```bash
git clone https://github.com/yourusername/brkfx.git
cd brkfx
```

2. Run the setup script:

```bash
chmod +x server-setup.sh
sudo ./server-setup.sh
```

3. Configure your credentials:

```bash
sudo nano /home/ibtrader/brkfx/.env
```

4. Start the service:

```bash
sudo systemctl start ib-trading.service
```

### Option 2: Manual Deployment

If you prefer to setup everything manually, follow these steps:

1. Clone the repository:

```bash
git clone https://github.com/yourusername/brkfx.git
cd brkfx
```

2. Create a dedicated user:

```bash
sudo useradd -m -s /bin/bash ibtrader
```

3. Create the directory structure:

```bash
sudo mkdir -p /home/ibtrader/brkfx/logs
sudo mkdir -p /home/ibtrader/brkfx/data
```

4. Copy the files:

```bash
sudo cp usd-micro.py config.json requirements.txt .env.example /home/ibtrader/brkfx/
sudo cp .env.example /home/ibtrader/brkfx/.env
sudo cp ib-trading.service /etc/systemd/system/
```

5. Set the proper ownership:

```bash
sudo chown -R ibtrader:ibtrader /home/ibtrader/brkfx
```

6. Create a Python virtual environment:

```bash
sudo -u ibtrader bash -c "cd /home/ibtrader/brkfx && python3 -m venv venv"
sudo -u ibtrader bash -c "cd /home/ibtrader/brkfx && source venv/bin/activate && pip install -r requirements.txt"
```

7. Install IB Gateway:

```bash
wget https://download2.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh
chmod +x ibgateway-latest-standalone-linux-x64.sh
sudo ./ibgateway-latest-standalone-linux-x64.sh
```

8. Configure the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ib-trading.service
sudo systemctl start ib-trading.service
```

## Post-Deployment Steps

### 1. Verify the Service is Running

```bash
sudo systemctl status ib-trading.service
```

You should see "active (running)" in the output.

### 2. Check the Logs

```bash
sudo journalctl -u ib-trading.service -f
```

You should see messages about connecting to IB and calculating the next NFP release date.

### 3. Configure Interactive Brokers Gateway

The Interactive Brokers Gateway needs to be configured to:

- Auto-login at startup
- Allow API connections
- Use the correct account

Follow these steps:

1. Access the IB Gateway configuration (typically in `/opt/IBGateway/`):

```bash
cd /opt/IBGateway
./ibgateway
```

2. Navigate to Configure > Settings
3. Enable API connections and set port to 4001
4. Configure auto-login (File > Global Configuration > API > Settings)

### 4. Testing the System

To test the system without waiting for the actual NFP release:

1. Edit the configuration file to use a small test amount:

```bash
sudo nano /home/ibtrader/brkfx/config.json
```

2. Set a temporary NFP timing for testing (edit the `usd-micro.py` file):

```python
# For testing, return a time 2 minutes in the future
def get_next_nfp_release(self):
    return datetime.datetime.now(pytz.timezone('US/Eastern')) + datetime.timedelta(minutes=3)
```

3. Restart the service:

```bash
sudo systemctl restart ib-trading.service
```

## Monitoring and Maintenance

### Daily Monitoring

Check the status every day:

```bash
sudo systemctl status ib-trading.service
```

### Log Rotation

Logs are automatically rotated, but you can check them:

```bash
ls -la /home/ibtrader/brkfx/logs/
```

### Backup Configuration

Backup your configuration files regularly:

```bash
sudo cp /home/ibtrader/brkfx/config.json /home/ibtrader/brkfx/config.json.bak
sudo cp /home/ibtrader/brkfx/.env /home/ibtrader/brkfx/.env.bak
```

## Troubleshooting

### Common Issues

1. **Connection Errors**:
   - Check if IB Gateway is running
   - Verify firewall settings
   - Check credentials in .env file

2. **Time Synchronization Issues**:
   - Verify NTP is running: `sudo systemctl status ntp`
   - Check server timezone: `timedatectl`

3. **Order Placement Failures**:
   - Check account balance and permissions
   - Verify market hours for USD/JPY futures

### Getting Help

If you encounter issues:

1. Check the logs: `sudo journalctl -u ib-trading.service -f`
2. File an issue on GitHub
3. Contact support

## Updating the System

To update the system when new versions are released:

```bash
cd /path/to/brkfx
git pull
sudo cp usd-micro.py /home/ibtrader/brkfx/
sudo systemctl restart ib-trading.service
```
