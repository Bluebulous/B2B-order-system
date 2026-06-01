import os
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import Client, create_client

from utils.formatting import format_df_cols, safe_number


try:
    SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("⚠️ 系統找不到 Supabase 金鑰！請確定已在 Render 設定 Environment Variables。")
    st.stop()


@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


supabase: Client = init_connection()


@st.cache_data(ttl=60)
def get_products_data():
    try:
        response = supabase.table("products").select("*").execute()
        df = pd.DataFrame(response.data)
        df = format_df_cols(df)
        if not df.empty:
            for col in ['Size', 'Name', 'Color', 'Category', 'Brand']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"無法讀取產品資料: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def get_brand_rules():
    try:
        response = supabase.table("brandrules").select("*").execute()
        df = pd.DataFrame(response.data)
        df = format_df_cols(df)
        rules = {}
        if not df.empty:
            for _, row in df.iterrows():
                rules[str(row.get('Brand', '')).strip()] = {
                    'wholesale_threshold': int(row.get('Wholesale_Threshold', 10000)),
                    'shipping_threshold': int(row.get('Shipping_Threshold', 10000)),
                    'discount_rate': float(row.get('Discount', 0.7))
                }
        return rules, df
    except Exception:
        default_df = pd.DataFrame([{"Brand": "default", "Wholesale_Threshold": 10000, "Shipping_Threshold": 10000, "Discount": 0.7}])
        return {"default": {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}}, default_df


def get_data(table_name):
    try:
        response = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(response.data)
        return format_df_cols(df)
    except Exception as e:
        print(f"Read {table_name} error: {e}")
        return pd.DataFrame()


def insert_data(table_name, data_dict):
    try:
        lower_dict = {k.lower(): v for k, v in data_dict.items()}
        supabase.table(table_name).insert(lower_dict).execute()
        return True
    except Exception as e:
        st.error(f"寫入 {table_name} 失敗: {e}")
        return False


def update_data_by_id(table_name, match_col, match_val, update_dict):
    try:
        lower_dict = {k.lower(): v for k, v in update_dict.items()}
        supabase.table(table_name).update(lower_dict).eq(match_col.lower(), match_val).execute()
        return True
    except Exception as e:
        st.error(f"更新 {table_name} 失敗: {e}")
        return False


def delete_data_by_id(table_name, match_col, match_val):
    try:
        supabase.table(table_name).delete().eq(match_col.lower(), match_val).execute()
        return True
    except Exception as e:
        st.error(f"刪除 {table_name} 失敗: {e}")
        return False


def save_brand_rules(edited_df, original_df):
    clean_records = []
    for record in edited_df.to_dict(orient='records'):
        brand = str(record.get('Brand', '')).strip()
        if not brand:
            continue
        clean_records.append({
            "Brand": brand,
            "Wholesale_Threshold": int(safe_number(record.get('Wholesale_Threshold', 0), 0)),
            "Shipping_Threshold": int(safe_number(record.get('Shipping_Threshold', 0), 0)),
            "Discount": float(safe_number(record.get('Discount', 1), 1)),
        })

    original_brands = set()
    if not original_df.empty and 'Brand' in original_df.columns:
        original_brands = {str(brand).strip() for brand in original_df['Brand'].dropna() if str(brand).strip()}

    edited_brands = {record["Brand"] for record in clean_records}

    for record in clean_records:
        brand = record["Brand"]
        if brand in original_brands:
            if not update_data_by_id("brandrules", "Brand", brand, record):
                return False
        else:
            if not insert_data("brandrules", record):
                return False

    for removed_brand in original_brands - edited_brands:
        if not delete_data_by_id("brandrules", "Brand", removed_brand):
            return False

    return True


def log_system_event(user_data, action, details=""):
    try:
        new_log = {
            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Username": user_data.get('Username', 'Unknown'),
            "Dealer": user_data.get('Dealer_Name', 'Unknown'),
            "Action": action,
            "Details": details
        }
        insert_data("systemlogs", new_log)
    except Exception as e:
        print(f"Log Error (Ignored): {e}")


@st.cache_data(ttl=600)
def get_announcement():
    try:
        df = get_data("announcements")
        if not df.empty and 'Message' in df.columns:
            msgs = df['Message'].dropna().tolist()
            if msgs:
                return str(msgs[-1])
        return ""
    except Exception:
        return ""
