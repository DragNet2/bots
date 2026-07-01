# VPN Switch Bot - Keenetic Policy Manager

## Overview

Telegram bot for managing Keenetic router device policies (VPN_ON / VPN_OFF).

## Features

- View devices in VPN_ON and VPN_OFF policies
- Move devices between policies with inline keyboard buttons
- Real-time device status updates

## Architecture

```
vpn_switch_bot/
├── config.py       # Configuration (API credentials, bot token)
├── keenetic.py     # Keenetic RCI API client
├── bot.py          # Main bot logic with aiogram 3.x
├── keyboards.py     # Inline keyboards
├── requirements.txt # Python dependencies
└── .env            # Environment variables (local)
```

## API Endpoints Used

- `GET /rci/show/ip/hotspot` - List all devices
- `GET /rci/show/ip/policy` - List policies
- `POST /rci/` - Update device policy (batch commands)

## Keenetic Authentication

Challenge-response auth via `/auth` endpoint:
1. GET /auth → receive X-NDM-Challenge, X-NDM-Realm
2. Calculate: SHA256(challenge + MD5(login:realm:password))
3. POST /auth with credentials

## Polling Interval

5 seconds between policy checks

## Commands

- `/start` - Show main menu with policy buttons
- `/status` - Refresh device lists
- Inline buttons - Move devices between policies