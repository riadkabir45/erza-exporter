from datetime import datetime, timedelta, timezone
import os
import re
import urllib3
import requests
import yaml

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://prepaid.desco.org.bd/api"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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

def escape_label_value(val):
    if val is None:
        return ""
    return str(val).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()

def parse_iso_or_date(date_str):
    """Parses various date/time formats to unix timestamp seconds."""
    if not date_str:
        return 0
    # Try ISO with offset e.g. 2026-11-18T00:00+06:00 or with Z
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        pass
    # Try YYYY-MM-DD HH:MM:SS(.f)
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(date_str, fmt)
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
    return 0

class DescoClient:
    def __init__(self, username, password, timeout=15):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.verify = False

    def login(self):
        # 1. Warm-up call to fetch XSRF cookie if present
        try:
            self.session.get(f"{BASE_URL}/accounts", timeout=self.timeout)
        except Exception:
            pass

        xsrf = self.session.cookies.get("XSRF-TOKEN")
        headers = {"Content-Type": "application/json"}
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf

        payload = {"username": self.username, "password": self.password}
        res = self.session.post(f"{BASE_URL}/auth/login", json=payload, headers=headers, timeout=self.timeout)
        if res.status_code != 200:
            raise RuntimeError(f"DESCO login failed with HTTP {res.status_code}: {res.text}")
        data = res.json()
        if not data.get("authenticated", False):
            raise RuntimeError(f"DESCO authentication rejected: {data}")
        return True

    def get(self, endpoint, params=None, retry_auth=True):
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        res = self.session.get(url, params=params, timeout=self.timeout)
        if res.status_code == 401 and retry_auth:
            self.login()
            res = self.session.get(url, params=params, timeout=self.timeout)
        res.raise_for_status()
        return res.json()

    def get_accounts(self):
        data = self.get("accounts")
        return data.get("accounts", [])

def meta():
    """Returns static headers and execution parameters for main.py"""
    config = load_module_config()
    return {
        "pre_scrape": config.get('pre_scrape', {'enabled': True, 'interval_seconds': 300}),
        "headers": [
            "# HELP desco_account_info Account and meter metadata info metric",
            "# TYPE desco_account_info gauge",
            "# HELP desco_balance_taka Daily snapshot account balance in BDT",
            "# TYPE desco_balance_taka gauge",
            "# HELP desco_balance_timestamp_seconds Unix timestamp when the daily snapshot was recorded",
            "# TYPE desco_balance_timestamp_seconds gauge",
            "# HELP desco_current_month_consumed_taka Current month cumulative consumption in BDT",
            "# TYPE desco_current_month_consumed_taka gauge",
            "# HELP desco_energy_reading_kwh_total Cumulative kWh meter counter reading",
            "# TYPE desco_energy_reading_kwh_total counter",
            "# HELP desco_daily_consumed_kwh Energy consumed yesterday in kWh",
            "# TYPE desco_daily_consumed_kwh gauge",
            "# HELP desco_daily_consumed_taka Energy cost yesterday in BDT",
            "# TYPE desco_daily_consumed_taka gauge",
            "# HELP desco_monthly_consumed_kwh Monthly consumption in kWh",
            "# TYPE desco_monthly_consumed_kwh gauge",
            "# HELP desco_monthly_consumed_taka Monthly cost in BDT",
            "# TYPE desco_monthly_consumed_taka gauge",
            "# HELP desco_monthly_max_demand_kw Maximum demand peak in kW",
            "# TYPE desco_monthly_max_demand_kw gauge",
            "# HELP desco_monthly_average_power_factor Average power factor (apf)",
            "# TYPE desco_monthly_average_power_factor gauge",
            "# HELP desco_lifeline_crossed Whether 50 kWh subsidized lifeline slab was crossed (1=yes, 0=no)",
            "# TYPE desco_lifeline_crossed gauge",
            "# HELP desco_lifeline_adjustment_taka Tariff adjustment fee added when lifeline threshold crossed",
            "# TYPE desco_lifeline_adjustment_taka gauge",
            "# HELP desco_recharge_last_amount_taka Gross amount of the last recharge in BDT",
            "# TYPE desco_recharge_last_amount_taka gauge",
            "# HELP desco_recharge_last_net_energy_taka Recharge amount credited to energy balance in BDT",
            "# TYPE desco_recharge_last_net_energy_taka gauge",
            "# HELP desco_recharge_last_charge_taka Non-energy deduction breakdown from last recharge",
            "# TYPE desco_recharge_last_charge_taka gauge",
            "# HELP desco_recharge_last_timestamp_seconds Unix timestamp of the last recharge transaction",
            "# TYPE desco_recharge_last_timestamp_seconds gauge",
            "# HELP desco_recharge_last_token_sequence Sequence number of the last token generated",
            "# TYPE desco_recharge_last_token_sequence gauge"
        ]
    }

def collect():
    """Fetches live data from DESCO API and produces Prometheus metrics"""
    config = load_module_config()
    auth_cfg = config.get("auth", {})
    username = auth_cfg.get("username")
    password = auth_cfg.get("password")
    timeout_sec = config.get("pre_scrape", {}).get("timeout_seconds", 15)
    filter_accounts = set(str(a) for a in config.get("accounts", []) if a)

    if not username or not password:
        return ["# Error: DESCO auth credentials missing in desco.yaml"]

    client = DescoClient(username=username, password=password, timeout=timeout_sec)
    try:
        client.login()
        accounts_data = client.get_accounts()
    except Exception as e:
        return [f"# Error initializing DESCO session or fetching accounts: {e}"]

    metrics = []
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    date_from_daily = (now - timedelta(days=7)).strftime('%Y-%m-%d')

    first_of_this_month = now.replace(day=1)
    current_month_str = now.strftime('%Y-%m')
    prev_month_str = (first_of_this_month - timedelta(days=1)).strftime('%Y-%m')

    date_from_tx = (now - timedelta(days=90)).strftime('%Y-%m-%d')

    for acc in accounts_data:
        account_id = acc.get("accountId")
        account_no = str(acc.get("accountNo", ""))
        meter_no = str(acc.get("meterNo", ""))

        if not account_id:
            continue
        if filter_accounts and account_no not in filter_accounts:
            continue

        base_labels = f'account="{account_no}",meter="{meter_no}"'

        # 1. Summary (Account Info)
        try:
            summary = client.get(f"accounts/{account_id}/summary")
            cust_name = escape_label_value(summary.get("customerName", ""))
            addr = escape_label_value(summary.get("installationAddress", ""))
            feeder = escape_label_value(summary.get("feederName", ""))
            phase = escape_label_value(summary.get("phaseType", ""))
            sanction_load = escape_label_value(summary.get("sanctionLoad", ""))
            tariff = escape_label_value(summary.get("tariffSolution", ""))
            meter_model = escape_label_value(summary.get("meterModel", ""))

            metrics.append(
                f'desco_account_info{{{base_labels},customer_name="{cust_name}",tariff="{tariff}",'
                f'sanction_load_kw="{sanction_load}",phase="{phase}",feeder="{feeder}",meter_model="{meter_model}"}} 1'
            )
        except Exception as e:
            print(f"Error fetching summary for account {account_no}: {e}")

        # 2. Balance
        try:
            bal_data = client.get(f"accounts/{account_id}/balance")
            balance = float(bal_data.get("balance", 0.0))
            curr_month_consumed = float(bal_data.get("currentMonthConsumedTaka", 0.0))
            snapshot_str = bal_data.get("balanceSnapshotAt", "")
            snapshot_ts = parse_iso_or_date(snapshot_str)

            metrics.extend([
                f'desco_balance_taka{{{base_labels}}} {balance}',
                f'desco_balance_timestamp_seconds{{{base_labels}}} {snapshot_ts}',
                f'desco_current_month_consumed_taka{{{base_labels}}} {curr_month_consumed}'
            ])
        except Exception as e:
            print(f"Error fetching balance for account {account_no}: {e}")

        # 3. Daily Usage, Lifeline & Meter Counter
        try:
            daily_data = client.get(f"accounts/{account_id}/usage/daily", params={"dateFrom": date_from_daily, "dateTo": today_str})
            days = daily_data.get("days", [])
            if days:
                latest_day = days[-1]
                cum_reading = float(latest_day.get("rawCumulativeUnit") or 0.0)
                daily_consumed_unit = float(latest_day.get("dailyConsumedUnit") or 0.0)
                daily_consumed_taka = float(latest_day.get("dailyConsumedTaka") or 0.0)

                metrics.extend([
                    f'desco_energy_reading_kwh_total{{{base_labels}}} {cum_reading}',
                    f'desco_daily_consumed_kwh{{{base_labels}}} {daily_consumed_unit}',
                    f'desco_daily_consumed_taka{{{base_labels}}} {daily_consumed_taka}'
                ])

            lifeline = daily_data.get("lifeline") or {}
            crossed_val = 1 if lifeline.get("crossed") else 0
            adj_taka = float(lifeline.get("adjustmentAmount") or 0.0)
            metrics.extend([
                f'desco_lifeline_crossed{{{base_labels}}} {crossed_val}',
                f'desco_lifeline_adjustment_taka{{{base_labels}}} {adj_taka}'
            ])
        except Exception as e:
            print(f"Error fetching daily usage for account {account_no}: {e}")

        # 4. Monthly Usage & Peak Demand
        try:
            monthly_list = client.get(f"accounts/{account_id}/usage/monthly", params={"monthFrom": prev_month_str, "monthTo": current_month_str})
            for m_item in monthly_list:
                m_str = m_item.get("month", "")
                period_tag = "current" if m_str == current_month_str else ("previous" if m_str == prev_month_str else m_str)
                m_unit = float(m_item.get("consumedUnit") or 0.0)
                m_taka = float(m_item.get("consumedTaka") or 0.0)
                max_demand = float(m_item.get("maximumDemand") or 0.0)
                apf = float(m_item.get("apf") or 1.0)

                metrics.extend([
                    f'desco_monthly_consumed_kwh{{{base_labels},period="{period_tag}"}} {m_unit}',
                    f'desco_monthly_consumed_taka{{{base_labels},period="{period_tag}"}} {m_taka}',
                    f'desco_monthly_max_demand_kw{{{base_labels},period="{period_tag}"}} {max_demand}',
                    f'desco_monthly_average_power_factor{{{base_labels},period="{period_tag}"}} {apf}'
                ])
        except Exception as e:
            print(f"Error fetching monthly usage for account {account_no}: {e}")

        # 5. Last Recharge & Charge Deductions
        try:
            tx_list = client.get(f"accounts/{account_id}/transactions", params={"dateFrom": date_from_tx, "dateTo": today_str})
            if tx_list:
                last_tx = tx_list[0]
                total_amt = float(last_tx.get("totalAmount") or 0.0)
                energy_amt = float(last_tx.get("energyAmount") or 0.0)
                operator = escape_label_value(last_tx.get("rechargeOperator") or "unknown")
                recharge_date = last_tx.get("rechargeDate", "")
                recharge_ts = parse_iso_or_date(recharge_date)
                seq_val = int(last_tx.get("sequence") or 0)

                vat = float(last_tx.get("vat") or 0.0)
                rebate = float(last_tx.get("rebate") or 0.0)
                demand_charge = float(last_tx.get("demandCharge") or 0.0)
                meter_rent = float(last_tx.get("meterRent") or 0.0)

                metrics.extend([
                    f'desco_recharge_last_amount_taka{{{base_labels},operator="{operator}"}} {total_amt}',
                    f'desco_recharge_last_net_energy_taka{{{base_labels}}} {energy_amt}',
                    f'desco_recharge_last_timestamp_seconds{{{base_labels}}} {recharge_ts}',
                    f'desco_recharge_last_token_sequence{{{base_labels}}} {seq_val}',
                    f'desco_recharge_last_charge_taka{{{base_labels},charge_type="vat"}} {vat}',
                    f'desco_recharge_last_charge_taka{{{base_labels},charge_type="rebate"}} {rebate}',
                    f'desco_recharge_last_charge_taka{{{base_labels},charge_type="demand_charge"}} {demand_charge}',
                    f'desco_recharge_last_charge_taka{{{base_labels},charge_type="meter_rent"}} {meter_rent}'
                ])
        except Exception as e:
            print(f"Error fetching transactions for account {account_no}: {e}")

    return metrics
