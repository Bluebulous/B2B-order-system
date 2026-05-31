import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
import json
import time

from config import ENABLE_DEFAULT_ADMIN, SHIPPING_FEE, TAX_RATE
from services.email_service import send_order_email
from services.shopline_service import (
    fetch_product_stocks,
    fetch_products,
    fetch_warehouses,
    is_shopline_configured,
    normalize_products,
    normalize_stocks,
    normalize_warehouses,
)
from services.supabase_service import (
    delete_data_by_id,
    get_announcement,
    get_brand_rules,
    get_data,
    get_products_data,
    insert_data,
    log_system_event,
    save_brand_rules,
    update_data_by_id,
)
from utils.formatting import convert_drive_url, display_status_badges, escape_html
from utils.security import hash_password, is_admin, is_password_hash, verify_password


def clean_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def first_existing_column(df, candidates):
    for column in candidates:
        if column in df.columns:
            return column
    return None


def first_populated_number_column(df, candidates):
    fallback = None
    for column in candidates:
        if column not in df.columns:
            continue
        if fallback is None:
            fallback = column
        numeric_values = pd.to_numeric(df[column], errors="coerce")
        if numeric_values.notna().any():
            return column
    return fallback


def parse_order_items(items_json):
    if isinstance(items_json, str):
        try:
            items_json = json.loads(items_json)
        except Exception:
            return []
    if isinstance(items_json, dict):
        return list(items_json.values())
    if isinstance(items_json, list):
        return items_json
    return []


def build_recent_sales_summary(orders, days=30):
    if orders.empty or 'Items_Json' not in orders.columns:
        return pd.DataFrame()

    orders = orders.copy()
    orders['Order_Time_dt'] = pd.to_datetime(orders.get('Order_Time', ''), errors='coerce')
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
    recent_orders = orders[orders['Order_Time_dt'].notna() & (orders['Order_Time_dt'] >= cutoff)]
    rows = []

    for _, order in recent_orders.iterrows():
        for item in parse_order_items(order.get('Items_Json', {})):
            qty = pd.to_numeric(item.get('qty', 0), errors='coerce')
            if pd.isna(qty):
                qty = 0
            subtotal = pd.to_numeric(item.get('final_subtotal', 0), errors='coerce')
            if pd.isna(subtotal):
                final_price = pd.to_numeric(item.get('final_price', item.get('price', 0)), errors='coerce')
                subtotal = (0 if pd.isna(final_price) else final_price) * qty
            rows.append({
                "Product_ID": clean_text(item.get("id") or item.get("product_id") or item.get("Product_ID")),
                "Name": clean_text(item.get("name") or item.get("Name")),
                "Spec": clean_text(item.get("spec") or item.get("Spec")),
                "Brand": clean_text(item.get("brand") or item.get("Brand")),
                "Category": clean_text(item.get("category") or item.get("Category")),
                "Last_30D_Qty": int(qty),
                "Last_30D_Revenue": int(subtotal),
            })

    if not rows:
        return pd.DataFrame()

    sales = pd.DataFrame(rows)
    group_cols = ["Product_ID", "Name", "Spec", "Brand", "Category"]
    return sales.groupby(group_cols, dropna=False, as_index=False).agg(
        Last_30D_Qty=("Last_30D_Qty", "sum"),
        Last_30D_Revenue=("Last_30D_Revenue", "sum"),
    )


def apply_shopline_live_stock(inventory_df, shopline_stock_df, updated_at):
    if shopline_stock_df is None or shopline_stock_df.empty:
        return inventory_df

    inventory_df = inventory_df.copy()
    shopline_stock_df = shopline_stock_df.copy()
    shopline_stock_df["Stock"] = pd.to_numeric(shopline_stock_df.get("Stock", pd.NA), errors="coerce")

    matched = pd.Series(False, index=inventory_df.index)

    if "Shopline_SKU" in inventory_df.columns and "Shopline_SKU" in shopline_stock_df.columns:
        sku_stock = (
            shopline_stock_df[shopline_stock_df["Shopline_SKU"].apply(clean_text) != ""]
            .groupby("Shopline_SKU", as_index=False)["Stock"]
            .sum()
        )
        if not sku_stock.empty:
            sku_map = dict(zip(sku_stock["Shopline_SKU"].astype(str), sku_stock["Stock"]))
            sku_values = inventory_df["Shopline_SKU"].astype(str).map(sku_map)
            sku_matched = sku_values.notna()
            inventory_df.loc[sku_matched, "Display_Stock"] = sku_values[sku_matched]
            matched = matched | sku_matched

    if "Shopline_Product_ID" in inventory_df.columns and "Shopline_Product_ID" in shopline_stock_df.columns:
        product_stock = (
            shopline_stock_df[shopline_stock_df["Shopline_Product_ID"].apply(clean_text) != ""]
            .groupby("Shopline_Product_ID", as_index=False)["Stock"]
            .sum()
        )
        if not product_stock.empty:
            product_map = dict(zip(product_stock["Shopline_Product_ID"].astype(str), product_stock["Stock"]))
            product_values = inventory_df["Shopline_Product_ID"].astype(str).map(product_map)
            product_matched = product_values.notna() & ~matched
            inventory_df.loc[product_matched, "Display_Stock"] = product_values[product_matched]
            matched = matched | product_matched

    if matched.any():
        inventory_df.loc[matched, "Display_Updated_At"] = updated_at
        inventory_df.loc[matched, "Stock_Source"] = "Shopline 即時"
    return inventory_df


def merge_shopline_variant_skus(shopline_stock_df, shopline_product_df):
    if shopline_stock_df.empty or shopline_product_df.empty:
        return shopline_stock_df
    if not {"Shopline_Product_ID", "Variant_ID"}.issubset(shopline_stock_df.columns):
        return shopline_stock_df

    product_cols = ["Shopline_Product_ID", "Variant_ID", "Shopline_SKU"]
    if not set(product_cols).issubset(shopline_product_df.columns):
        return shopline_stock_df

    merged_df = shopline_stock_df.merge(
        shopline_product_df[product_cols].drop_duplicates(),
        on=["Shopline_Product_ID", "Variant_ID"],
        how="left",
        suffixes=("", "_from_product"),
    )
    if "Shopline_SKU_from_product" in merged_df.columns:
        merged_df["Shopline_SKU"] = merged_df["Shopline_SKU"].where(
            merged_df["Shopline_SKU"].apply(clean_text) != "",
            merged_df["Shopline_SKU_from_product"],
        )
        merged_df = merged_df.drop(columns=["Shopline_SKU_from_product"])
    return merged_df


def fetch_shopline_stock_snapshot(product_page_limit=100, per_page=100, stock_status=None, stock_progress=None):
    all_products = []
    for page in range(1, product_page_limit + 1):
        if stock_status:
            stock_status.info(f"正在讀取 Shopline 商品清單，第 {page} 頁...")
        _, products = fetch_products(page=page, per_page=per_page)
        if not products:
            break
        all_products.extend(products)
        if len(products) < per_page:
            break

    shopline_product_df = normalize_products(all_products)
    if shopline_product_df.empty:
        return shopline_product_df, pd.DataFrame()

    unique_products = (
        shopline_product_df[["Shopline_Product_ID", "Shopline_Name"]]
        .dropna()
        .drop_duplicates()
    )
    unique_products = unique_products[unique_products["Shopline_Product_ID"].apply(clean_text) != ""]

    stock_frames = []
    total_products = len(unique_products)
    for idx, (_, shopline_product) in enumerate(unique_products.iterrows(), start=1):
        if stock_status:
            stock_status.info(f"正在讀取庫存 {idx}/{total_products}：{shopline_product['Shopline_Name']}")
        if stock_progress:
            stock_progress.progress(idx / max(total_products, 1))
        product_id = clean_text(shopline_product["Shopline_Product_ID"])
        if not product_id:
            continue
        stock_payload, _ = fetch_product_stocks(product_id)
        stock_df = normalize_stocks(product_id, shopline_product["Shopline_Name"], stock_payload)
        if not stock_df.empty:
            stock_frames.append(stock_df)

    if not stock_frames:
        return shopline_product_df, pd.DataFrame()
    shopline_stock_df = pd.concat(stock_frames, ignore_index=True)
    shopline_stock_df = merge_shopline_variant_skus(shopline_stock_df, shopline_product_df)
    return shopline_product_df, shopline_stock_df


def write_shopline_stock_to_supabase(inventory_df, shopline_stock_df, updated_at):
    if shopline_stock_df.empty:
        return {"matched": 0, "updated": 0, "failed": 0}

    synced_inventory = apply_shopline_live_stock(inventory_df, shopline_stock_df, updated_at)
    stock_rows = synced_inventory[
        synced_inventory["Stock_Source"].eq("Shopline 即時")
        & synced_inventory["Display_Stock"].notna()
    ].copy()
    if stock_rows.empty:
        return {"matched": 0, "updated": 0, "failed": 0}

    updated = 0
    failed = 0
    for _, row in stock_rows.iterrows():
        stock_value = int(pd.to_numeric(row["Display_Stock"], errors="coerce"))
        update_dict = {
            "Current_Stock": stock_value,
            "Stock_Updated_At": updated_at,
        }
        product_id = clean_text(row.get("Product_ID", ""))
        shopline_sku = clean_text(row.get("Shopline_SKU", ""))
        if product_id:
            ok = update_data_by_id("products", "Product_ID", product_id, update_dict)
        elif shopline_sku:
            ok = update_data_by_id("products", "Shopline_SKU", shopline_sku, update_dict)
        else:
            ok = False
        if ok:
            updated += 1
        else:
            failed += 1

    return {"matched": len(stock_rows), "updated": updated, "failed": failed}


# --- 1. 系統設定 ---
st.set_page_config(
    page_title="Bluebulous B2B",
    layout="wide",
    page_icon="https://raw.githubusercontent.com/Bluebulous/product-images/main/Bluebulous%20logo.jpg"
)

# --- 2. CSS 樣式 ---
st.markdown(
    """
<style>
    /* 1. 全站深色背景 */
    .stApp {
        background-color: #171717;
        color: #ffffff;
    }
    
    /* 2. Header 設定 */
    header[data-testid="stHeader"] {
        background-color: #171717;
        color: white;
    }
    
    .block-container {
        max-width: 1540px;
        padding-top: 2rem;
    }

    /* 3. 深色卡片容器 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #202020;
        border: 1px solid #3a3a3a;
        border-radius: 8px;
        padding: 18px;
    }
    
    /* 4. 深色卡片內文字 */
    div[data-testid="stVerticalBlockBorderWrapper"] p,
    div[data-testid="stVerticalBlockBorderWrapper"] h1,
    div[data-testid="stVerticalBlockBorderWrapper"] h2,
    div[data-testid="stVerticalBlockBorderWrapper"] h3,
    div[data-testid="stVerticalBlockBorderWrapper"] span,
    div[data-testid="stVerticalBlockBorderWrapper"] div,
    div[data-testid="stVerticalBlockBorderWrapper"] label,
    div[data-testid="stVerticalBlockBorderWrapper"] li {
        color: #f4f4f4 !important;
    }

    /* 5. Selectbox & Input 樣式 */
    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div, div[data-baseweb="textarea"] > div {
        background-color: #f0f2f6 !important;
        color: #000000 !important;
        border-color: #ccc !important;
    }
    div[data-baseweb="select"] div {
        color: #000000 !important;
    }
    input, textarea {
        color: #000000 !important;
    }
    
    /* 6. 按鈕樣式 (側邊欄) */
    section[data-testid="stSidebar"] button {
        background-color: transparent !important;
        color: #cccccc !important;
        border: 1px solid transparent !important;
        text-align: left !important;
        width: 100% !important;
        height: auto !important;
        padding: 5px 10px !important;
        display: block !important;
    }
    section[data-testid="stSidebar"] button:hover {
        color: #ff5000 !important;
        background-color: #2b2b2b !important;
    }
    
    /* 7. 卡片內按鈕樣式 (產品名稱 & 購物車按鈕) */
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"] {
        border: 1px solid #4a4a4a !important;
        background-color: transparent !important;
        box-shadow: none !important;        
        padding: 8px 10px !important;
        min-height: 0px !important;
        height: auto !important;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"] p {
        color: #f4f4f4 !important;
        font-weight: bold !important;
        font-size: 13px !important;
        margin: 0px !important;
        padding: 0px !important;
        line-height: 1.35 !important;
        word-break: keep-all !important;
        overflow-wrap: anywhere !important;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"]:hover {
        color: #ff5000 !important;          
        background-color: #2c2c2c !important;
        border: 1px solid #ff5000 !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"]:hover p {
        color: #ff5000 !important;
    }

    /* 主要按鈕 (ADD / CHECKOUT) */
    button[kind="primary"] {
        background-color: #ff5500 !important;
        border: none !important;
        color: white !important;
        font-weight: bold;
        box-shadow: 0px 2px 5px rgba(0,0,0,0.2) !important;
    }
    button[kind="primary"]:hover {
        background-color: #e04a00 !important;
        color: white !important;
    }
    button[kind="primary"] p {
        color: white !important; 
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="primary"] {
        min-width: 72px !important;
        padding-left: 10px !important;
        padding-right: 10px !important;
        white-space: nowrap !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="primary"] p {
        white-space: nowrap !important;
        word-break: keep-all !important;
    }
    
    /* 狀態標籤樣式 */
    .status-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: bold;
        margin-right: 5px;
        color: white !important;
    }
    .badge-logistics { background-color: #3498db; }
    .badge-payment { background-color: #27ae60; }
    .badge-pending { background-color: #e67e22; }
    .badge-done { background-color: #2c3e50; }
    .badge-unpaid { background-color: #c0392b; }

    /* === 🛒 購物車專用微調 === */
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stNumberInput"] {
        max-width: 180px !important;
        min-width: 150px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-baseweb="input"] {
        min-height: 40px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"] {
       margin-top: 2px;
    }

    /* === 桌面採購篩選欄位 === */
    div[data-testid="stTextInput"] input {
        color: #111111 !important;
        caret-color: #111111 !important;
        background-color: #f8fafc !important;
    }
    div[data-testid="stTextInput"] input::placeholder {
        color: #6b7280 !important;
        opacity: 1 !important;
    }
    div[data-baseweb="select"] > div {
        background-color: #f8fafc !important;
        color: #111111 !important;
    }

    /* === 桌面採購工作台視覺 === */
    .shop-toolbar-title {
        color: #ffffff !important;
        font-size: 19px;
        font-weight: 850;
        margin: 0 0 4px 0;
    }
    .shop-toolbar-meta {
        color: #a7a7a7 !important;
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 10px;
    }
    .product-title {
        color: #f7f7f7 !important;
        font-size: 22px;
        font-weight: 800;
        line-height: 1.25;
        margin-bottom: 8px;
        letter-spacing: 0;
    }
    .brand-pill {
        display: inline-block;
        color: #d6d6d6 !important;
        background: #2a2a2a;
        border: 1px solid #3f3f3f;
        border-radius: 6px;
        padding: 4px 9px;
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 18px;
    }
    .section-heading {
        color: #f7f7f7 !important;
        font-size: 18px;
        font-weight: 800;
        margin: 8px 0 18px 0;
    }
    .table-head {
        color: #d9d9d9 !important;
        font-size: 13px;
        font-weight: 800;
        text-transform: uppercase;
        line-height: 1.25;
        padding-bottom: 2px;
        margin-bottom: 0;
    }
    .sku-table-divider {
        height: 1px;
        background: #3d3d3d;
        margin: 4px 0 10px 0;
    }
    .sku-size {
        color: #ffffff !important;
        font-size: 15px;
        font-weight: 800;
    }
    .price-wholesale {
        color: #ff650f !important;
        font-size: 15px;
        font-weight: 800;
        white-space: nowrap;
    }
    .price-retail {
        color: #8d8d8d !important;
        font-size: 14px;
        font-weight: 700;
        white-space: nowrap;
    }
    .cart-title {
        color: #f7f7f7 !important;
        font-size: 22px;
        font-weight: 800;
        margin-bottom: 4px;
    }
    .cart-subtitle {
        color: #a6a6a6 !important;
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 14px;
    }
    .cart-total-box {
        background: #252525;
        border: 1px solid #3f3f3f;
        border-radius: 8px;
        padding: 14px 16px;
        margin: 12px 0 14px 0;
    }
    .cart-total-row {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        color: #d8d8d8 !important;
        font-size: 14px;
        font-weight: 700;
        margin-bottom: 8px;
    }
    .cart-total-row.final {
        color: #ffffff !important;
        font-size: 20px;
        font-weight: 900;
        margin-top: 10px;
        margin-bottom: 0;
        padding-top: 10px;
        border-top: 1px solid #464646;
    }
    .threshold-card {
        border-radius: 8px;
        padding: 12px 14px;
        margin: 10px 0 14px 0;
        border: 1px solid #3f3f3f;
        background: #262626;
    }
    .threshold-card.ok {
        border-color: #2f6b45;
        background: #173322;
    }
    .threshold-card.warn {
        border-color: #6b642e;
        background: #353416;
    }
    .threshold-title {
        color: #ffffff !important;
        font-size: 14px;
        font-weight: 900;
        line-height: 1.25;
        margin-bottom: 4px;
    }
    .threshold-meta {
        color: #d6d6d6 !important;
        font-size: 12px;
        font-weight: 700;
        line-height: 1.35;
    }
    .cart-item-name {
        color: #f4f4f4 !important;
        font-size: 12px;
        font-weight: 800;
        line-height: 1.18;
        letter-spacing: 0;
    }
    .cart-item-spec {
        color: #a8a8a8 !important;
        font-size: 11px;
        font-weight: 700;
        line-height: 1.2;
        margin-top: 3px;
    }
    .cart-item-price {
        color: #f7f7f7 !important;
        font-size: 14px;
        font-weight: 900;
        text-align: right;
        white-space: nowrap;
    }

    /* === 管理後台 Dashboard 視覺 === */
    .dashboard-hero {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: flex-end;
        margin-bottom: 18px;
    }
    .dashboard-title {
        color: #ffffff !important;
        font-size: 28px;
        font-weight: 900;
        line-height: 1.15;
        margin-bottom: 6px;
    }
    .dashboard-subtitle {
        color: #9ca3af !important;
        font-size: 13px;
        font-weight: 700;
    }
    .dashboard-pill {
        display: inline-flex;
        align-items: center;
        border: 1px solid #334155;
        background: #202734;
        color: #dbeafe !important;
        border-radius: 999px;
        padding: 7px 12px;
        font-size: 12px;
        font-weight: 800;
        white-space: nowrap;
    }
    .dashboard-kpi {
        background: linear-gradient(180deg, #242424 0%, #1f1f1f 100%);
        border: 1px solid #343434;
        border-radius: 10px;
        padding: 14px 15px;
        min-height: 104px;
    }
    .dashboard-kpi-label {
        color: #a7adb6 !important;
        font-size: 12px;
        font-weight: 800;
        margin-bottom: 10px;
    }
    .dashboard-kpi-value {
        color: #f8fafc !important;
        font-size: 24px;
        font-weight: 950;
        line-height: 1.05;
        margin-bottom: 8px;
    }
    .dashboard-kpi-note {
        display: inline-block;
        color: #b9f6ca !important;
        background: #163324;
        border: 1px solid #24583a;
        border-radius: 999px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 800;
    }
    .dashboard-card-title {
        color: #f8fafc !important;
        font-size: 15px;
        font-weight: 900;
        margin-bottom: 3px;
    }
    .dashboard-card-caption {
        color: #9ca3af !important;
        font-size: 12px;
        font-weight: 700;
        margin-bottom: 12px;
    }
    .rank-panel {
        border: 1px solid #343a46;
        background: #1b1f27;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 12px;
    }
    .rank-panel-title {
        color: #f8fafc !important;
        font-size: 14px;
        font-weight: 950;
        margin-bottom: 10px;
    }
    .rank-row {
        display: grid;
        grid-template-columns: 34px minmax(0, 1fr) 92px 96px;
        gap: 10px;
        align-items: center;
        border-top: 1px solid #2c313a;
        padding: 9px 0;
    }
    .rank-row:first-of-type {
        border-top: none;
    }
    .rank-badge {
        width: 24px;
        height: 24px;
        border-radius: 999px;
        background: #d9ff74;
        color: #111827 !important;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: 950;
    }
    .rank-product {
        color: #f8fafc !important;
        font-size: 13px;
        font-weight: 850;
        line-height: 1.25;
    }
    .rank-meta {
        color: #9ca3af !important;
        font-size: 11px;
        font-weight: 750;
        margin-top: 3px;
    }
    .rank-number {
        color: #e5e7eb !important;
        font-size: 12px;
        font-weight: 850;
        text-align: right;
        white-space: nowrap;
    }
    .mobile-cart-bar {
        display: none;
    }

    /* === 📱 手機版專用優化 === */
    @media only screen and (max-width: 768px) {
        .stApp {
            background:
                radial-gradient(circle at top right, rgba(217,255,116,0.08), transparent 34%),
                #151515;
        }
        .block-container {
            padding-top: 4.4rem !important;
            padding-bottom: 7.5rem !important;
            padding-left: 0.65rem !important;
            padding-right: 0.65rem !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: linear-gradient(180deg, #222222 0%, #1d1d1d 100%);
            border: 1px solid #343a46;
            border-radius: 12px;
            padding: 14px !important;
            margin-bottom: 10px;
        }
        .shop-toolbar-title {
            font-size: 18px;
        }
        .shop-toolbar-meta {
            font-size: 12px;
            line-height: 1.35;
        }
        .product-title {
            font-size: 20px;
            line-height: 1.25;
        }
        .brand-pill {
            font-size: 12px;
            margin-bottom: 12px;
        }
        .section-heading,
        .cart-title {
            font-size: 18px;
        }
        .table-head {
            font-size: 11px;
        }
        .desktop-sku-header {
            display: none !important;
        }
        .sku-section-marker {
            display: none;
        }
        .mobile-sku-card {
            display: block;
            border: 1px solid #2f3540;
            background: #1b1f27;
            border-radius: 12px;
            padding: 12px;
            margin: 10px 0 8px 0;
        }
        .mobile-sku-top {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: flex-start;
        }
        .mobile-sku-size {
            color: #f8fafc !important;
            font-size: 16px;
            font-weight: 950;
        }
        .mobile-sku-prices {
            text-align: right;
        }
        .mobile-sku-action-hint {
            color: #9ca3af !important;
            font-size: 11px;
            font-weight: 750;
            margin-top: 8px;
        }
        div[data-testid="stElementContainer"]:has(.sku-row-start) + div[data-testid="stHorizontalBlock"] {
            display: grid !important;
            grid-template-columns: 48px minmax(78px, 1fr) 92px 62px !important;
            column-gap: 7px !important;
            align-items: center !important;
            border-top: 1px solid #2f3540;
            padding-top: 10px;
            margin-top: 8px;
        }
        div[data-testid="stElementContainer"]:has(.sku-row-start) + div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            width: 100% !important;
            min-width: 0 !important;
            padding: 0 !important;
        }
        div[data-testid="stElementContainer"]:has(.sku-row-start) + div[data-testid="stHorizontalBlock"] div[data-testid="stNumberInput"] {
            min-width: 84px !important;
            max-width: 92px !important;
        }
        div[data-testid="stElementContainer"]:has(.sku-row-start) + div[data-testid="stHorizontalBlock"] button[kind="primary"] {
            min-width: 58px !important;
            padding-left: 4px !important;
            padding-right: 4px !important;
        }
        .sku-size,
        .price-wholesale,
        .price-retail {
            font-size: 12px;
        }
        .price-wholesale {
            color: #d9ff74 !important;
            font-size: 18px !important;
            font-weight: 950 !important;
            line-height: 1.15;
        }
        .price-wholesale::before {
            content: "批發價 ";
            display: block;
            color: #9ca3af !important;
            font-size: 10px;
            font-weight: 800;
            margin-bottom: 3px;
        }
        .price-retail {
            color: #aeb4bd !important;
            font-size: 15px !important;
            font-weight: 850 !important;
            line-height: 1.15;
            margin-top: 7px;
        }
        .price-retail::before {
            content: "零售價 ";
            display: block;
            color: #7d8591 !important;
            font-size: 10px;
            font-weight: 800;
            margin-bottom: 3px;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stNumberInput"] {
            min-width: 92px !important;
            max-width: 118px !important;
        }
        div[data-testid="column"] {
            padding: 0px 1px !important;
            min-width: 0px !important;
        }
        div.stButton > button {
            min-height: 44px !important;
            border-radius: 10px !important;
        }
        button[kind="primary"] {
            background-color: #d9ff74 !important;
            color: #111827 !important;
            box-shadow: none !important;
        }
        button[kind="primary"] p {
            color: #111827 !important;
            font-weight: 900 !important;
        }
        div[data-testid="stTextInput"] input,
        div[data-baseweb="select"] > div {
            min-height: 44px !important;
            border-radius: 10px !important;
        }
        p, .stMarkdown, div[data-testid="stText"] {
            font-size: 13px !important;
        }
        .threshold-card {
            border-radius: 10px;
            padding: 10px 12px;
        }
        .cart-total-box {
            border-radius: 12px;
            padding: 12px;
        }
        .cart-item-name {
            font-size: 12px;
            line-height: 1.25;
        }
        .cart-item-price {
            font-size: 12px;
        }
        .mobile-cart-bar {
            display: flex;
            position: fixed;
            left: 10px;
            right: 10px;
            bottom: 12px;
            z-index: 9999;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            background: rgba(28, 32, 40, 0.96);
            border: 1px solid #3f4654;
            border-radius: 16px;
            padding: 12px 14px;
            box-shadow: 0 12px 36px rgba(0,0,0,0.44);
            backdrop-filter: blur(10px);
        }
        .mobile-cart-main {
            color: #f8fafc !important;
            font-size: 13px;
            font-weight: 900;
            line-height: 1.2;
        }
        .mobile-cart-sub {
            color: #aeb4bd !important;
            font-size: 11px;
            font-weight: 750;
            margin-top: 3px;
        }
        .mobile-cart-total {
            color: #d9ff74 !important;
            font-size: 17px;
            font-weight: 950;
            white-space: nowrap;
        }
        .desktop-only {
            display: none !important;
        }
        .desktop-related-products {
            display: none !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.desktop-related-products) {
            display: none !important;
        }
    }
    @media only screen and (min-width: 769px) {
        .mobile-only {
            display: none !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --- 4. 頁面邏輯 ---

def main_app(user):
    if 'cart' not in st.session_state: st.session_state.cart = {}
    if 'page' not in st.session_state: st.session_state.page = 'shop'
    if 'editing_order_id' not in st.session_state: st.session_state.editing_order_id = None
    if 'editing_customer_info' not in st.session_state: st.session_state.editing_customer_info = None
    
    if 'has_logged_cart_start' not in st.session_state: st.session_state.has_logged_cart_start = False
    if 'product_picker_version' not in st.session_state: st.session_state.product_picker_version = 0

    def set_current_product(product_name):
        st.session_state.current_product_name = product_name
        st.session_state.product_picker_version += 1

    def is_mobile_client():
        try:
            user_agent = str(st.context.headers.get("user-agent", "")).lower()
        except Exception:
            user_agent = ""
        mobile_markers = ["iphone", "android", "mobile", "ipad", "ipod"]
        return any(marker in user_agent for marker in mobile_markers)

    def calculate_cart_totals(extra_discount=0):
        BRAND_RULES, _ = get_brand_rules()
        for item in st.session_state.cart.values():
            if 'brand' not in item: item['brand'] = "default"
            if 'wholesale_price' not in item: item['wholesale_price'] = item.get('Wholesale_Price', 0)
            if 'retail_price' not in item: item['retail_price'] = item.get('Retail_Price', 0)

        brand_groups = {}
        for item in st.session_state.cart.values():
            b_name = item['brand']
            if b_name not in brand_groups:
                brand_groups[b_name] = {'items': [], 'raw_wholesale_total': 0, 'is_wholesale_qualified': False, 'is_shipping_qualified': False}
            brand_groups[b_name]['items'].append(item)
            brand_groups[b_name]['raw_wholesale_total'] += int(round(item['wholesale_price'] * item['qty']))

        is_order_free_shipping = False
        grand_total_subtotal = 0
        grand_total_tax = 0

        for b_name, data in brand_groups.items():
            safe_default = {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}
            rule = BRAND_RULES.get(b_name, BRAND_RULES.get("default", safe_default))
            d_rate = rule.get('discount_rate', 0.7)
            w_threshold = rule.get('wholesale_threshold', 10000)
            s_threshold = rule.get('shipping_threshold', 10000)

            if data['raw_wholesale_total'] >= w_threshold:
                data['is_wholesale_qualified'] = True
                brand_subtotal = data['raw_wholesale_total']
                brand_tax = int(round(brand_subtotal * TAX_RATE))
            else:
                data['is_wholesale_qualified'] = False
                brand_subtotal = 0
                brand_tax = 0
                for item in data['items']:
                    brand_subtotal += int(round(item['retail_price'] * d_rate)) * item['qty']

            if data['raw_wholesale_total'] >= s_threshold:
                data['is_shipping_qualified'] = True
                is_order_free_shipping = True

            grand_total_subtotal += brand_subtotal
            grand_total_tax += brand_tax
            for item in data['items']:
                if data['is_wholesale_qualified']:
                    item['final_unit_price'] = item['wholesale_price']
                else:
                    item['final_unit_price'] = int(round(item['retail_price'] * d_rate))
                item['final_subtotal'] = item['final_unit_price'] * item['qty']

        if is_order_free_shipping:
            shipping = 0
            shipping_msg = "符合免運資格"
        else:
            shipping = SHIPPING_FEE
            shipping_msg = f"運費 ${SHIPPING_FEE}"

        grand_total = grand_total_subtotal + grand_total_tax + shipping - extra_discount
        return {
            "brand_groups": brand_groups,
            "is_order_free_shipping": is_order_free_shipping,
            "grand_total_subtotal": int(grand_total_subtotal),
            "grand_total_tax": int(grand_total_tax),
            "shipping": int(shipping),
            "shipping_msg": shipping_msg,
            "grand_total": int(grand_total),
        }

    announcement = get_announcement()
    if announcement and announcement.strip() != "":
        st.info(f"📢 **公告：** {announcement}", icon="📢")

    try:
        df_products = get_products_data()
        
        if df_products.empty:
            st.error("無法載入產品資料，請檢查 Supabase 連線或資料表是否為空。")
            return

        if 'Wholesale_Price' in df_products.columns:
            df_products['Wholesale_Price'] = pd.to_numeric(df_products['Wholesale_Price'], errors='coerce').fillna(0)
        else:
            st.error("錯誤：找不到 'Wholesale_Price' 欄位。")
            return

        if 'Retail_Price' in df_products.columns:
            df_products['Retail_Price'] = pd.to_numeric(df_products['Retail_Price'], errors='coerce').fillna(0)
        
        allowed_brands_str = str(user.get('Allowed_Brands', ''))
        if pd.notna(allowed_brands_str) and allowed_brands_str.strip() != "" and allowed_brands_str.lower() != "nan":
            allowed_list = [b.strip() for b in allowed_brands_str.split(',') if b.strip()]
            if allowed_list and "All" not in allowed_list:
                df_products = df_products[df_products['Brand'].isin(allowed_list)]
                
    except Exception as e:
        st.error(f"處理產品資料時發生錯誤: {e}")
        return

    if df_products.empty:
        st.warning("⚠️ 目前沒有您有權限查看的產品，請聯繫管理員。")
        with st.sidebar:
            if st.button("登出", key="logout_empty", width="stretch"):
                st.session_state.clear()
                st.rerun()
        return

    if 'current_product_name' not in st.session_state:
        st.session_state.current_product_name = df_products['Name'].unique()[0]
    elif st.session_state.current_product_name not in df_products['Name'].unique():
        st.session_state.current_product_name = df_products['Name'].unique()[0]

    shop_product_scope = df_products

    with st.sidebar:
        logo_url = "https://raw.githubusercontent.com/Bluebulous/product-images/main/LOGO-white-01.png"
        st.image(logo_url, width="stretch")
        st.markdown("<h3 style='text-align: center; color: #ffffff; margin-top: -10px;'>B2B採購系統 (Beta版)</h3>", unsafe_allow_html=True)
        st.divider()
        st.markdown(f"### Hello, {user.get('Contact_Person', 'User')}")
        st.caption(f"單位: {user.get('Dealer_Name', 'Unknown')}")
        st.divider()
        
        if st.session_state.cart:
            total_qty = sum(item['qty'] for item in st.session_state.cart.values())
            total_skus = len(st.session_state.cart)
            st.info(f"🛒 購物車內有 {total_skus} 個 SKU / {total_qty} 件商品")
            st.caption("明細與結帳按鈕在右側購物車")
        else:
            st.caption("🛒 購物車是空的")
            
        st.divider()
        
        if st.button("🔄 重整產品資料", width="stretch"):
            st.cache_data.clear()
            st.toast("資料已更新！正在重新載入...", icon="🔄")
            time.sleep(1)
            st.rerun()

        if st.button("開始訂購", width="stretch"):
            st.session_state.page = 'shop'
            st.session_state.editing_order_id = None
            st.rerun()
        if st.button("歷史訂單", width="stretch"):
            st.session_state.page = 'history'
            st.rerun()
        if st.button("我的採購分析", width="stretch"):
            st.session_state.page = 'my_analytics'
            st.rerun()
        if st.button("個人資料", width="stretch"):
            st.session_state.page = 'profile'
            st.rerun()
        if is_admin(user):
            st.markdown("---")
            if st.button("🔧 管理員後台", width="stretch"):
                st.session_state.page = 'admin_orders'
                st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("登出", key="logout", width="stretch"):
            st.session_state.clear()
            st.rerun()

    # 0. 送出前確認頁
    if st.session_state.page == 'checkout_review':
        st.title("確認採購明細")
        if not st.session_state.cart:
            st.warning("購物車是空的，請先加入商品。")
            if st.button("返回採購", type="primary"):
                st.session_state.page = 'shop'
                st.rerun()
            return

        contact_email_input = st.session_state.get('checkout_contact_email', '')
        if not contact_email_input:
            contact_email_input = str(user.get('Contact_Email', '')).replace('nan', '')
        if not contact_email_input and "@" in str(user.get('Username', '')):
            contact_email_input = user['Username']

        totals = calculate_cart_totals(extra_discount=0)
        review_rows = []
        for item in st.session_state.cart.values():
            review_rows.append({
                "商品": item.get('name', ''),
                "規格": item.get('spec', ''),
                "品牌": item.get('brand', ''),
                "數量": int(item.get('qty', 0)),
                "單價": int(item.get('final_unit_price', 0)),
                "小計": int(item.get('final_subtotal', 0)),
            })

        with st.container(border=True):
            st.markdown("<div class='cart-title'>訂單內容</div>", unsafe_allow_html=True)
            st.caption(f"接收訂單通知 Email：{contact_email_input}")
            st.dataframe(
                pd.DataFrame(review_rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "數量": st.column_config.NumberColumn("數量", format="%d"),
                    "單價": st.column_config.NumberColumn("單價", format="$%d"),
                    "小計": st.column_config.NumberColumn("小計", format="$%d"),
                }
            )

        with st.container(border=True):
            summary_rows = [
                ("小計 Subtotal", f"${totals['grand_total_subtotal']:,}"),
                ("稅金 Tax", f"${totals['grand_total_tax']:,}"),
                ("運費 Shipping", totals['shipping_msg']),
            ]
            summary_html = "<div class='cart-total-box'>"
            for label, value in summary_rows:
                summary_html += f"<div class='cart-total-row'><span>{escape_html(label)}</span><span>{escape_html(value)}</span></div>"
            summary_html += f"<div class='cart-total-row final'><span>總計含稅</span><span>${totals['grand_total']:,}</span></div></div>"
            st.markdown(summary_html, unsafe_allow_html=True)

        back_col, submit_col = st.columns([1, 2])
        with back_col:
            if st.button("返回修改", width="stretch"):
                st.session_state.page = 'shop'
                st.rerun()
        with submit_col:
            if st.button("確認送出訂單", type="primary", width="stretch"):
                order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                c_name = user.get('Dealer_Name', '')
                c_email = contact_email_input
                c_phone = user.get('Phone', '')

                c_name = "" if pd.isna(c_name) else str(c_name)
                c_email = "" if pd.isna(c_email) else str(c_email)
                c_phone = "" if pd.isna(c_phone) else str(c_phone)

                try:
                    if c_email != str(user.get('Contact_Email', '')):
                        if update_data_by_id("users", "Username", user['Username'], {"Contact_Email": c_email}):
                            st.session_state['user']['Contact_Email'] = c_email
                except: pass

                final_cart_data = st.session_state.cart.copy()
                order_data = {
                    "Order_ID": order_id,
                    "Order_Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Customer_Name": c_name,
                    "Email": c_email,
                    "Phone": c_phone,
                    "Items_Json": final_cart_data,
                    "Subtotal": int(totals['grand_total_subtotal']),
                    "Tax": int(totals['grand_total_tax']),
                    "Shipping": int(totals['shipping']),
                    "Total": int(totals['grand_total']),
                    "Status": "待處理",
                    "Extra_Discount": 0,
                    "Tracking_Number": "",
                    "Admin_Note": ""
                }

                try:
                    if insert_data("orders", order_data):
                        log_system_event(user, "Checkout", f"Order ID: {order_id}")
                        st.success(f"訂單 {order_id} 已送出!")
                        with st.spinner("正在寄送確認信..."):
                            if send_order_email(order_data, final_cart_data):
                                st.toast("📧 確認信已寄出！", icon="✅")
                            else:
                                st.warning("訂單已成立，但信件寄送失敗")
                        st.session_state.cart = {}
                        st.session_state.has_logged_cart_start = False
                        st.session_state.checkout_contact_email = ""
                        time.sleep(1)
                        st.session_state.page = 'shop'
                        st.rerun()
                    else:
                        st.error("新增訂單至資料庫失敗")
                except Exception as e:
                    st.error(f"訂單處理失敗: {e}")
        return

    # 1. 歷史訂單頁
    if st.session_state.page == 'history':
        st.title("歷史訂單")
        with st.container(border=True):
            try:
                orders = get_data("orders")
                if orders.empty:
                    st.info("目前沒有訂單紀錄")
                else:
                    if 'Tracking_Number' not in orders.columns: orders['Tracking_Number'] = ""
                    if 'Admin_Note' not in orders.columns: orders['Admin_Note'] = ""
                    if 'Extra_Discount' not in orders.columns: orders['Extra_Discount'] = 0 
                    orders['Extra_Discount'] = pd.to_numeric(orders['Extra_Discount'], errors='coerce').fillna(0).astype(int)

                    match_email = orders['Email'].astype(str).str.strip() == str(user.get('Username', '')).strip()
                    match_dealer = orders['Customer_Name'].astype(str).str.strip() == str(user.get('Dealer_Name', '')).strip()
                    
                    my_orders = orders[match_email | match_dealer].sort_values("Order_Time", ascending=False)
                    
                    if not my_orders.empty:
                        for index, row in my_orders.iterrows():
                            status_str = str(row['Status'])
                            status_icon = ""
                            if "已完成" in status_str: status_icon = "✅"
                            elif "已出貨" in status_str: status_icon = "🚚"
                            elif "處理中" in status_str: status_icon = "⏳"
                            if "未付款" in status_str: status_icon += "🔴"
                            elif "已付款" in status_str: status_icon += "💰"

                            expander_title = f"{status_icon} {status_str} | {row['Order_Time']} | ${row['Total']}"
                            with st.expander(expander_title):
                                st.markdown(f"### 狀態: {display_status_badges(row['Status'])}", unsafe_allow_html=True)
                                st.divider()
                                c1, c2 = st.columns([1, 1])
                                with c1:
                                    st.markdown(f"**訂單編號:** {row['Order_ID']}")
                                    st.markdown(f"**總金額:** ${row['Total']}")
                                    extra_disc = int(row.get('Extra_Discount', 0))
                                    if extra_disc != 0:
                                        sign = "-" if extra_disc > 0 else "+"
                                        color = "green" if extra_disc > 0 else "red"
                                        st.markdown(f"<span style='color:{color};'>**🎁 額外調整:** {sign}${abs(extra_disc)}</span>", unsafe_allow_html=True)
                                    if pd.notna(row['Tracking_Number']) and str(row['Tracking_Number']).strip() != "":
                                        st.info(f"📦 **物流單號:** {row['Tracking_Number']}")
                                    if pd.notna(row['Admin_Note']) and str(row['Admin_Note']).strip() != "":
                                        st.warning(f"📝 **賣家備註:** {row['Admin_Note']}")
                                with c2:
                                    st.markdown("**訂購內容:**")
                                    try:
                                        items = row['Items_Json']
                                        if isinstance(items, str):
                                            items = json.loads(items)
                                        for item in items.values():
                                            st.text(f"• {item['name']} ({item['spec']}) x{item['qty']}")
                                    except:
                                        st.error("內容讀取失敗")
                    else:
                        st.info("目前沒有您的訂單紀錄")
            except Exception as e:
                st.error(f"讀取失敗: {e}")
        return

    # 1b. 我的採購分析頁
    if st.session_state.page == 'my_analytics':
        st.markdown(
            """
            <div class="dashboard-hero">
                <div>
                    <div class="dashboard-title">我的採購分析</div>
                    <div class="dashboard-subtitle">只顯示本通路自己的採購紀錄與常購商品</div>
                </div>
                <div class="dashboard-pill">Private Dealer View</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        try:
            orders = get_data("orders")
            if orders.empty:
                st.info("目前沒有採購紀錄可分析")
                return

            if 'Extra_Discount' not in orders.columns: orders['Extra_Discount'] = 0
            orders['Order_Date'] = pd.to_datetime(orders['Order_Time'], errors='coerce')
            orders = orders.dropna(subset=['Order_Date']).copy()
            orders['Total'] = pd.to_numeric(orders['Total'], errors='coerce').fillna(0)
            orders['Month'] = orders['Order_Date'].dt.strftime('%Y-%m')

            match_email = orders['Email'].astype(str).str.strip() == str(user.get('Username', '')).strip()
            match_dealer = orders['Customer_Name'].astype(str).str.strip() == str(user.get('Dealer_Name', '')).strip()
            my_orders = orders[match_email | match_dealer].sort_values("Order_Date", ascending=False).copy()

            if my_orders.empty:
                st.info("目前沒有您的採購紀錄")
                return

            df_prods = get_products_data()
            prod_cat_map = {}
            if not df_prods.empty and 'Name' in df_prods.columns and 'Category' in df_prods.columns:
                prod_cat_map = dict(zip(df_prods['Name'], df_prods['Category']))

            my_items_list = []
            for _, row in my_orders.iterrows():
                try:
                    items = row['Items_Json']
                    if isinstance(items, str):
                        items = json.loads(items)
                    for item in items.values():
                        product_name = item.get('name', 'Unknown')
                        my_items_list.append({
                            'Order_ID': row['Order_ID'],
                            'Order_Date': row['Order_Date'],
                            'Month': row['Order_Date'].strftime('%Y-%m'),
                            'Brand': item.get('brand', 'Unknown'),
                            'Product': product_name,
                            'Category': item.get('category', prod_cat_map.get(product_name, 'Unknown')),
                            'Qty': int(item.get('qty', 0)),
                            'Subtotal': int(item.get('final_subtotal', 0)),
                        })
                except: pass

            my_items = pd.DataFrame(my_items_list)

            total_spent = int(my_orders['Total'].sum())
            total_order_count = len(my_orders)
            last_order_date = my_orders['Order_Date'].max().strftime('%Y-%m-%d')
            total_qty = int(my_items['Qty'].sum()) if not my_items.empty else 0

            kpi_items = [
                ("累積採購金額", f"${total_spent:,}", "Total"),
                ("累積訂單數", f"{total_order_count}", "Orders"),
                ("最近一次採購", last_order_date, "Latest"),
                ("累積採購件數", f"{total_qty:,}", "Units"),
            ]
            kpi_cols = st.columns(4)
            for col, (label, value, note) in zip(kpi_cols, kpi_items):
                with col:
                    st.markdown(
                        f"""
                        <div class="dashboard-kpi">
                            <div class="dashboard-kpi-label">{escape_html(label)}</div>
                            <div class="dashboard-kpi-value">{escape_html(value)}</div>
                            <div class="dashboard-kpi-note">{escape_html(note)}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            st.divider()

            if my_items.empty:
                st.info("目前訂單中沒有可分析的商品明細")
                return

            chart_col1, chart_col2 = st.columns([1.25, 1])
            with chart_col1:
                with st.container(border=True):
                    st.markdown("<div class='dashboard-card-title'>每月採購趨勢</div><div class='dashboard-card-caption'>回顧每月採購金額與件數，掌握補貨節奏</div>", unsafe_allow_html=True)
                    monthly = my_items.groupby('Month', as_index=False).agg(
                        Amount=('Subtotal', 'sum'),
                        Qty=('Qty', 'sum')
                    )
                    base = alt.Chart(monthly).encode(x=alt.X('Month:N', title='月份'))
                    bar = base.mark_bar(color="#d9ff74", cornerRadiusTopLeft=8, cornerRadiusTopRight=8).encode(
                        y=alt.Y('Amount:Q', title='採購金額'),
                        tooltip=[
                            alt.Tooltip('Month:N', title='月份'),
                            alt.Tooltip('Amount:Q', title='採購金額', format=','),
                            alt.Tooltip('Qty:Q', title='採購件數')
                        ]
                    )
                    line = base.mark_line(point=True, color="#6ee7f9", strokeWidth=3).encode(
                        y=alt.Y('Qty:Q', title='件數')
                    )
                    trend_chart = (bar + line).resolve_scale(y='independent').properties(height=320)
                    st.altair_chart(
                        trend_chart.configure(background="transparent").configure_view(strokeWidth=0),
                        width="stretch"
                    )

            with chart_col2:
                with st.container(border=True):
                    st.markdown("<div class='dashboard-card-title'>項目採購占比</div><div class='dashboard-card-caption'>看自己主要採購哪些商品類型</div>", unsafe_allow_html=True)
                    cat_share = my_items.groupby('Category', as_index=False).agg(
                        Amount=('Subtotal', 'sum'),
                        Qty=('Qty', 'sum')
                    )
                    pie = alt.Chart(cat_share).mark_arc(innerRadius=55, outerRadius=115).encode(
                        theta=alt.Theta('Amount:Q'),
                        color=alt.Color('Category:N', title='項目', scale=alt.Scale(scheme='set2')),
                        tooltip=[
                            alt.Tooltip('Category:N', title='項目'),
                            alt.Tooltip('Amount:Q', title='採購金額', format=','),
                            alt.Tooltip('Qty:Q', title='件數')
                        ]
                    ).properties(height=320).configure(background="transparent")
                    st.altair_chart(pie, width="stretch")

            st.divider()

            top_col1, top_col2 = st.columns(2)
            top_products = my_items.groupby(['Product', 'Brand', 'Category'], as_index=False).agg(
                Qty=('Qty', 'sum'),
                Amount=('Subtotal', 'sum')
            )
            with top_col1:
                with st.container(border=True):
                    st.markdown("<div class='dashboard-card-title'>常購商品 TOP 10 / 件數</div><div class='dashboard-card-caption'>適合用來快速補貨</div>", unsafe_allow_html=True)
                    top_by_qty = top_products.sort_values('Qty', ascending=False).head(10)
                    st.dataframe(
                        top_by_qty,
                        column_config={
                            "Product": st.column_config.TextColumn("商品"),
                            "Brand": st.column_config.TextColumn("品牌"),
                            "Category": st.column_config.TextColumn("項目"),
                            "Qty": st.column_config.NumberColumn("件數", format="%d"),
                            "Amount": st.column_config.NumberColumn("金額", format="$%d"),
                        },
                        width="stretch",
                        hide_index=True
                    )
            with top_col2:
                with st.container(border=True):
                    st.markdown("<div class='dashboard-card-title'>熱銷商品 TOP 10 / 金額</div><div class='dashboard-card-caption'>看自己採購金額最高的商品</div>", unsafe_allow_html=True)
                    top_by_amount = top_products.sort_values('Amount', ascending=False).head(10)
                    st.dataframe(
                        top_by_amount,
                        column_config={
                            "Product": st.column_config.TextColumn("商品"),
                            "Brand": st.column_config.TextColumn("品牌"),
                            "Category": st.column_config.TextColumn("項目"),
                            "Qty": st.column_config.NumberColumn("件數", format="%d"),
                            "Amount": st.column_config.NumberColumn("金額", format="$%d"),
                        },
                        width="stretch",
                        hide_index=True
                    )

            st.divider()

            with st.container(border=True):
                st.markdown("<div class='dashboard-card-title'>最近採購紀錄</div><div class='dashboard-card-caption'>最近 10 筆訂單摘要</div>", unsafe_allow_html=True)
                recent_orders = my_orders[['Order_Time', 'Order_ID', 'Status', 'Total']].head(10).copy()
                recent_orders['Order_Time'] = pd.to_datetime(recent_orders['Order_Time'], errors='coerce').dt.strftime('%Y-%m-%d')
                st.dataframe(
                    recent_orders,
                    column_config={
                        "Order_Time": st.column_config.TextColumn("採購日期"),
                        "Order_ID": st.column_config.TextColumn("訂單編號"),
                        "Status": st.column_config.TextColumn("狀態"),
                        "Total": st.column_config.NumberColumn("金額", format="$%d"),
                    },
                    width="stretch",
                    hide_index=True
                )

        except Exception as e:
            st.error(f"採購分析載入失敗: {e}")
        return

    # 2. 個人資料頁
    if st.session_state.page == 'profile':
        st.title("個人資料")
        with st.container(border=True):
            st.markdown(f"**單位:** {user.get('Dealer_Name', '')}")
            st.markdown(f"**聯絡人:** {user.get('Contact_Person', '')}")
            st.markdown(f"**登入帳號:** {user.get('Username', '')}")
            st.markdown(f"**電話:** {user.get('Phone', '')}")
            st.markdown(f"**地址:** {user.get('Address', '')}")
            st.divider()
            
            st.subheader("📬 通知設定")
            with st.form("update_email_form"):
                current_contact_email = str(user.get('Contact_Email', '')).replace('nan', '')
                new_contact_email = st.text_input("接收訂單通知的 Email", value=current_contact_email, help="我們會將訂單確認信寄到這個信箱")
                
                if st.form_submit_button("更新 Email 設定", type="primary"):
                    try:
                        if update_data_by_id("users", "Username", user['Username'], {"Contact_Email": new_contact_email}):
                            st.session_state['user']['Contact_Email'] = new_contact_email
                            st.success("✅ Email 設定已更新！")
                            time.sleep(1)
                            st.rerun()
                    except Exception as e:
                        st.error(f"更新失敗: {e}")

        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.subheader("🔒 修改密碼")
            with st.form("change_password_form"):
                current_pwd = st.text_input("目前密碼", type="password")
                new_pwd = st.text_input("新密碼", type="password")
                confirm_pwd = st.text_input("確認新密碼", type="password")
                if st.form_submit_button("更新密碼", type="primary", width="stretch"):
                    if not verify_password(user.get('Password', ''), current_pwd):
                        st.error("❌ 目前密碼輸入錯誤")
                    elif new_pwd != confirm_pwd:
                        st.error("❌ 兩次新密碼輸入不一致")
                    elif not new_pwd:
                        st.error("❌ 新密碼不得為空")
                    else:
                        try:
                            hashed_password = hash_password(new_pwd)
                            if update_data_by_id("users", "Username", user['Username'], {"Password": hashed_password}):
                                st.session_state['user']['Password'] = hashed_password
                                st.success("✅ 密碼修改成功！")
                        except Exception as e: st.error(f"❌ 更新失敗: {e}")
        return

    # 4. 管理員後台
    if st.session_state.page == 'admin_orders':
        if not is_admin(user):
            st.warning("您沒有權限訪問此頁面")
            st.session_state.page = 'shop'
            st.rerun()
            return

        st.title("🔧 管理員後台")
        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["📦 訂單管理", "⚙️ 品牌門檻設定", "👥 用戶權限管理", "📊 銷售數據中心", "📦 庫存監測", "📢 公告管理", "🕵️‍♂️ 足跡追蹤"])
        
        with tab1:
            with st.container(border=True):
                try:
                    orders = get_data("orders")
                    if orders.empty:
                        st.info("目前無任何訂單")
                    else:
                        if 'Tracking_Number' not in orders.columns: orders['Tracking_Number'] = ""
                        if 'Admin_Note' not in orders.columns: orders['Admin_Note'] = ""
                        if 'Extra_Discount' not in orders.columns: orders['Extra_Discount'] = 0
                        if 'Phone' not in orders.columns: orders['Phone'] = ""
                        if 'Address' not in orders.columns: orders['Address'] = ""
                        if 'Shipping_Address' not in orders.columns: orders['Shipping_Address'] = ""
                        if 'Recipient_Name' not in orders.columns: orders['Recipient_Name'] = ""
                        orders['Extra_Discount'] = orders['Extra_Discount'].fillna(0).astype(int)

                        user_contact_lookup = {}
                        try:
                            users_for_contacts = get_data("users")
                            if not users_for_contacts.empty:
                                for _, user_row in users_for_contacts.iterrows():
                                    contact_info = {
                                        "Phone": clean_text(user_row.get("Phone", "")),
                                        "Address": clean_text(user_row.get("Address", "")),
                                        "Contact_Person": clean_text(user_row.get("Contact_Person", "")),
                                    }
                                    username_key = clean_text(user_row.get("Username", "")).strip()
                                    dealer_key = clean_text(user_row.get("Dealer_Name", "")).strip()
                                    if username_key:
                                        user_contact_lookup[("email", username_key)] = contact_info
                                    if dealer_key:
                                        user_contact_lookup[("dealer", dealer_key)] = contact_info
                        except Exception:
                            user_contact_lookup = {}

                        all_orders = orders.sort_values("Order_Time", ascending=False)
                        st.markdown(f"共 {len(all_orders)} 筆訂單")
                        
                        for index, row in all_orders.iterrows():
                            status_str = str(row['Status'])
                            status_icon = ""
                            if "已完成" in status_str: status_icon += "✅"
                            elif "已出貨" in status_str: status_icon += "🚚"
                            elif "處理中" in status_str: status_icon += "⏳"
                            if "未付款" in status_str: status_icon += "🔴"
                            elif "已付款" in status_str: status_icon += "💰"

                            status_badges = display_status_badges(row['Status'])
                            order_time = pd.to_datetime(row.get('Order_Time', ''), errors='coerce')
                            if pd.notna(order_time):
                                order_date_text = order_time.strftime("%Y-%m-%d")
                                order_time_text = order_time.strftime("%Y-%m-%d %H:%M")
                            else:
                                order_date_text = str(row.get('Order_Time', ''))
                                order_time_text = str(row.get('Order_Time', ''))
                            expander_title = f"{status_icon} {status_str} | {order_date_text} | {row['Customer_Name']} (${row['Total']})"
                            matched_contact = user_contact_lookup.get(("email", clean_text(row.get("Email", "")).strip()), {})
                            if not matched_contact:
                                matched_contact = user_contact_lookup.get(("dealer", clean_text(row.get("Customer_Name", "")).strip()), {})

                            recipient_name = clean_text(row.get("Recipient_Name", "")) or clean_text(row.get("Customer_Name", ""))
                            recipient_phone = clean_text(row.get("Phone", "")) or matched_contact.get("Phone", "")
                            recipient_address = (
                                clean_text(row.get("Address", ""))
                                or clean_text(row.get("Shipping_Address", ""))
                                or matched_contact.get("Address", "")
                            )
                            contact_person = matched_contact.get("Contact_Person", "")
                            
                            with st.expander(expander_title):
                                st.markdown(f"### 目前狀態: {status_badges}", unsafe_allow_html=True)
                                
                                c1, c2, c3 = st.columns([1.5, 2, 1])
                                with c1:
                                    st.markdown(f"**訂單編號:** `{row['Order_ID']}`")
                                    st.markdown(f"**採購日期:** {order_time_text}")
                                    st.markdown(f"**客戶:** {row['Customer_Name']}")
                                    st.markdown(f"**Email:** {row['Email']}")
                                    st.divider()
                                    st.markdown("**收件資訊**")
                                    st.markdown(f"**收件姓名:** {recipient_name or '-'}")
                                    if contact_person and contact_person != recipient_name:
                                        st.markdown(f"**聯絡人:** {contact_person}")
                                    st.markdown(f"**聯絡電話:** {recipient_phone or '-'}")
                                    st.markdown(f"**收件地址:** {recipient_address or '-'}")
                                with c2:
                                    st.markdown("**訂購內容:**")
                                    try:
                                        items = row['Items_Json']
                                        if isinstance(items, str):
                                            items = json.loads(items)
                                        for item in items.values():
                                            st.text(f"• {item['name']} ({item['spec']}) x{item['qty']}")
                                    except: st.error("JSON 解析失敗")
                                with c3:
                                    st.markdown(f"**小計:** ${row['Subtotal']}")
                                    st.markdown(f"**稅金:** ${row['Tax']}")
                                    st.markdown(f"**運費:** ${row['Shipping']}")
                                    extra_disc_show = int(row.get('Extra_Discount', 0))
                                    if extra_disc_show != 0:
                                        sign = "-" if extra_disc_show > 0 else "+"
                                        color = "green" if extra_disc_show > 0 else "red"
                                        st.markdown(f"<span style='color:{color}'>**調整:** {sign}${abs(extra_disc_show)}</span>", unsafe_allow_html=True)
                                    st.markdown(f"### Total: ${row['Total']}")
                                    
                                    if st.button("✏️ 修改內容 (進入購物車)", key=f"admin_edit_{row['Order_ID']}", type="primary"):
                                        items_to_cart = row['Items_Json']
                                        if isinstance(items_to_cart, str): items_to_cart = json.loads(items_to_cart)
                                        st.session_state.cart = items_to_cart
                                        st.session_state.editing_order_id = row['Order_ID']
                                        
                                        # 🛡️ 濾網一：從後台抓取客戶資料時，強制過濾掉所有的 NaN，替換成安全的空字串
                                        st.session_state.editing_customer_info = {
                                            "Customer_Name": clean_text(row.get('Customer_Name', "")),
                                            "Email": clean_text(row.get('Email', "")),
                                            "Phone": recipient_phone,
                                            "Extra_Discount": int(row.get('Extra_Discount', 0)) if pd.notna(row.get('Extra_Discount', 0)) else 0
                                        }
                                        st.session_state.page = 'shop'
                                        st.rerun()

                                st.divider()
                                st.markdown("#### 📝 訂單資訊 (物流/備註/折扣)")
                                track_key = f"track_{row['Order_ID']}"
                                note_key = f"note_{row['Order_ID']}"
                                disc_key = f"disc_{row['Order_ID']}"
                                logi_key = f"logi_{row['Order_ID']}"
                                pay_key = f"pay_{row['Order_ID']}"
                                
                                current_status = str(row['Status'])
                                default_logi_idx = 0
                                if "已完成" in current_status: default_logi_idx = 4
                                elif "已部分出貨" in current_status: default_logi_idx = 3
                                elif "已出貨" in current_status: default_logi_idx = 2
                                elif "處理中" in current_status: default_logi_idx = 1
                                
                                default_pay_idx = 1 if "已付款" in current_status else 0

                                with st.form(key=f"order_update_form_{row['Order_ID']}"):
                                    col_s1, col_s2 = st.columns(2)
                                    with col_s1:
                                        new_logistics = st.selectbox("物流狀態", ["待處理", "處理中", "已出貨", "已部分出貨", "已完成"], index=default_logi_idx, key=logi_key)
                                    with col_s2:
                                        new_payment = st.selectbox("金流狀態", ["未付款", "已付款"], index=default_pay_idx, key=pay_key)

                                    ic1, ic2, ic3 = st.columns([2, 3, 1.5], vertical_alignment="bottom")
                                    new_track = ic1.text_input("物流單號", value=str(row.get('Tracking_Number', '')).replace('nan','').replace('None',''), key=track_key)
                                    new_note = ic2.text_area("備註 (買家可見)", value=str(row.get('Admin_Note', '')).replace('nan','').replace('None',''), key=note_key, height=100)
                                    new_discount = ic3.number_input("額外折扣/調整 (+扣款, -加價)", value=int(row.get('Extra_Discount', 0)), key=disc_key)
                                    
                                    act_c1, act_c2 = st.columns([3, 1])
                                    with act_c1:
                                        btn_update = st.form_submit_button("💾 更新訂單並通知客戶", type="primary", width="stretch")
                                    with act_c2:
                                        btn_delete = st.form_submit_button("🗑️ 刪除訂單", width="stretch", help="注意：刪除後無法復原！")

                                    if btn_update:
                                        try:
                                            final_status_list = [new_logistics, new_payment]
                                            if new_logistics == "已完成" and new_payment == "未付款":
                                                st.warning("提醒：您將訂單設為「已完成」但「未付款」")
                                            final_status_str = ", ".join(final_status_list)
    
                                            org_sub = int(pd.to_numeric(row.get('Subtotal', 0), errors='coerce'))
                                            org_tax = int(pd.to_numeric(row.get('Tax', 0), errors='coerce'))
                                            org_ship = int(pd.to_numeric(row.get('Shipping', 0), errors='coerce'))
                                            new_total = org_sub + org_tax + org_ship - int(new_discount)
                                            
                                            update_dict = {
                                                'Status': final_status_str,
                                                'Tracking_Number': str(new_track),
                                                'Admin_Note': str(new_note),
                                                'Extra_Discount': int(new_discount),
                                                'Total': new_total
                                            }
                                            
                                            if update_data_by_id("orders", "Order_ID", row['Order_ID'], update_dict):
                                                st.success(f"訂單已更新！狀態：[{final_status_str}]")
                                                o_data = {
                                                    "Order_ID": row['Order_ID'], "Customer_Name": row['Customer_Name'],
                                                    "Email": row['Email'], "Status": final_status_str,
                                                    "Total": new_total, "Tracking_Number": new_track, "Admin_Note": new_note,
                                                    "Extra_Discount": new_discount
                                                }
                                                
                                                c_items = row['Items_Json']
                                                if isinstance(c_items, str): c_items = json.loads(c_items)
                                                
                                                with st.spinner("正在寄送通知信..."):
                                                    send_order_email(o_data, c_items, is_update=True)
                                                    st.toast("信件已寄出！", icon="📧")
                                                time.sleep(1)
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"更新失敗: {e}")
                                            
                                    if btn_delete:
                                        try:
                                            if delete_data_by_id("orders", "Order_ID", row['Order_ID']):
                                                log_system_event(user, "Delete Order", f"Deleted Order ID: {row['Order_ID']}")
                                                st.success(f"訂單 {row['Order_ID']} 已成功刪除！")
                                                time.sleep(1)
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"刪除失敗: {e}")

                except Exception as e: st.error(f"讀取失敗: {e}")

        with tab2:
            st.subheader("設定各品牌門檻與折扣")
            st.info("💡 Wholesale_Threshold: 批發門檻 | Shipping_Threshold: 免運門檻 | Discount: 零售折扣")
            _, df_rules = get_brand_rules()
            if not df_rules.empty:
                edited_df = st.data_editor(
                    df_rules, num_rows="dynamic",
                    column_config={
                        "Brand": st.column_config.TextColumn("品牌", required=True),
                        "Wholesale_Threshold": st.column_config.NumberColumn("批發門檻", min_value=0, format="$%d"),
                        "Shipping_Threshold": st.column_config.NumberColumn("免運門檻", min_value=0, format="$%d"),
                        "Discount": st.column_config.NumberColumn("折扣 (0.1~1.0)", min_value=0.1, max_value=1.0, step=0.05)
                    }, width="stretch", key="brand_rules_editor"
                )
                if st.button("💾 儲存設定", type="primary"):
                    try:
                        if save_brand_rules(edited_df, df_rules):
                            st.success("設定已更新！")
                            get_brand_rules.clear()
                            time.sleep(1)
                            st.rerun()
                    except Exception as e: st.error(f"儲存失敗: {e}")
            else:
                st.warning("目前 BrandRules 資料表為空。")
        
        with tab3:
            st.subheader("👥 用戶權限管理")
            try:
                all_brands_list = sorted(get_products_data()['Brand'].dropna().unique().tolist())
            except:
                all_brands_list = []

            try:
                users_df = get_data("users") 
                if users_df.empty:
                    st.warning("目前 Users 資料表為空。")
                else:
                    required_cols = ['Username', 'Dealer_Name']
                    missing_cols = [c for c in required_cols if c not in users_df.columns]
                    
                    if missing_cols:
                        st.error(f"❌ 資料表缺少必要欄位: {missing_cols}")
                    else:
                        if 'Allowed_Brands' not in users_df.columns: users_df['Allowed_Brands'] = ""
                        if 'Contact_Email' not in users_df.columns: users_df['Contact_Email'] = ""
                        
                        users_df['Allowed_Brands'] = users_df['Allowed_Brands'].astype(str).replace('nan', '')
                        users_df['Contact_Email'] = users_df['Contact_Email'].astype(str).replace('nan', '')

                        st.markdown("##### 目前權限總覽")
                        st.dataframe(
                            users_df[['Username', 'Dealer_Name', 'Contact_Email', 'Allowed_Brands']], 
                            width="stretch", 
                            hide_index=True
                        )
                        
                        st.divider()
                        st.markdown("##### ✏️ 修改權限")
                        
                        c_edit_1, c_edit_2 = st.columns([1, 2])
                        
                        with c_edit_1:
                            target_user = st.selectbox("選擇要修改的用戶", users_df['Username'].unique())
                            current_row = users_df[users_df['Username'] == target_user].iloc[0]
                            admin_edit_email = st.text_input("聯絡 Email", value=str(current_row['Contact_Email']).replace('nan',''))

                        with c_edit_2:
                            current_setting = str(current_row['Allowed_Brands'])
                            is_all = (current_setting == "" or "all" in current_setting.lower() or current_setting == "nan")
                            default_selected = []
                            if not is_all:
                                saved_list = [x.strip() for x in current_setting.split(',')]
                                default_selected = [x for x in saved_list if x in all_brands_list]

                            allow_all = st.checkbox("✅ 開放所有品牌權限 (All)", value=is_all)
                            if not allow_all:
                                selected_brands = st.multiselect(
                                    "請勾選允許的品牌：", 
                                    options=all_brands_list,
                                    default=default_selected
                                )
                            else:
                                st.info("ℹ️ 已選擇開放所有品牌，下方選單無須選擇。")
                                selected_brands = []

                        if st.button("💾 更新該用戶設定", type="primary"):
                            try:
                                final_str = "All" if allow_all else ", ".join(selected_brands)
                                update_dict = {
                                    'Allowed_Brands': final_str,
                                    'Contact_Email': admin_edit_email
                                }
                                if update_data_by_id("users", "Username", target_user, update_dict):
                                    st.success(f"✅ 用戶 {target_user} 資料已更新！")
                                    time.sleep(1)
                                    st.rerun()
                            except Exception as e:
                                st.error(f"更新失敗: {e}")

            except Exception as e:
                st.error(f"讀取用戶資料失敗: {e}")

        with tab4:
            st.markdown(
                """
                <div class="dashboard-hero">
                    <div>
                        <div class="dashboard-title">銷售數據中心</div>
                        <div class="dashboard-subtitle">即時追蹤營收趨勢、通路價值、品牌結構與熱銷商品</div>
                    </div>
                    <div class="dashboard-pill">Modern Dashboard View</div>
                </div>
                """,
                unsafe_allow_html=True
            )
            
            try:
                orders = get_data("orders")
                if orders.empty:
                    st.warning("目前沒有訂單數據可供分析。")
                else:
                    def style_chart(chart, height=320):
                        return (
                            chart.properties(height=height)
                            .configure(background="transparent")
                            .configure_view(strokeWidth=0)
                            .configure_axis(
                                labelColor="#aeb4bd",
                                titleColor="#d8dde5",
                                gridColor="#2f343d",
                                domain=False,
                                tickColor="#3d4350",
                            )
                            .configure_legend(
                                labelColor="#d8dde5",
                                titleColor="#aeb4bd",
                                orient="bottom",
                            )
                        )

                    orders['Order_Date'] = pd.to_datetime(orders['Order_Time'], errors='coerce')
                    orders = orders.dropna(subset=['Order_Date']).copy()
                    orders['Month'] = orders['Order_Date'].dt.strftime('%Y-%m')
                    orders['Date'] = orders['Order_Date'].dt.strftime('%Y-%m-%d')
                    orders['Week_Start'] = orders['Order_Date'].dt.to_period('W-MON').apply(lambda r: r.start_time)
                    orders['Total'] = pd.to_numeric(orders['Total'], errors='coerce').fillna(0)
                    orders['Subtotal'] = pd.to_numeric(orders.get('Subtotal', 0), errors='coerce').fillna(0)
                    orders['Shipping'] = pd.to_numeric(orders.get('Shipping', 0), errors='coerce').fillna(0)
                    
                    df_prods = get_products_data()
                    prod_cat_map = {}
                    if not df_prods.empty and 'Name' in df_prods.columns and 'Category' in df_prods.columns:
                        prod_cat_map = dict(zip(df_prods['Name'], df_prods['Category']))

                    all_items_list = []
                    for _, row in orders.iterrows():
                        try:
                            items = row['Items_Json']
                            if isinstance(items, str): items = json.loads(items)
                            for item in items.values():
                                c_cat = item.get('category', prod_cat_map.get(item.get('name'), 'Unknown'))
                                all_items_list.append({
                                    'Order_ID': row['Order_ID'],
                                    'Dealer': row['Customer_Name'],
                                    'Date': row['Order_Date'].strftime('%Y-%m-%d'),
                                    'Month': row['Order_Date'].strftime('%Y-%m'),
                                    'Week_Start': row['Order_Date'].to_period('W-MON').start_time,
                                    'Brand': item.get('brand', 'Unknown'),
                                    'Product': item.get('name', 'Unknown'),
                                    'Category': c_cat,
                                    'Qty': int(item.get('qty', 0)),
                                    'Subtotal': int(item.get('final_subtotal', 0))
                                })
                        except: pass
                    
                    df_items = pd.DataFrame(all_items_list)

                    total_rev = int(orders['Total'].sum())
                    total_orders = len(orders)
                    avg_order_value = int(total_rev / total_orders) if total_orders > 0 else 0
                    active_dealers = orders['Customer_Name'].nunique()
                    total_qty = int(df_items['Qty'].sum()) if not df_items.empty and 'Qty' in df_items.columns else 0
                    
                    kpi_items = [
                        ("總營業額", f"${total_rev:,}", "Revenue"),
                        ("總訂單數", f"{total_orders}", "Orders"),
                        ("平均客單價", f"${avg_order_value:,}", "AOV"),
                        ("下單通路數", f"{active_dealers}", "Dealers"),
                        ("銷售件數", f"{total_qty:,}", "Units"),
                    ]
                    kpi_cols = st.columns(5)
                    for col, (label, value, note) in zip(kpi_cols, kpi_items):
                        with col:
                            st.markdown(
                                f"""
                                <div class="dashboard-kpi">
                                    <div class="dashboard-kpi-label">{escape_html(label)}</div>
                                    <div class="dashboard-kpi-value">{escape_html(value)}</div>
                                    <div class="dashboard-kpi-note">{escape_html(note)}</div>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )
                    
                    st.divider()

                    c_chart1, c_chart2 = st.columns(2)
                    
                    with c_chart1:
                        with st.container(border=True):
                            st.markdown("<div class='dashboard-card-title'>每週營收趨勢</div><div class='dashboard-card-caption'>以週為單位觀察營收與訂單量變化</div>", unsafe_allow_html=True)
                            weekly_sales = orders.groupby('Week_Start', as_index=False).agg(
                                Revenue=('Total', 'sum'),
                                Orders=('Order_ID', 'count')
                            )
                            weekly_base = alt.Chart(weekly_sales).encode(
                                x=alt.X('Week_Start:T', title='週起始日'),
                                y=alt.Y('Revenue:Q', title='營收'),
                                tooltip=[
                                    alt.Tooltip('Week_Start:T', title='週起始日'),
                                    alt.Tooltip('Revenue:Q', title='營收', format=','),
                                    alt.Tooltip('Orders:Q', title='訂單數')
                                ]
                            )
                            weekly_area = weekly_base.mark_area(
                                color="#38d996",
                                opacity=0.18,
                                interpolate="monotone",
                            )
                            weekly_line = weekly_base.mark_line(
                                color="#7cf0b2",
                                strokeWidth=3,
                                interpolate="monotone",
                            )
                            weekly_points = weekly_base.mark_circle(color="#d9ff74", size=55)
                            st.altair_chart(style_chart(weekly_area + weekly_line + weekly_points, 320), width="stretch")

                    with c_chart2:
                        with st.container(border=True):
                            st.markdown("<div class='dashboard-card-title'>每月營收與訂單數</div><div class='dashboard-card-caption'>同時看營收與訂單量，避免只看金額誤判</div>", unsafe_allow_html=True)
                            monthly_sales = orders.groupby('Month', as_index=False).agg(
                                Revenue=('Total', 'sum'),
                                Orders=('Order_ID', 'count')
                            )
                            monthly_base = alt.Chart(monthly_sales).encode(x=alt.X('Month:N', title='月份'))
                            monthly_bar = monthly_base.mark_bar(color="#d9ff74", cornerRadiusTopLeft=8, cornerRadiusTopRight=8).encode(
                                y=alt.Y('Revenue:Q', title='營收'),
                                tooltip=[
                                    alt.Tooltip('Month:N', title='月份'),
                                    alt.Tooltip('Revenue:Q', title='營收', format=','),
                                    alt.Tooltip('Orders:Q', title='訂單數')
                                ]
                            )
                            monthly_line = monthly_base.mark_line(point=True, color="#6ee7f9", strokeWidth=3).encode(
                                y=alt.Y('Orders:Q', title='訂單數')
                            )
                            monthly_chart = (monthly_bar + monthly_line).resolve_scale(y='independent')
                            st.altair_chart(style_chart(monthly_chart, 320), width="stretch")

                    st.divider()
                    
                    c_chart3, c_chart4 = st.columns(2)
                    
                    if not df_items.empty:
                        with c_chart3:
                            with st.container(border=True):
                                st.markdown("<div class='dashboard-card-title'>品牌營收排行</div><div class='dashboard-card-caption'>看品牌貢獻與銷售集中度</div>", unsafe_allow_html=True)
                                brand_sales_df = df_items.groupby('Brand', as_index=False).agg(
                                    Revenue=('Subtotal', 'sum'),
                                    Qty=('Qty', 'sum')
                                ).sort_values('Revenue', ascending=False).head(12)
                                brand_chart = alt.Chart(brand_sales_df).mark_bar(color="#d9ff74", cornerRadiusEnd=8).encode(
                                    x=alt.X('Revenue:Q', title='營收'),
                                    y=alt.Y('Brand:N', sort='-x', title='品牌'),
                                    tooltip=[
                                        alt.Tooltip('Brand:N', title='品牌'),
                                        alt.Tooltip('Revenue:Q', title='營收', format=','),
                                        alt.Tooltip('Qty:Q', title='件數')
                                    ]
                                )
                                st.altair_chart(style_chart(brand_chart, 360), width="stretch")

                        with c_chart4:
                            with st.container(border=True):
                                st.markdown("<div class='dashboard-card-title'>分類營收排行</div><div class='dashboard-card-caption'>辨識主要品類與品類缺口</div>", unsafe_allow_html=True)
                                cat_sales_df = df_items.groupby('Category', as_index=False).agg(
                                    Revenue=('Subtotal', 'sum'),
                                    Qty=('Qty', 'sum')
                                ).sort_values('Revenue', ascending=False).head(12)
                                cat_chart = alt.Chart(cat_sales_df).mark_bar(color="#6ee7f9", cornerRadiusEnd=8).encode(
                                    x=alt.X('Revenue:Q', title='營收'),
                                    y=alt.Y('Category:N', sort='-x', title='分類'),
                                    tooltip=[
                                        alt.Tooltip('Category:N', title='分類'),
                                        alt.Tooltip('Revenue:Q', title='營收', format=','),
                                        alt.Tooltip('Qty:Q', title='件數')
                                    ]
                                )
                                st.altair_chart(style_chart(cat_chart, 360), width="stretch")

                    st.divider()

                    if not df_items.empty:
                        d1, d2 = st.columns(2)
                        with d1:
                            with st.container(border=True):
                                st.markdown("<div class='dashboard-card-title'>通路營收貢獻 TOP 15</div><div class='dashboard-card-caption'>找出核心通路與高客單通路</div>", unsafe_allow_html=True)
                                dealer_sales = orders.groupby('Customer_Name', as_index=False).agg(
                                    Revenue=('Total', 'sum'),
                                    Orders=('Order_ID', 'count')
                                )
                                dealer_sales['AOV'] = (dealer_sales['Revenue'] / dealer_sales['Orders']).round(0).astype(int)
                                dealer_sales = dealer_sales.sort_values('Revenue', ascending=False).head(15)
                                st.dataframe(
                                    dealer_sales,
                                    column_config={
                                        "Customer_Name": st.column_config.TextColumn("通路"),
                                        "Revenue": st.column_config.NumberColumn("營收", format="$%d"),
                                        "Orders": st.column_config.NumberColumn("訂單數", format="%d"),
                                        "AOV": st.column_config.NumberColumn("平均客單", format="$%d"),
                                    },
                                    width="stretch",
                                    hide_index=True
                                )

                        with d2:
                            with st.container(border=True):
                                st.markdown("<div class='dashboard-card-title'>品牌 x 月份營收熱度</div><div class='dashboard-card-caption'>看品牌在不同月份的起伏</div>", unsafe_allow_html=True)
                                brand_month = df_items.groupby(['Month', 'Brand'], as_index=False)['Subtotal'].sum()
                                top_heat_brands = brand_month.groupby('Brand')['Subtotal'].sum().sort_values(ascending=False).head(8).index.tolist()
                                brand_month = brand_month[brand_month['Brand'].isin(top_heat_brands)]
                                heat_chart = alt.Chart(brand_month).mark_rect(cornerRadius=5).encode(
                                    x=alt.X('Month:N', title='月份'),
                                    y=alt.Y('Brand:N', title='品牌'),
                                    color=alt.Color('Subtotal:Q', title='營收', scale=alt.Scale(range=["#252a32", "#d9ff74"])),
                                    tooltip=[
                                        alt.Tooltip('Month:N', title='月份'),
                                        alt.Tooltip('Brand:N', title='品牌'),
                                        alt.Tooltip('Subtotal:Q', title='營收', format=',')
                                    ]
                                )
                                st.altair_chart(style_chart(heat_chart, 330), width="stretch")

                        st.divider()

                        with st.container(border=True):
                            st.markdown("<div class='dashboard-card-title'>各項目銷售 TOP 3</div><div class='dashboard-card-caption'>每個項目內部各自排名，適合看 NSD 胸背帶、雨衣、保健品等項目的主力商品</div>", unsafe_allow_html=True)
                            category_product_rank = df_items.groupby(['Category', 'Product', 'Brand'], as_index=False).agg(
                                Qty=('Qty', 'sum'),
                                Revenue=('Subtotal', 'sum')
                            )
                            category_product_rank = category_product_rank.sort_values(['Category', 'Revenue', 'Qty'], ascending=[True, False, False])
                            category_product_rank['Rank'] = category_product_rank.groupby('Category')['Revenue'].rank(method='first', ascending=False).astype(int)
                            category_top3 = category_product_rank[category_product_rank['Rank'] <= 3].copy()
                            category_top3 = category_top3.sort_values(['Category', 'Rank'])
                            category_cols = st.columns(3)
                            for idx, (category, group) in enumerate(category_top3.groupby('Category', sort=True)):
                                rows_html = ""
                                for _, top_item in group.iterrows():
                                    rows_html += (
                                        "<div class='rank-row'>"
                                        f"<div><span class='rank-badge'>{int(top_item['Rank'])}</span></div>"
                                        f"<div><div class='rank-product'>{escape_html(top_item['Product'])}</div>"
                                        f"<div class='rank-meta'>{escape_html(top_item['Brand'])}</div></div>"
                                        f"<div class='rank-number'>{int(top_item['Qty']):,} 件</div>"
                                        f"<div class='rank-number'>${int(top_item['Revenue']):,}</div>"
                                        "</div>"
                                    )
                                with category_cols[idx % 3]:
                                    st.markdown(
                                        f"<div class='rank-panel'><div class='rank-panel-title'>{escape_html(category)}</div>{rows_html}</div>",
                                        unsafe_allow_html=True
                                    )

                        st.divider()

                        st.markdown("##### 🧭 通路比較分析")
                        dealer_options = sorted(df_items['Dealer'].dropna().unique().tolist())
                        default_dealers = (
                            dealer_sales['Customer_Name'].head(4).tolist()
                            if 'dealer_sales' in locals() and not dealer_sales.empty
                            else dealer_options[:4]
                        )
                        selected_dealers = st.multiselect(
                            "選擇要比較的通路",
                            dealer_options,
                            default=[d for d in default_dealers if d in dealer_options],
                            help="建議選 2 到 6 間，圖表會比較清楚。"
                        )

                        if selected_dealers:
                            dealer_compare_items = df_items[df_items['Dealer'].isin(selected_dealers)].copy()
                            dealer_compare_orders = orders[orders['Customer_Name'].isin(selected_dealers)].copy()

                            dc1, dc2, dc3 = st.columns(3)
                            with dc1:
                                with st.container(border=True):
                                    st.markdown("<div class='dashboard-card-title'>通路品牌結構</div><div class='dashboard-card-caption'>比較不同通路主要賣哪些品牌</div>", unsafe_allow_html=True)
                                    dealer_brand = dealer_compare_items.groupby(['Dealer', 'Brand'], as_index=False).agg(
                                        Revenue=('Subtotal', 'sum'),
                                        Qty=('Qty', 'sum')
                                    )
                                    dealer_brand['Dealer_Total'] = dealer_brand.groupby('Dealer')['Revenue'].transform('sum')
                                    dealer_brand['Share'] = (dealer_brand['Revenue'] / dealer_brand['Dealer_Total']).fillna(0)
                                    top_compare_brands = dealer_brand.groupby('Brand')['Revenue'].sum().sort_values(ascending=False).head(8).index.tolist()
                                    dealer_brand_chart_data = dealer_brand[dealer_brand['Brand'].isin(top_compare_brands)]
                                    dealer_brand_chart = alt.Chart(dealer_brand_chart_data).mark_bar(cornerRadiusEnd=7).encode(
                                        x=alt.X('Share:Q', title='佔比', axis=alt.Axis(format='%')),
                                        y=alt.Y('Dealer:N', title='通路'),
                                        color=alt.Color('Brand:N', title='品牌', scale=alt.Scale(scheme='set2')),
                                        tooltip=[
                                            alt.Tooltip('Dealer:N', title='通路'),
                                            alt.Tooltip('Brand:N', title='品牌'),
                                            alt.Tooltip('Revenue:Q', title='營收', format=','),
                                            alt.Tooltip('Share:Q', title='佔比', format='.1%'),
                                            alt.Tooltip('Qty:Q', title='件數')
                                        ]
                                    )
                                    st.altair_chart(style_chart(dealer_brand_chart, 320), width="stretch")

                            with dc2:
                                with st.container(border=True):
                                    st.markdown("<div class='dashboard-card-title'>通路分類結構</div><div class='dashboard-card-caption'>比較不同通路偏好的商品類型</div>", unsafe_allow_html=True)
                                    dealer_cat = dealer_compare_items.groupby(['Dealer', 'Category'], as_index=False).agg(
                                        Revenue=('Subtotal', 'sum'),
                                        Qty=('Qty', 'sum')
                                    )
                                    dealer_cat['Dealer_Total'] = dealer_cat.groupby('Dealer')['Revenue'].transform('sum')
                                    dealer_cat['Share'] = (dealer_cat['Revenue'] / dealer_cat['Dealer_Total']).fillna(0)
                                    top_compare_cats = dealer_cat.groupby('Category')['Revenue'].sum().sort_values(ascending=False).head(8).index.tolist()
                                    dealer_cat_chart_data = dealer_cat[dealer_cat['Category'].isin(top_compare_cats)]
                                    dealer_cat_chart = alt.Chart(dealer_cat_chart_data).mark_bar(cornerRadiusEnd=7).encode(
                                        x=alt.X('Share:Q', title='佔比', axis=alt.Axis(format='%')),
                                        y=alt.Y('Dealer:N', title='通路'),
                                        color=alt.Color('Category:N', title='分類', scale=alt.Scale(scheme='tableau20')),
                                        tooltip=[
                                            alt.Tooltip('Dealer:N', title='通路'),
                                            alt.Tooltip('Category:N', title='分類'),
                                            alt.Tooltip('Revenue:Q', title='營收', format=','),
                                            alt.Tooltip('Share:Q', title='佔比', format='.1%'),
                                            alt.Tooltip('Qty:Q', title='件數')
                                        ]
                                    )
                                    st.altair_chart(style_chart(dealer_cat_chart, 320), width="stretch")

                            with dc3:
                                with st.container(border=True):
                                    st.markdown("<div class='dashboard-card-title'>通路規模比較</div><div class='dashboard-card-caption'>訂單數、營收與件數一起看</div>", unsafe_allow_html=True)
                                    dealer_summary = dealer_compare_orders.groupby('Customer_Name', as_index=False).agg(
                                        Revenue=('Total', 'sum'),
                                        Orders=('Order_ID', 'count')
                                    )
                                    dealer_qty = dealer_compare_items.groupby('Dealer', as_index=False)['Qty'].sum()
                                    dealer_summary = dealer_summary.merge(dealer_qty, left_on='Customer_Name', right_on='Dealer', how='left')
                                    dealer_summary['Qty'] = dealer_summary['Qty'].fillna(0).astype(int)
                                    dealer_summary['AOV'] = (dealer_summary['Revenue'] / dealer_summary['Orders']).round(0).astype(int)
                                    dealer_summary_chart = alt.Chart(dealer_summary).mark_circle(opacity=0.82).encode(
                                        x=alt.X('Orders:Q', title='訂單數'),
                                        y=alt.Y('Revenue:Q', title='營收'),
                                        size=alt.Size('Qty:Q', title='件數', scale=alt.Scale(range=[120, 900])),
                                        color=alt.Color('Customer_Name:N', title='通路', scale=alt.Scale(scheme='set2')),
                                        tooltip=[
                                            alt.Tooltip('Customer_Name:N', title='通路'),
                                            alt.Tooltip('Revenue:Q', title='營收', format=','),
                                            alt.Tooltip('Orders:Q', title='訂單數'),
                                            alt.Tooltip('Qty:Q', title='件數'),
                                            alt.Tooltip('AOV:Q', title='平均客單', format=',')
                                        ]
                                    )
                                    st.altair_chart(style_chart(dealer_summary_chart, 320), width="stretch")

                            with st.container(border=True):
                                st.markdown("<div class='dashboard-card-title'>各通路熱銷商品差異</div><div class='dashboard-card-caption'>每間通路各自列出熱銷商品，避免通路名稱在表格中重複出現</div>", unsafe_allow_html=True)
                                dealer_product = dealer_compare_items.groupby(['Dealer', 'Product', 'Brand', 'Category'], as_index=False).agg(
                                    Qty=('Qty', 'sum'),
                                    Revenue=('Subtotal', 'sum')
                                )
                                dealer_product = dealer_product.sort_values(['Dealer', 'Revenue'], ascending=[True, False])
                                dealer_product['Rank'] = dealer_product.groupby('Dealer')['Revenue'].rank(method='first', ascending=False).astype(int)
                                dealer_product_top = dealer_product[dealer_product['Rank'] <= 5].copy()
                                dealer_cols = st.columns(2)
                                for idx, (dealer, group) in enumerate(dealer_product_top.groupby('Dealer', sort=True)):
                                    rows_html = ""
                                    for _, top_item in group.iterrows():
                                        rows_html += (
                                            "<div class='rank-row'>"
                                            f"<div><span class='rank-badge'>{int(top_item['Rank'])}</span></div>"
                                            f"<div><div class='rank-product'>{escape_html(top_item['Product'])}</div>"
                                            f"<div class='rank-meta'>{escape_html(top_item['Brand'])} / {escape_html(top_item['Category'])}</div></div>"
                                            f"<div class='rank-number'>{int(top_item['Qty']):,} 件</div>"
                                            f"<div class='rank-number'>${int(top_item['Revenue']):,}</div>"
                                            "</div>"
                                        )
                                    with dealer_cols[idx % 2]:
                                        st.markdown(
                                            f"<div class='rank-panel'><div class='rank-panel-title'>{escape_html(dealer)}</div>{rows_html}</div>",
                                            unsafe_allow_html=True
                                        )
                        else:
                            st.caption("選擇至少一間通路後，就會顯示比較圖表。")

                        st.divider()
                        p1, p2 = st.columns(2)
                        top_products = df_items.groupby(['Product', 'Brand', 'Category'])[['Qty', 'Subtotal']].sum().reset_index()
                        with p1:
                            st.markdown("##### 🔥 熱銷商品 TOP 20 / 營收")
                            top_by_revenue = top_products.sort_values('Subtotal', ascending=False).head(20)
                            st.dataframe(
                                top_by_revenue,
                                column_config={
                                    "Product": st.column_config.TextColumn("商品"),
                                    "Brand": st.column_config.TextColumn("品牌"),
                                    "Category": st.column_config.TextColumn("分類"),
                                    "Subtotal": st.column_config.NumberColumn("銷售總額", format="$%d"),
                                    "Qty": st.column_config.NumberColumn("銷售數量"),
                                },
                                width="stretch",
                                hide_index=True
                            )
                        with p2:
                            st.markdown("##### 📦 熱銷商品 TOP 20 / 件數")
                            top_by_qty = top_products.sort_values('Qty', ascending=False).head(20)
                            st.dataframe(
                                top_by_qty,
                                column_config={
                                    "Product": st.column_config.TextColumn("商品"),
                                    "Brand": st.column_config.TextColumn("品牌"),
                                    "Category": st.column_config.TextColumn("分類"),
                                    "Subtotal": st.column_config.NumberColumn("銷售總額", format="$%d"),
                                    "Qty": st.column_config.NumberColumn("銷售數量"),
                                },
                                width="stretch",
                                hide_index=True
                            )
                    else:
                        st.info("尚無商品銷售細節數據")

            except Exception as e:
                st.error(f"數據分析載入失敗: {e}")

        # 第五個 Tab: 庫存監測
        with tab5:
            st.subheader("📦 庫存監測")
            st.caption("近期熱銷以最近 30 天訂單計算；即時庫存優先讀取 Supabase 商品欄位，Shopline 同步可由右側手動觸發。")

            try:
                inventory_df = df_products.copy()
                for column in [
                    "Shopline_SKU", "Shopline_Product_ID", "Current_Stock", "Shopline_Stock",
                    "Stock_Updated_At", "Restock_Qty", "Expected_Arrival_Date", "Restock_Date"
                ]:
                    if column not in inventory_df.columns:
                        inventory_df[column] = ""

                stock_col = first_populated_number_column(
                    inventory_df,
                    [
                        "Shopline_Stock", "Current_Stock", "Stock", "Inventory", "Inventory_Qty",
                        "Available_Quantity", "Quantity", "Qty", "On_Hand", "On_Hand_Qty"
                    ]
                )
                if stock_col:
                    inventory_df["Display_Stock"] = pd.to_numeric(inventory_df[stock_col], errors="coerce")
                else:
                    inventory_df["Display_Stock"] = pd.NA

                updated_col = first_existing_column(inventory_df, ["Stock_Updated_At", "Updated_At", "Last_Synced_At"])
                if updated_col:
                    inventory_df["Display_Updated_At"] = inventory_df[updated_col].apply(clean_text)
                else:
                    inventory_df["Display_Updated_At"] = ""
                inventory_df["Stock_Source"] = "Supabase"

                if "shopline_live_stock_df" in st.session_state:
                    inventory_df = apply_shopline_live_stock(
                        inventory_df,
                        st.session_state.get("shopline_live_stock_df"),
                        st.session_state.get("shopline_live_stock_updated_at", ""),
                    )

                restock_date_col = first_existing_column(inventory_df, ["Expected_Arrival_Date", "Restock_Date"])
                inventory_df["Display_Restock_Date"] = inventory_df[restock_date_col].apply(clean_text) if restock_date_col else ""
                inventory_df["Restock_Qty"] = pd.to_numeric(inventory_df.get("Restock_Qty", ""), errors="coerce")

                orders_for_inventory = get_data("orders")
                recent_sales = build_recent_sales_summary(orders_for_inventory, days=30)
                if recent_sales.empty:
                    inventory_df["Last_30D_Qty"] = 0
                    inventory_df["Last_30D_Revenue"] = 0
                else:
                    merge_key = "Product_ID" if "Product_ID" in inventory_df.columns and "Product_ID" in recent_sales.columns else "Name"
                    inventory_df[merge_key] = inventory_df[merge_key].astype(str)
                    recent_sales[merge_key] = recent_sales[merge_key].astype(str)
                    inventory_df = inventory_df.merge(
                        recent_sales[[merge_key, "Last_30D_Qty", "Last_30D_Revenue"]],
                        on=merge_key,
                        how="left"
                    )
                    inventory_df["Last_30D_Qty"] = inventory_df["Last_30D_Qty"].fillna(0).astype(int)
                    inventory_df["Last_30D_Revenue"] = inventory_df["Last_30D_Revenue"].fillna(0).astype(int)

                max_updated_at = inventory_df["Display_Updated_At"].replace("", pd.NA).dropna()
                last_update_text = max_updated_at.max() if not max_updated_at.empty else "尚未有庫存更新時間"
                hot_sku_count = int((inventory_df["Last_30D_Qty"] > 0).sum())
                low_stock_count = int((inventory_df["Display_Stock"].fillna(999999) <= 5).sum())
                no_mapping_count = int(inventory_df["Shopline_SKU"].apply(clean_text).eq("").sum())

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("近 30 天有銷售 SKU", f"{hot_sku_count:,}")
                k2.metric("低庫存 SKU", f"{low_stock_count:,}")
                k3.metric("未對應 Shopline SKU", f"{no_mapping_count:,}")
                k4.metric("庫存更新時間", last_update_text)

                st.divider()
                filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 1.4])
                with filter_col1:
                    selected_inventory_brand = st.selectbox(
                        "品牌",
                        ["全部"] + sorted(inventory_df["Brand"].dropna().astype(str).unique().tolist()),
                        key="inventory_brand_filter",
                    )
                with filter_col2:
                    selected_inventory_category = st.selectbox(
                        "類別",
                        ["全部"] + sorted(inventory_df["Category"].dropna().astype(str).unique().tolist()),
                        key="inventory_category_filter",
                    )
                with filter_col3:
                    inventory_keyword = st.text_input("搜尋品名 / SKU / 顏色 / 尺寸", key="inventory_keyword")

                filtered_inventory = inventory_df.copy()
                if selected_inventory_brand != "全部":
                    filtered_inventory = filtered_inventory[filtered_inventory["Brand"].astype(str) == selected_inventory_brand]
                if selected_inventory_category != "全部":
                    filtered_inventory = filtered_inventory[filtered_inventory["Category"].astype(str) == selected_inventory_category]
                if inventory_keyword:
                    keyword = inventory_keyword.strip().lower()
                    searchable_cols = [col for col in ["Name", "Brand", "Category", "Color", "Size", "Shopline_SKU"] if col in filtered_inventory.columns]
                    mask = pd.Series(False, index=filtered_inventory.index)
                    for col in searchable_cols:
                        mask = mask | filtered_inventory[col].astype(str).str.lower().str.contains(keyword, na=False)
                    filtered_inventory = filtered_inventory[mask]

                hot_inventory = filtered_inventory.sort_values(["Last_30D_Qty", "Last_30D_Revenue"], ascending=False)
                st.markdown("#### 近期熱銷產品與即時庫存")
                st.dataframe(
                    hot_inventory[
                        [
                            "Brand", "Category", "Name", "Color", "Size",
                            "Last_30D_Qty", "Last_30D_Revenue", "Display_Stock",
                            "Display_Updated_At", "Stock_Source", "Restock_Qty", "Display_Restock_Date"
                        ]
                    ].head(200),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Brand": st.column_config.TextColumn("品牌"),
                        "Category": st.column_config.TextColumn("類別"),
                        "Name": st.column_config.TextColumn("商品"),
                        "Color": st.column_config.TextColumn("顏色"),
                        "Size": st.column_config.TextColumn("尺寸"),
                        "Last_30D_Qty": st.column_config.NumberColumn("近 30 天銷量"),
                        "Last_30D_Revenue": st.column_config.NumberColumn("近 30 天金額", format="$%d"),
                        "Display_Stock": st.column_config.NumberColumn("即時庫存"),
                        "Display_Updated_At": st.column_config.TextColumn("庫存更新時間"),
                        "Stock_Source": st.column_config.TextColumn("來源"),
                        "Restock_Qty": st.column_config.NumberColumn("未來補貨數量"),
                        "Display_Restock_Date": st.column_config.TextColumn("預計到貨日期"),
                    },
                )

                st.divider()
                st.markdown("#### 各品牌近期熱銷 TOP 5")
                hot_by_brand = hot_inventory[hot_inventory["Last_30D_Qty"] > 0].copy()
                if hot_by_brand.empty:
                    st.info("最近 30 天尚無可分析的商品銷售資料。")
                else:
                    hot_by_brand["Rank"] = hot_by_brand.groupby("Brand")["Last_30D_Qty"].rank(method="first", ascending=False).astype(int)
                    brand_panels = st.columns(3)
                    for idx, (brand, group) in enumerate(hot_by_brand[hot_by_brand["Rank"] <= 5].groupby("Brand", sort=True)):
                        rows_html = ""
                        for _, hot_item in group.sort_values("Rank").iterrows():
                            stock_text = "未填庫存" if pd.isna(hot_item["Display_Stock"]) else f"{int(hot_item['Display_Stock']):,} 件"
                            rows_html += (
                                "<div class='rank-row'>"
                                f"<div><span class='rank-badge'>{int(hot_item['Rank'])}</span></div>"
                                f"<div><div class='rank-product'>{escape_html(hot_item['Name'])}</div>"
                                f"<div class='rank-meta'>{escape_html(hot_item['Color'])} / {escape_html(hot_item['Size'])}</div></div>"
                                f"<div class='rank-number'>{int(hot_item['Last_30D_Qty']):,} 件</div>"
                                f"<div class='rank-number'>{escape_html(stock_text)}</div>"
                                "</div>"
                            )
                        with brand_panels[idx % 3]:
                            st.markdown(
                                f"<div class='rank-panel'><div class='rank-panel-title'>{escape_html(brand)}</div>{rows_html}</div>",
                                unsafe_allow_html=True
                            )

                st.divider()
                st.markdown("#### Shopline 庫存同步")
                sync_col1, sync_col2 = st.columns([1, 2])
                with sync_col1:
                    st.caption("環境變數需要設定 SHOPLINE_API_TOKEN 與 SHOPLINE_API_BASE_URL。")
                    st.write("狀態：", "已設定 Token" if is_shopline_configured() else "尚未設定 Token")
                    st.caption("全量同步會讀取 Shopline 商品與庫存，並寫回 Supabase 的 Current_Stock 與 Stock_Updated_At。")
                    run_full_shopline_sync = st.button("🔄 同步全部 Shopline 庫存", type="primary", width="stretch")
                with sync_col2:
                    if run_full_shopline_sync:
                        try:
                            if not is_shopline_configured():
                                st.error("尚未設定 SHOPLINE_API_TOKEN")
                            else:
                                sync_status = st.empty()
                                sync_progress = st.progress(0)
                                updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                                shopline_product_df, shopline_stock_df = fetch_shopline_stock_snapshot(
                                    stock_status=sync_status,
                                    stock_progress=sync_progress,
                                )
                                if shopline_stock_df.empty:
                                    st.warning("已讀取 Shopline 商品，但沒有解析到庫存明細。需要依 Shopline 實際回傳格式調整。")
                                else:
                                    result = write_shopline_stock_to_supabase(inventory_df, shopline_stock_df, updated_at)
                                    st.session_state.shopline_live_stock_df = shopline_stock_df
                                    st.session_state.shopline_live_stock_updated_at = updated_at
                                    sync_status.success(
                                        f"同步完成：讀取 {len(shopline_product_df):,} 筆 Shopline 規格、"
                                        f"{len(shopline_stock_df):,} 筆庫存；匹配 {result['matched']:,} 筆，"
                                        f"寫回 {result['updated']:,} 筆，失敗 {result['failed']:,} 筆。"
                                    )
                                    get_products_data.clear()
                                    st.cache_data.clear()
                                    st.rerun()
                        except Exception as e:
                            st.error(f"Shopline 全量同步失敗: {e}")
                    else:
                        if "shopline_live_stock_df" in st.session_state:
                            cached_stock = st.session_state.get("shopline_live_stock_df", pd.DataFrame())
                            st.success(f"最近一次 Shopline 同步套用：{len(cached_stock):,} 筆，時間 {st.session_state.get('shopline_live_stock_updated_at', '')}")
                        else:
                            st.info("按下同步後，系統會讀取全部 Shopline 庫存並寫回 Supabase。完成後上方庫存表會直接顯示更新後資料。")

                    with st.expander("進階：小批測試讀取"):
                        fetch_limit = st.number_input("本次讀取 Shopline 商品數", min_value=5, max_value=100, value=20, step=5)
                        fetch_stock_limit = st.number_input("本次讀取庫存的商品數", min_value=1, max_value=30, value=5, step=1)
                        run_shopline_test = st.button("只測試讀取，不寫回 Supabase")
                        if run_shopline_test:
                            try:
                                _, shopline_products = fetch_products(per_page=int(fetch_limit))
                                _, shopline_warehouses = fetch_warehouses(per_page=50)
                                shopline_product_df = normalize_products(shopline_products)
                                shopline_warehouse_df = normalize_warehouses(shopline_warehouses)
                                st.success(f"已讀取 Shopline 商品 {len(shopline_product_df)} 筆規格資料、倉庫 {len(shopline_warehouse_df)} 筆。")
                                if not shopline_warehouse_df.empty:
                                    st.dataframe(
                                        shopline_warehouse_df[["Warehouse_ID", "Warehouse", "Type"]],
                                        width="stretch",
                                        hide_index=True
                                    )
                                stock_frames = []
                                if not shopline_product_df.empty:
                                    unique_products = shopline_product_df[["Shopline_Product_ID", "Shopline_Name"]].drop_duplicates().head(int(fetch_stock_limit))
                                    for _, shopline_product in unique_products.iterrows():
                                        product_id = clean_text(shopline_product["Shopline_Product_ID"])
                                        if not product_id:
                                            continue
                                        stock_payload, _ = fetch_product_stocks(product_id)
                                        stock_df = normalize_stocks(product_id, shopline_product["Shopline_Name"], stock_payload)
                                        if not stock_df.empty:
                                            stock_frames.append(stock_df)
                                if stock_frames:
                                    shopline_stock_df = pd.concat(stock_frames, ignore_index=True)
                                    shopline_stock_df = merge_shopline_variant_skus(shopline_stock_df, shopline_product_df)
                                    st.dataframe(shopline_stock_df, width="stretch", hide_index=True)
                                else:
                                    st.info("已連線，但這次沒有解析到庫存明細。")
                            except Exception as e:
                                st.error(f"Shopline 測試讀取失敗: {e}")

            except Exception as e:
                st.error(f"庫存監測載入失敗: {e}")

        # 第六個 Tab: 公告管理
        with tab6:
            st.subheader("📢 置頂公告設定")
            try:
                announcement_df = get_data("announcements")
                current_msg = ""
                if not announcement_df.empty and 'Message' in announcement_df.columns:
                    msgs = announcement_df['Message'].dropna().tolist()
                    if msgs: current_msg = str(msgs[-1])
                
                new_msg = st.text_area("公告內容 (支援 Emoji)", value=current_msg, height=100)
                
                if st.button("💾 更新公告", type="primary"):
                    insert_data("announcements", {"Message": new_msg})
                    st.success("公告已更新！請重新整理頁面查看效果。")
                    get_announcement.clear()
            except Exception as e:
                st.error(f"讀取公告失敗: {e}")

        # 第七個 Tab: 足跡追蹤
        with tab7:
            st.subheader("🕵️‍♂️ 系統日誌 (足跡追蹤)")
            st.info("這裡記錄了經銷商的登入與操作行為，協助您發現「棄單」狀況。\n(註：若有登入紀錄、有開始購物，但沒有結帳紀錄，即為潛在棄單)")
            
            if st.button("🔄 刷新日誌"):
                st.rerun()

            try:
                logs = get_data("systemlogs")
                if logs.empty:
                    st.warning("目前尚無日誌紀錄")
                else:
                    logs = logs.sort_values("Time", ascending=False)
                    filter_user = st.multiselect("篩選經銷商", logs['Dealer'].unique())
                    if filter_user:
                        logs = logs[logs['Dealer'].isin(filter_user)]

                    st.dataframe(
                        logs, 
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "Time": st.column_config.TextColumn("時間"),
                            "Dealer": st.column_config.TextColumn("經銷商"),
                            "Action": st.column_config.TextColumn("動作", width="medium"),
                            "Details": st.column_config.TextColumn("詳細資訊", width="large"),
                        }
                    )
            except Exception as e:
                st.error(f"讀取日誌失敗: {e}")

        return

    # 3. 商店頁
    categories = sorted(df_products['Category'].dropna().unique().tolist())
    current_category_value = df_products[df_products['Name'] == st.session_state.current_product_name]['Category']
    default_category = current_category_value.iloc[0] if not current_category_value.empty else categories[0]
    if 'shop_selected_category' not in st.session_state or st.session_state.shop_selected_category not in categories:
        st.session_state.shop_selected_category = default_category

    with st.container(border=True):
        st.markdown("<div class='shop-toolbar-title'>採購工作台</div>", unsafe_allow_html=True)
        st.markdown("<div class='shop-toolbar-meta'>先選項目縮小範圍，也可以直接搜尋品名、品牌、顏色或尺寸。</div>", unsafe_allow_html=True)
        f_cat, f_search, f_product = st.columns([1.1, 1.5, 1.8], vertical_alignment="bottom")

        with f_cat:
            selected_cat = st.selectbox(
                "項目",
                categories,
                index=categories.index(st.session_state.shop_selected_category),
                key="shop_selected_category",
            )

        with f_search:
            search_query = st.text_input("搜尋商品", placeholder="輸入品名、品牌、顏色或尺寸", key="shop_search")

        if search_query.strip():
            query = search_query.strip().lower()
            df_filtered = df_products.copy()
            searchable_cols = [col for col in ['Name', 'Brand', 'Color', 'Size', 'Category'] if col in df_filtered.columns]
            search_mask = pd.Series(False, index=df_filtered.index)
            for col in searchable_cols:
                search_mask = search_mask | df_filtered[col].astype(str).str.lower().str.contains(query, na=False)
            df_filtered = df_filtered[search_mask]
            scope_label = "搜尋結果"
        else:
            df_filtered = df_products[df_products['Category'] == selected_cat].copy()
            scope_label = selected_cat

        product_list = sorted(df_filtered['Name'].dropna().unique().tolist())
        shop_product_scope = df_filtered

        with f_product:
            if product_list:
                if st.session_state.current_product_name not in product_list:
                    st.session_state.current_product_name = product_list[0]
                selected_product = st.selectbox(
                    "快速選擇商品",
                    product_list,
                    index=product_list.index(st.session_state.current_product_name),
                    key=f"shop_product_picker_{st.session_state.product_picker_version}",
                )
                if selected_product != st.session_state.current_product_name:
                    set_current_product(selected_product)
                    st.rerun()
            else:
                st.selectbox("快速選擇商品", ["沒有符合條件的商品"], disabled=True)

        st.caption(f"{scope_label}：{len(product_list)} 款商品 / {len(df_filtered)} 個 SKU")

    if shop_product_scope.empty:
        st.warning("目前沒有符合篩選條件的商品，請調整上方搜尋或項目。")
        return

    col_visual, col_select, col_cart = st.columns([1.35, 1.45, 2.0], gap="medium")
    current_name = st.session_state.current_product_name
    current_product_data = df_products[df_products['Name'] == current_name]

    with col_select:
        with st.container(border=True):
            st.markdown(f"<div class='product-title'>{escape_html(current_name)}</div>", unsafe_allow_html=True)
            st.markdown(f"<span class='brand-pill'>Brand: {escape_html(current_product_data.iloc[0]['Brand'])}</span>", unsafe_allow_html=True)
            available_colors = current_product_data['Color'].unique()
            selected_color = st.selectbox("顏色", available_colors, key=f"color_sel_{current_name}")
            variants = current_product_data[current_product_data['Color'] == selected_color]
            st.markdown("<br>", unsafe_allow_html=True)
            h1, h2, h3, h4 = st.columns([0.85, 1.25, 1.25, 0.95], vertical_alignment="center")
            h1.markdown("<div class='desktop-only table-head'>尺寸</div>", unsafe_allow_html=True)
            h2.markdown("<div class='desktop-only table-head'>價格</div>", unsafe_allow_html=True)
            h3.markdown("<div class='desktop-only table-head'>數量</div>", unsafe_allow_html=True)
            h4.markdown("<div class='desktop-only table-head'>加入</div>", unsafe_allow_html=True)
            st.markdown("<span class='sku-section-marker'></span>", unsafe_allow_html=True)
            st.markdown(
                "<div class='desktop-sku-header'>"
                "<div class='sku-table-divider'></div>"
                "</div>",
                unsafe_allow_html=True
            )

            def add_to_cart_callback(p_id, p_name, p_spec, p_w, p_r, q_key, p_brand, p_category):
                qty = st.session_state[q_key]
                if qty <= 0: return
                if p_id in st.session_state.cart:
                    st.session_state.cart[p_id]['qty'] += qty
                else:
                    st.session_state.cart[p_id] = {
                        "id": p_id, "name": p_name, "spec": p_spec,
                        "wholesale_price": int(p_w), "retail_price": int(p_r),
                        "brand": p_brand, "category": p_category, "qty": qty
                    }
                st.toast(f"已加入 {p_name} x {qty}", icon="🛒")
                st.session_state[q_key] = 1
                
                if not st.session_state.has_logged_cart_start:
                    log_system_event(st.session_state['user'], "Start Shopping", f"Added first item: {p_name}")
                    st.session_state.has_logged_cart_start = True

            for i, (_, sku) in enumerate(variants.iterrows()):
                st.markdown("<span class='sku-row-start'></span>", unsafe_allow_html=True)
                c1, c2, c3, c4 = st.columns([0.85, 1.25, 1.25, 0.95], vertical_alignment="center")
                with c1: st.markdown(f"<div class='sku-size'>{escape_html(sku['Size'])}</div>", unsafe_allow_html=True)
                with c2:
                    st.markdown(
                        f"<div class='price-wholesale'>${int(sku['Wholesale_Price']):,}</div>"
                        f"<div class='price-retail'>${int(sku['Retail_Price']):,}</div>",
                        unsafe_allow_html=True
                    )
                with c3:
                    qty_key = f"qty_input_{sku['Product_ID']}_{selected_color}_{i}"
                    st.number_input("Qty", min_value=1, value=1, step=1, key=qty_key, label_visibility="collapsed")
                with c4:
                    st.button("ADD", key=f"add_{sku['Product_ID']}_{selected_color}_{i}", type="primary", width="stretch",
                        on_click=add_to_cart_callback,
                        args=(sku['Product_ID'], current_name, f"{selected_color} / {str(sku['Size'])}", sku['Wholesale_Price'], sku['Retail_Price'], qty_key, current_product_data.iloc[0]['Brand'], current_product_data.iloc[0]['Category']))

    with col_visual:
        with st.container(border=True):
            img_row = current_product_data[current_product_data['Color'] == selected_color]
            if img_row.empty: img_row = current_product_data.iloc[0]
            else: img_row = img_row.iloc[0]
            main_img = convert_drive_url(img_row['Image_URL'])
            if main_img: st.image(main_img, width="stretch")
            else: st.warning("No Image")

        if not is_mobile_client():
            with st.container(border=True):
                st.markdown("<span class='desktop-related-products'></span>", unsafe_allow_html=True)
                st.markdown("<div class='section-heading'>Related Products / 同系列商品</div>", unsafe_allow_html=True)
                current_category = current_product_data.iloc[0]['Category']
                same_category_products = shop_product_scope[shop_product_scope['Category'] == current_category]['Name'].unique()
                others = [p for p in same_category_products if p != current_name]
                for i in range(0, len(others), 2):
                    cols = st.columns(2)
                    batch = others[i:i+2]
                    for idx, other_prod in enumerate(batch):
                        row = df_products[df_products['Name'] == other_prod].iloc[0]
                        thumb = convert_drive_url(row['Image_URL'])
                        with cols[idx]:
                            with st.container(border=True):
                                if thumb:
                                    st.image(thumb, width="stretch")
                                else:
                                    st.markdown("<div style='height: 150px; background-color: #f0f0f0; display: flex; align-items: center; justify-content: center; color: #666;'>No Image</div>", unsafe_allow_html=True)

                                display_name = str(other_prod)
                                if len(display_name) > 18:
                                    display_name = display_name[:17] + "..."
                                if st.button(display_name, key=f"view_{other_prod}_{i}_{idx}", width="stretch", help=str(other_prod)):
                                    set_current_product(other_prod)
                                    st.rerun()
                if not others:
                    st.caption("此分類下無其他商品")

    # 購物車邏輯
    def update_item_qty(item_id):
        new_val = st.session_state[f"cart_qty_{item_id}"]
        if item_id in st.session_state.cart:
            st.session_state.cart[item_id]['qty'] = new_val

    with col_cart:
        with st.container(border=True):
            st.markdown("<div class='cart-title'>購物車</div>", unsafe_allow_html=True)
            st.divider()
            if st.session_state.cart:
                BRAND_RULES, _ = get_brand_rules()
                cart_qty_total = sum(int(item.get('qty', 0)) for item in st.session_state.cart.values())
                cart_sku_total = len(st.session_state.cart)
                st.markdown(f"<div class='cart-subtitle'>{cart_sku_total} 個 SKU / {cart_qty_total} 件商品</div>", unsafe_allow_html=True)
                for item in st.session_state.cart.values():
                    if 'brand' not in item: item['brand'] = "default"
                    if 'wholesale_price' not in item: item['wholesale_price'] = item.get('Wholesale_Price', 0)
                    if 'retail_price' not in item: item['retail_price'] = item.get('Retail_Price', 0)

                brand_groups = {} 
                for item in st.session_state.cart.values():
                    b_name = item['brand']
                    if b_name not in brand_groups:
                        brand_groups[b_name] = {'items': [], 'raw_wholesale_total': 0, 'is_wholesale_qualified': False, 'is_shipping_qualified': False}
                    brand_groups[b_name]['items'].append(item)
                    brand_groups[b_name]['raw_wholesale_total'] += int(round(item['wholesale_price'] * item['qty']))

                is_order_free_shipping = False 
                grand_total_subtotal = 0
                grand_total_tax = 0
                
                for b_name, data in brand_groups.items():
                    safe_default = {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}
                    rule = BRAND_RULES.get(b_name, BRAND_RULES.get("default", safe_default))
                    d_rate = rule.get('discount_rate', 0.7)
                    w_threshold = rule.get('wholesale_threshold', 10000)
                    s_threshold = rule.get('shipping_threshold', 10000)
                    
                    if data['raw_wholesale_total'] >= w_threshold:
                        data['is_wholesale_qualified'] = True
                        brand_subtotal = data['raw_wholesale_total']
                        brand_tax = int(round(brand_subtotal * TAX_RATE))
                    else:
                        data['is_wholesale_qualified'] = False
                        brand_subtotal = 0
                        brand_tax = 0
                        for item in data['items']:
                            brand_subtotal += int(round(item['retail_price'] * d_rate)) * item['qty']

                    if data['raw_wholesale_total'] >= s_threshold:
                        data['is_shipping_qualified'] = True
                        is_order_free_shipping = True
                    
                    grand_total_subtotal += brand_subtotal
                    grand_total_tax += brand_tax
                    for item in data['items']:
                        if data['is_wholesale_qualified']: item['final_unit_price'] = item['wholesale_price']
                        else: item['final_unit_price'] = int(round(item['retail_price'] * d_rate))
                        item['final_subtotal'] = item['final_unit_price'] * item['qty']

                for b_name, data in brand_groups.items():
                    safe_default = {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}
                    rule = BRAND_RULES.get(b_name, BRAND_RULES.get("default", safe_default))
                    d_rate = rule.get('discount_rate', 0.7)
                    w_threshold = rule.get('wholesale_threshold', 10000)
                    s_threshold = rule.get('shipping_threshold', 10000)
                    wholesale_remaining = max(0, int(w_threshold) - int(data['raw_wholesale_total']))
                    shipping_remaining = max(0, int(s_threshold) - int(data['raw_wholesale_total']))
                    is_nonstop = str(b_name).strip().lower() == "non-stop dogwear"
                    
                    if data['is_wholesale_qualified']:
                        title = f"{escape_html(b_name)} | 已達批發門檻"
                        if data['is_shipping_qualified']:
                            meta = f"小計 ${data['raw_wholesale_total']:,} | 已免運"
                        else:
                            meta = f"小計 ${data['raw_wholesale_total']:,} | 再 ${shipping_remaining:,} 免運"
                        st.markdown(
                            f"<div class='threshold-card ok'><div class='threshold-title'>{title}</div><div class='threshold-meta'>{escape_html(meta)}</div></div>",
                            unsafe_allow_html=True
                        )
                    else:
                        if is_nonstop:
                            title = f"{escape_html(b_name)} | 未達批發門檻"
                            meta = f"目前零售 {int(d_rate * 10)} 折 | 再 ${wholesale_remaining:,} 達批發"
                        else:
                            title = f"{escape_html(b_name)} | 未達出貨門檻"
                            meta = f"再 ${wholesale_remaining:,} 可出貨"
                        st.markdown(
                            f"<div class='threshold-card warn'><div class='threshold-title'>{title}</div><div class='threshold-meta'>{escape_html(meta)}</div></div>",
                            unsafe_allow_html=True
                        )

                    for item in data['items']:
                        c_name, c_qty, c_del, c_price = st.columns([2.05, 1.9, 0.7, 0.85], vertical_alignment="center")
                        
                        with c_name:
                            st.markdown(f"<div class='cart-item-name'>{escape_html(item.get('name', ''))}</div><div class='cart-item-spec'>{escape_html(item.get('spec', ''))}</div>", unsafe_allow_html=True)
                        
                        with c_qty:
                            st.number_input(
                                "Qty",
                                min_value=1,
                                value=int(item['qty']),
                                step=1,
                                key=f"cart_qty_{item['id']}",
                                label_visibility="collapsed",
                                on_change=update_item_qty,
                                args=(item['id'],)
                            )
                        
                        with c_del:
                            if st.button("✕", key=f"cart_del_{item['id']}", type="secondary", width="stretch", help="移除此商品"):
                                del st.session_state.cart[item['id']]
                                st.rerun()
                        
                        with c_price:
                            st.markdown(f"<div class='cart-item-price'>${int(item['final_subtotal']):,}</div>", unsafe_allow_html=True)
                    st.divider()

                if is_order_free_shipping:
                    shipping = 0
                    shipping_msg = "✅ 符合免運資格"
                else:
                    shipping = SHIPPING_FEE
                    shipping_msg = f"運費 ${SHIPPING_FEE}"

                is_editing = st.session_state.get('editing_order_id') is not None
                default_discount = 0
                if is_editing:
                    default_discount = int(st.session_state.get('editing_customer_info', {}).get('Extra_Discount', 0))

                extra_discount = 0
                if is_admin(user):
                    st.markdown("---")
                    extra_discount = st.number_input(
                        "🔧 管理員：總價手動調整 (+輸入折扣扣除, -輸入額外加價)", 
                        value=default_discount, 
                        step=10, 
                        key="cart_extra_discount", 
                        help="可以直接改變最後的結帳總金額"
                    )

                grand_total = grand_total_subtotal + grand_total_tax + shipping - extra_discount
                
                summary_rows = [
                    ("小計 Subtotal", f"${grand_total_subtotal:,}"),
                    ("稅金 Tax", f"${grand_total_tax:,}"),
                    ("運費 Shipping", shipping_msg),
                ]
                summary_html = "<div class='cart-total-box'>"
                for label, value in summary_rows:
                    summary_html += f"<div class='cart-total-row'><span>{escape_html(label)}</span><span>{escape_html(value)}</span></div>"
                
                if extra_discount != 0:
                    sign = "-" if extra_discount > 0 else "+"
                    summary_html += f"<div class='cart-total-row'><span>手動調整 Adjustment</span><span>{sign}${abs(extra_discount):,}</span></div>"

                summary_html += f"<div class='cart-total-row final'><span>總計含稅</span><span>${grand_total:,}</span></div></div>"
                st.markdown(summary_html, unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div class="mobile-cart-bar">
                        <div>
                            <div class="mobile-cart-main">購物車 {cart_sku_total} SKU / {cart_qty_total} 件</div>
                            <div class="mobile-cart-sub">往下確認明細與送出訂單</div>
                        </div>
                        <div class="mobile-cart-total">${grand_total:,}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                if is_order_free_shipping: st.info("🎉 訂單已享免運優惠！")
                else: st.warning(f"⚠️ 全單未達免運標準，需付運費 ${SHIPPING_FEE}")
                
                if is_editing:
                    btn_text = "💾 確認修改並儲存 (Admin Update)"
                    client_name = st.session_state.get('editing_customer_info', {}).get('Customer_Name', 'Unknown')
                    st.warning(f"🔧 正在修改客戶 [{client_name}] 的訂單：{st.session_state.editing_order_id}")
                else: 
                    st.markdown("---")
                    default_checkout_email = str(user.get('Contact_Email', '')).replace('nan', '')
                    if not default_checkout_email and "@" in str(user.get('Username', '')):
                        default_checkout_email = user['Username']
                    
                    contact_email_input = st.text_input("📧 接收訂單通知 Email (必填)", value=default_checkout_email, help="訂單確認信將寄送至此信箱")
                    btn_text = "前往確認採購明細"

                disable_btn = (not is_editing) and (not contact_email_input)
                
                if st.button(btn_text, type="primary", width="stretch", disabled=disable_btn):
                    if not is_editing:
                        st.session_state.checkout_contact_email = contact_email_input
                        st.session_state.page = 'checkout_review'
                        st.rerun()

                    if is_editing:
                        order_id = st.session_state.editing_order_id
                        saved_info = st.session_state.get('editing_customer_info', {})
                        c_name = saved_info.get('Customer_Name', user.get('Dealer_Name', ''))
                        c_email = saved_info.get('Email', user.get('Username', '')) 
                        c_phone = saved_info.get('Phone', user.get('Phone', ''))
                        
                        # 🛡️ 濾網二：強制把所有 NaN 替換成乾淨字串
                        c_name = "" if pd.isna(c_name) else str(c_name)
                        c_email = "" if pd.isna(c_email) else str(c_email)
                        c_phone = "" if pd.isna(c_phone) else str(c_phone)
                        
                        c_status = "賣方已修改"
                    else:
                        order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                        c_name = user.get('Dealer_Name', '')
                        c_email = contact_email_input 
                        c_phone = user.get('Phone', '')
                        
                        # 🛡️ 濾網二：強制把所有 NaN 替換成乾淨字串
                        c_name = "" if pd.isna(c_name) else str(c_name)
                        c_email = "" if pd.isna(c_email) else str(c_email)
                        c_phone = "" if pd.isna(c_phone) else str(c_phone)
                        
                        c_status = "待處理"
                        
                        try:
                            if c_email != str(user.get('Contact_Email', '')):
                                if update_data_by_id("users", "Username", user['Username'], {"Contact_Email": c_email}):
                                    st.session_state['user']['Contact_Email'] = c_email
                        except: pass 

                    final_cart_data = st.session_state.cart.copy()
                    
                    # 🛡️ 濾網三：所有的金額都強迫轉為安全的整數
                    order_data = {
                        "Order_ID": order_id, 
                        "Order_Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Customer_Name": c_name, 
                        "Email": c_email, 
                        "Phone": c_phone,
                        "Items_Json": final_cart_data, 
                        "Subtotal": int(grand_total_subtotal), 
                        "Tax": int(grand_total_tax), 
                        "Shipping": int(shipping), 
                        "Total": int(grand_total), 
                        "Status": c_status,
                        "Extra_Discount": int(extra_discount),
                        "Tracking_Number": "",
                        "Admin_Note": ""
                    }

                    try:
                        if is_editing:
                            update_dict = {
                                "Customer_Name": c_name,
                                "Email": c_email,
                                "Phone": c_phone,
                                "Items_Json": final_cart_data,
                                "Subtotal": int(grand_total_subtotal),
                                "Tax": int(grand_total_tax),
                                "Shipping": int(shipping),
                                "Total": int(grand_total),
                                "Status": c_status,
                                "Extra_Discount": int(extra_discount)
                            }
                            if update_data_by_id("orders", "Order_ID", order_id, update_dict):
                                log_system_event(user, "Admin Edit Checkout", f"Order ID: {order_id}")
                                st.success(f"訂單 {order_id} 修改完成！")
                                with st.spinner("正在寄送通知信給客戶..."):
                                    if send_order_email(order_data, final_cart_data, is_update=True):
                                        st.toast("📧 確認信已寄出！", icon="✅")
                                    else: st.error("信件寄送失敗")
                            else: st.error("更新訂單至資料庫失敗")
                        else:
                            if insert_data("orders", order_data):
                                log_system_event(user, "Checkout", f"Order ID: {order_id}")
                                st.success(f"訂單 {order_id} 已送出!")
                                with st.spinner("正在寄送確認信..."):
                                    if send_order_email(order_data, final_cart_data):
                                        st.toast("📧 確認信已寄出！", icon="✅")
                                    else: st.warning("訂單已成立，但信件寄送失敗")
                            else: st.error("新增訂單至資料庫失敗")

                        st.session_state.cart = {}
                        st.session_state.editing_order_id = None
                        st.session_state.editing_customer_info = None
                        st.session_state.has_logged_cart_start = False 
                        time.sleep(1)
                        if is_admin(user): st.session_state.page = 'admin_orders'
                        else: st.session_state.page = 'shop'
                        st.rerun()
                    except Exception as e: st.error(f"訂單處理失敗: {e}")
            else: st.info("購物車是空的")

def login_page():
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.markdown("<br><br><br>", unsafe_allow_html=True)
        st.markdown("## Bluebulous B2B 採購系統")
        st.warning("💡 建議使用 筆電 / 桌機 登入以獲得最佳體驗")
        with st.form("login"):
            u = st.text_input("Username / Email")
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Login", width="stretch", type="primary"):
                with st.spinner("正在連線驗證中..."):
                    username = str(u).strip()
                    users = get_data("users")
                    
                    if users.empty:
                        if ENABLE_DEFAULT_ADMIN and username == "admin" and p == "admin":
                            st.session_state['user'] = {"Username": "admin", "Dealer_Name": "System Admin", "Password": "admin"}
                            st.rerun()
                        else:
                            st.error("系統資料庫目前為空，且預設管理員登入已關閉。請先建立正式管理員帳號。")
                    elif 'Username' not in users.columns:
                        st.error("⚠️ 資料表結構錯誤：找不到 Username 欄位。")
                    else:
                        match = users[users['Username'].astype(str).str.strip() == username]
                        if not match.empty and verify_password(match.iloc[0].get('Password', ''), p):
                            user_record = match.iloc[0].to_dict()
                            if not is_password_hash(user_record.get('Password', '')):
                                upgraded_password = hash_password(p)
                                if update_data_by_id("users", "Username", username, {"Password": upgraded_password}):
                                    user_record['Password'] = upgraded_password
                            st.session_state['user'] = user_record
                            log_system_event(user_record, "Login", "User logged in")
                            st.rerun()
                        else: 
                            st.error("帳號或密碼錯誤")

if __name__ == "__main__":
    if 'user' not in st.session_state:
        login_page()
    else:
        main_app(st.session_state['user'])
