from datetime import datetime, timedelta
import os
import requests
import yaml

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://prepaid.desco.org.bd/api/unified/customer"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

def load_module_config():
    config_path = os.path.join(os.path.dirname(__file__), 'desco.yaml')
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Error loading desco.yaml: {e}")
        return {}

def meta():
    """Returns static headers and execution parameters for main.py"""
    config = load_module_config()
    return {
        "pre_scrape": config.get('pre_scrape', {'enabled': True, 'interval_seconds': 300}),
        "headers": [
            "# HELP desco_balance_taka Current account balance in Taka",
            "# TYPE desco_balance_taka gauge",
            "# HELP desco_current_month_consumption_taka Current month consumption in Taka",
            "# TYPE desco_current_month_consumption_taka gauge",
            "# HELP desco_daily_consumed_taka Daily energy consumed in Taka",
            "# TYPE desco_daily_consumed_taka gauge",
            "# HELP desco_daily_consumed_units Daily energy consumed in Units (kWh)",
            "# TYPE desco_daily_consumed_units gauge",
            "# HELP desco_monthly_consumed_taka Monthly energy consumed in Taka",
            "# TYPE desco_monthly_consumed_taka gauge",
            "# HELP desco_monthly_consumed_units Monthly energy consumed in Units",
            "# TYPE desco_monthly_consumed_units gauge",
            "# HELP desco_monthly_max_demand Monthly maximum demand",
            "# TYPE desco_monthly_max_demand gauge",
            "# HELP desco_latest_recharge_total_amount Last recharge total amount in Taka",
            "# TYPE desco_latest_recharge_total_amount gauge",
            "# HELP desco_latest_recharge_energy_amount Last recharge energy amount in Taka",
            "# TYPE desco_latest_recharge_energy_amount gauge",
            "# HELP desco_latest_recharge_vat Last recharge VAT amount",
            "# TYPE desco_latest_recharge_vat gauge",
        ]
    }

def collect():
    """Fetches live data from DESCO API using a persistent session for speed"""
    config = load_module_config()
    meters_list = config.get('meters', [])
    timeout_sec = config.get('pre_scrape', {}).get('timeout_seconds', 15)
    
    if not meters_list:
        return []

    metrics = []
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    current_month_str = now.strftime('%Y-%m')
    first_of_current_month = now.replace(day=1)
    last_month_str = (first_of_current_month - timedelta(days=1)).strftime('%Y-%m')
    
    date_from_recharge = (now - timedelta(days=30)).strftime('%Y-%m-%d')

    # Use a persistent session to reuse TCP connections & SSL handshakes
    with requests.Session() as session:
        session.headers.update(HEADERS)
        session.verify = False

        for item in meters_list:
            account_no = item.get('account_no')
            meter_no = item.get('meter_no')
            
            if not account_no or not meter_no:
                continue

            # 1. Fetch Current Balance & Status
            try:
                res = session.get(
                    f"{BASE_URL}/getBalance",
                    params={"accountNo": account_no, "meterNo": meter_no},
                    timeout=timeout_sec
                )
                if res.status_code == 200:
                    data = res.json().get("data") or {}
                    balance = data.get("balance", 0.0)
                    current_month_consumption = data.get("currentMonthConsumption", 0.0)
                    
                    metrics.extend([
                        f'desco_balance_taka{{account="{account_no}",meter="{meter_no}"}} {balance}',
                        f'desco_current_month_consumption_taka{{account="{account_no}",meter="{meter_no}"}} {current_month_consumption}'
                    ])
            except Exception as e:
                print(f"Error fetching balance for {account_no}/{meter_no}: {e}")

            # 2. Daily Consumption
            for target_date, rel in [(today_str, "0"), (yesterday_str, "1")]:
                try:
                    res = session.get(
                        f"{BASE_URL}/getCustomerDailyConsumption",
                        params={"accountNo": account_no, "meterNo": meter_no, "dateFrom": target_date, "dateTo": target_date},
                        timeout=timeout_sec
                    )
                    daily_list = res.json().get("data") if res.status_code == 200 else []
                    
                    consumed_taka = 0.0
                    consumed_unit = 0.0
                    if daily_list:
                        day_data = daily_list[0]
                        consumed_taka = day_data.get("consumedTaka") or 0.0
                        consumed_unit = day_data.get("consumedUnit") or 0.0

                    metrics.extend([
                        f'desco_daily_consumed_taka{{account="{account_no}",meter="{meter_no}",relevance="{rel}",date="{target_date}"}} {consumed_taka}',
                        f'desco_daily_consumed_units{{account="{account_no}",meter="{meter_no}",relevance="{rel}",date="{target_date}"}} {consumed_unit}'
                    ])
                except Exception as e:
                    print(f"Error fetching daily consumption for {account_no}/{meter_no} on {target_date}: {e}")

            # 3. Monthly Consumption
            for target_month, rel in [(current_month_str, "0"), (last_month_str, "1")]:
                try:
                    res = session.get(
                        f"{BASE_URL}/getCustomerMonthlyConsumption",
                        params={"accountNo": account_no, "meterNo": meter_no, "monthFrom": target_month, "monthTo": target_month},
                        timeout=timeout_sec
                    )
                    monthly_list = res.json().get("data") if res.status_code == 200 else []

                    m_taka = 0.0
                    m_units = 0.0
                    max_demand = 0.0
                    if monthly_list:
                        m_data = monthly_list[0]
                        m_taka = m_data.get("consumedTaka", 0.0)
                        m_units = m_data.get("consumedUnit", 0.0)
                        max_demand = m_data.get("maximumDemand", 0.0)

                    metrics.extend([
                        f'desco_monthly_consumed_taka{{account="{account_no}",meter="{meter_no}",relevance="{rel}",month="{target_month}"}} {m_taka}',
                        f'desco_monthly_consumed_units{{account="{account_no}",meter="{meter_no}",relevance="{rel}",month="{target_month}"}} {m_units}',
                        f'desco_monthly_max_demand{{account="{account_no}",meter="{meter_no}",relevance="{rel}",month="{target_month}"}} {max_demand}'
                    ])
                except Exception as e:
                    print(f"Error fetching monthly consumption for {account_no}/{meter_no} in {target_month}: {e}")

            # 4. Recent Recharges
            try:
                res = session.get(
                    f"{BASE_URL}/getRechargeHistory",
                    params={"accountNo": account_no, "meterNo": meter_no, "dateFrom": date_from_recharge, "dateTo": today_str},
                    timeout=timeout_sec
                )
                if res.status_code == 200:
                    recharges = res.json().get("data", [])
                    if recharges:
                        latest = recharges[0]
                        total_amount = latest.get("totalAmount", 0.0)
                        energy_amount = latest.get("energyAmount", 0.0)
                        vat = latest.get("VAT", 0.0)
                        order_id = latest.get("orderID", "")
                        recharge_date = latest.get("rechargeDate", "")
                        
                        metrics.extend([
                            f'desco_latest_recharge_total_amount{{account="{account_no}",meter="{meter_no}",order_id="{order_id}",recharge_date="{recharge_date}"}} {total_amount}',
                            f'desco_latest_recharge_energy_amount{{account="{account_no}",meter="{meter_no}",order_id="{order_id}"}} {energy_amount}',
                            f'desco_latest_recharge_vat{{account="{account_no}",meter="{meter_no}",order_id="{order_id}"}} {vat}'
                        ])
            except Exception as e:
                print(f"Error fetching recharge history for {account_no}/{meter_no}: {e}")

    return metrics
