import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
import json
import time

from config import ENABLE_DEFAULT_ADMIN, SHIPPING_FEE, TAX_RATE
from services.email_service import send_order_email
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
        background-color: #1e1e1e;
        color: #ffffff;
    }
    
    /* 2. Header 設定 */
    header[data-testid="stHeader"] {
        background-color: #1e1e1e;
        color: white;
    }
    
    /* 3. 白色卡片容器 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 20px;
    }
    
    /* 4. 強制白色卡片內的文字為黑色 */
    div[data-testid="stVerticalBlockBorderWrapper"] p,
    div[data-testid="stVerticalBlockBorderWrapper"] h1,
    div[data-testid="stVerticalBlockBorderWrapper"] h2,
    div[data-testid="stVerticalBlockBorderWrapper"] h3,
    div[data-testid="stVerticalBlockBorderWrapper"] span,
    div[data-testid="stVerticalBlockBorderWrapper"] div,
    div[data-testid="stVerticalBlockBorderWrapper"] label,
    div[data-testid="stVerticalBlockBorderWrapper"] li {
        color: #000000 !important;
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
        color: #000000 !important;          
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
        background-color: #f0f0f0 !important; 
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
        max-width: 140px !important;
        min-width: 120px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-baseweb="input"] {
        min-height: 40px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"] {
       margin-top: 2px;
    }

    /* === 桌面採購篩選欄位 === */
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] input {
        color: #111111 !important;
        caret-color: #111111 !important;
        background-color: #f8fafc !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] input::placeholder {
        color: #6b7280 !important;
        opacity: 1 !important;
    }
    section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
        background-color: #f8fafc !important;
        color: #111111 !important;
    }

    /* === 桌面採購工作台視覺 === */
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
        border-bottom: 1px solid #3d3d3d;
        padding-bottom: 8px;
        margin-bottom: 4px;
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

    /* === 📱 手機版專用優化 === */
    @media only screen and (max-width: 768px) {
        .block-container {
            padding-top: 1rem !important;
            padding-bottom: 5rem !important; 
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        div[data-testid="column"] {
            padding: 0px 2px !important;
            min-width: 0px !important; 
        }
        div.stButton > button {
            min-height: 40px !important;
        }
        p, .stMarkdown, div[data-testid="stText"] {
            font-size: 14px !important;
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
            if st.button("前往結帳 (查看詳情)", type="primary", width="stretch"):
                 st.toast("請往下滑動查看完整購物車", icon="👇")
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

        if st.session_state.page == 'shop':
            st.divider()
            st.markdown('<div class="nav-section-title">FOR DOGS</div>', unsafe_allow_html=True)
            categories = sorted(df_products['Category'].dropna().unique().tolist())
            selected_cat = st.radio("Category", categories, label_visibility="collapsed")

            search_query = st.text_input("搜尋商品", placeholder="輸入品名、品牌、顏色或尺寸", key="shop_search")
            if search_query.strip():
                query = search_query.strip().lower()
                df_filtered = df_products.copy()
                searchable_cols = [col for col in ['Name', 'Brand', 'Color', 'Size', 'Category'] if col in df_filtered.columns]
                search_mask = pd.Series(False, index=df_filtered.index)
                for col in searchable_cols:
                    search_mask = search_mask | df_filtered[col].astype(str).str.lower().str.contains(query, na=False)
                df_filtered = df_filtered[search_mask]
                st.caption("搜尋會跨所有項目，不受上方分類限制")
            else:
                df_filtered = df_products[df_products['Category'] == selected_cat].copy()

            product_list = sorted(df_filtered['Name'].dropna().unique().tolist())
            shop_product_scope = df_filtered
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
                scope_label = "搜尋結果" if search_query.strip() else selected_cat
                st.caption(f"{scope_label}：{len(product_list)} 款商品 / {len(df_filtered)} 個 SKU")
            else:
                st.warning("沒有符合篩選條件的商品")
    
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
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📦 訂單管理", "⚙️ 品牌門檻設定", "👥 用戶權限管理", "📊 銷售數據中心", "📢 公告管理", "🕵️‍♂️ 足跡追蹤"])
        
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
                        orders['Extra_Discount'] = orders['Extra_Discount'].fillna(0).astype(int)

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
                            expander_title = f"{status_icon} {status_str} | {row['Customer_Name']} (${row['Total']})"
                            
                            with st.expander(expander_title):
                                st.markdown(f"### 目前狀態: {status_badges}", unsafe_allow_html=True)
                                
                                c1, c2, c3 = st.columns([1.5, 2, 1])
                                with c1:
                                    st.markdown(f"**訂單編號:** `{row['Order_ID']}`")
                                    st.markdown(f"**客戶:** {row['Customer_Name']}")
                                    st.markdown(f"**Email:** {row['Email']}")
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
                                            "Customer_Name": str(row['Customer_Name']) if pd.notna(row['Customer_Name']) else "", 
                                            "Email": str(row['Email']) if pd.notna(row['Email']) else "", 
                                            "Phone": str(row['Phone']) if pd.notna(row['Phone']) else "",
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
            st.subheader("📊 數據戰情室")
            st.info("💡 這裡展示即時的銷售數據分析，協助您判斷通路價值與熱銷商品。")
            
            try:
                orders = get_data("orders")
                if orders.empty:
                    st.warning("目前沒有訂單數據可供分析。")
                else:
                    orders['Order_Date'] = pd.to_datetime(orders['Order_Time'])
                    orders['Month'] = orders['Order_Date'].dt.strftime('%Y-%m')
                    orders['Total'] = pd.to_numeric(orders['Total'], errors='coerce').fillna(0)
                    
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
                                    'Month': row['Order_Date'].strftime('%Y-%m'),
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
                    
                    k1, k2, k3 = st.columns(3)
                    k1.metric("💰 總營業額 (Total Revenue)", f"${total_rev:,}")
                    k2.metric("📦 總訂單數 (Total Orders)", f"{total_orders}")
                    k3.metric("📈 平均客單價 (AOV)", f"${avg_order_value:,}")
                    
                    st.divider()

                    c_chart1, c_chart2 = st.columns(2)
                    
                    with c_chart1:
                        st.markdown("##### 🏆 經銷商貢獻度排行 (Top Dealers)")
                        dealer_sales = orders.groupby('Customer_Name')['Total'].sum().sort_values(ascending=False).head(10)
                        st.bar_chart(dealer_sales, color="#ff5500")
                        st.caption("前 10 名貢獻營收最高的經銷商")

                    with c_chart2:
                        st.markdown("##### 📅 每月營收走勢 (Monthly Revenue)")
                        monthly_sales = orders.groupby('Month')['Total'].sum()
                        st.line_chart(monthly_sales, color="#3498db")
                        st.caption("觀察銷售季節性變化")

                    st.divider()
                    
                    c_chart3, c_chart4 = st.columns(2)
                    
                    if not df_items.empty:
                        with c_chart3:
                            st.markdown("##### 🏷️ 品牌銷售佔比 (Sales by Brand)")
                            brand_sales_df = df_items.groupby('Brand')['Subtotal'].sum().reset_index()
                            
                            total_brand = brand_sales_df['Subtotal'].sum()
                            brand_sales_df['Percentage_Num'] = (brand_sales_df['Subtotal'] / total_brand) * 100
                            brand_sales_df['Percentage'] = brand_sales_df['Percentage_Num'].round(1).astype(str) + '%'
                            brand_sales_df['Label'] = brand_sales_df.apply(lambda x: f"{x['Brand']} {x['Percentage']}" if x['Percentage_Num'] > 3 else "", axis=1)

                            base_brand = alt.Chart(brand_sales_df).encode(
                                theta=alt.Theta(field="Subtotal", type="quantitative", stack=True),
                                color=alt.Color(field="Brand", type="nominal", legend=alt.Legend(title="品牌", orient="bottom")),
                                tooltip=[alt.Tooltip("Brand", title="品牌"), alt.Tooltip("Subtotal", title="銷售總額"), alt.Tooltip("Percentage", title="佔比")]
                            )
                            pie_brand = base_brand.mark_arc(innerRadius=0, outerRadius=130)
                            text_brand = base_brand.mark_text(size=12, fill="white", fontWeight="bold", radius=80).encode(text="Label:N")
                            
                            chart_brand = (pie_brand + text_brand).properties(height=350)
                            st.altair_chart(chart_brand, width="stretch")

                        with c_chart4:
                            st.markdown("##### 📂 產品分類佔比 (Sales by Category)")
                            cat_sales_df = df_items.groupby('Category')['Subtotal'].sum().reset_index()
                            
                            total_cat = cat_sales_df['Subtotal'].sum()
                            cat_sales_df['Percentage_Num'] = (cat_sales_df['Subtotal'] / total_cat) * 100
                            cat_sales_df['Percentage'] = cat_sales_df['Percentage_Num'].round(1).astype(str) + '%'
                            cat_sales_df['Label'] = cat_sales_df.apply(lambda x: f"{x['Category']} {x['Percentage']}" if x['Percentage_Num'] > 3 else "", axis=1)

                            base_cat = alt.Chart(cat_sales_df).encode(
                                theta=alt.Theta(field="Subtotal", type="quantitative", stack=True),
                                color=alt.Color(field="Category", type="nominal", legend=alt.Legend(title="分類", orient="bottom")),
                                tooltip=[alt.Tooltip("Category", title="分類"), alt.Tooltip("Subtotal", title="銷售總額"), alt.Tooltip("Percentage", title="佔比")]
                            )
                            pie_cat = base_cat.mark_arc(innerRadius=70, outerRadius=130)
                            text_cat = base_cat.mark_text(size=12, fill="white", fontWeight="bold", radius=100).encode(text="Label:N")
                            
                            chart_cat = (pie_cat + text_cat).properties(height=350)
                            st.altair_chart(chart_cat, width="stretch")
                    
                    st.divider()
                    
                    st.markdown("##### 🔥 熱銷商品 TOP 20")
                    if not df_items.empty:
                        top_products = df_items.groupby(['Product', 'Brand', 'Category'])[['Qty', 'Subtotal']].sum().reset_index()
                        top_products = top_products.sort_values('Subtotal', ascending=False).head(20)
                        st.dataframe(
                            top_products,
                            column_config={
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

        # 第五個 Tab: 公告管理
        with tab5:
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

        # 第六個 Tab: 足跡追蹤
        with tab6:
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
    if shop_product_scope.empty:
        st.warning("目前沒有符合篩選條件的商品，請調整左側搜尋或篩選。")
        return

    col_visual, col_select, col_cart = st.columns([1.5, 1.5, 2.0], gap="medium")
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
            h1, h2, h3, h4, h5 = st.columns([1.0, 1.6, 1.6, 1.6, 1.8], vertical_alignment="center")
            h1.markdown("<div class='table-head'>尺寸</div>", unsafe_allow_html=True)
            h2.markdown("<div class='table-head'>數量</div>", unsafe_allow_html=True)
            h3.markdown("<div class='table-head'>批發價<br>(未稅)</div>", unsafe_allow_html=True)
            h4.markdown("<div class='table-head'>零售價<br>(含稅)</div>", unsafe_allow_html=True)
            h5.markdown("") 

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
                c_row = st.container()
                c1, c2, c3, c4, c5 = c_row.columns([1.0, 1.6, 1.6, 1.6, 1.8], vertical_alignment="center")
                with c1: st.markdown(f"<div class='sku-size'>{escape_html(sku['Size'])}</div>", unsafe_allow_html=True)
                with c2:
                    qty_key = f"qty_input_{sku['Product_ID']}_{selected_color}_{i}"
                    st.number_input("Qty", min_value=1, value=1, step=1, key=qty_key, label_visibility="collapsed")
                with c3: st.markdown(f"<div class='price-wholesale'>${int(sku['Wholesale_Price']):,}</div>", unsafe_allow_html=True)
                with c4: st.markdown(f"<div class='price-retail'>${int(sku['Retail_Price']):,}</div>", unsafe_allow_html=True)
                with c5:
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
            if not others: st.caption("此分類下無其他商品")

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
                    
                    if data['is_wholesale_qualified']:
                        msg = f"**{b_name}** | 小計 ${data['raw_wholesale_total']:,} / 批發門檻 ${int(w_threshold):,} ➝ **批發價**"
                        if data['is_shipping_qualified']:
                            msg += f" | 免運門檻已達 ${int(s_threshold):,}"
                        else:
                            msg += f" | 再 ${shipping_remaining:,} 免運"
                        st.success(msg, icon="✅")
                    else:
                        msg = f"**{b_name}** | 小計 ${data['raw_wholesale_total']:,} / 批發門檻 ${int(w_threshold):,} ➝ 目前 **零售{int(d_rate*10)}折**"
                        msg += f" | 再 ${wholesale_remaining:,} 達批發價"
                        if data['is_shipping_qualified']:
                            msg += f" | 免運門檻已達 ${int(s_threshold):,}"
                        else:
                            msg += f" | 再 ${shipping_remaining:,} 免運"
                        st.warning(msg, icon="⚠️")

                    for item in data['items']:
                        c_name, c_qty, c_del, c_price = st.columns([2.0, 1.8, 0.4, 1.2], vertical_alignment="center")
                        
                        with c_name:
                            st.markdown(f"<div style='line-height:1.2; font-weight:bold;'>{escape_html(item.get('name', ''))}</div><div style='color:#cccccc; font-size:12px; margin-top:2px;'>{escape_html(item.get('spec', ''))}</div>", unsafe_allow_html=True)
                        
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
                            if st.button("✖", key=f"cart_del_{item['id']}", type="secondary", help="移除此商品"):
                                del st.session_state.cart[item['id']]
                                st.rerun()
                        
                        with c_price:
                            st.markdown(f"<div style='text-align:right; font-weight:bold;'>${item['final_subtotal']}</div>", unsafe_allow_html=True)
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
                    btn_text = "CHECKOUT / 送出訂單"

                disable_btn = (not is_editing) and (not contact_email_input)
                
                if st.button(btn_text, type="primary", width="stretch", disabled=disable_btn):
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
