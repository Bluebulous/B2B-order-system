import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import json
import time

# Email 相關模組
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- 1. 系統設定 ---
st.set_page_config(
    page_title="Bluebulous B2B",
    layout="wide",
    page_icon="https://raw.githubusercontent.com/Bluebulous/product-images/main/Bluebulous%20logo.jpg"
)

# Google Sheets 連線
conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nuIdMqrRKhWIbuqsz0eVwKYr24HLDDdV7CNn_SPiSYI/edit"

# B2B 基礎規則
TAX_RATE = 0.05
SHIPPING_FEE = 125

# 定義管理員帳號
ADMIN_USERS = ["admin", "bluebulous", "test@test.com"] 

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
    
    /* 7. [新增] 調整卡片內一般按鈕文字大小 (影響同系列商品按鈕) */
    div[data-testid="stVerticalBlockBorderWrapper"] button p {
        font-size: 10px !important; /* 這裡控制同系列商品按鈕文字大小 */
        font-weight: bold !important;
    }
    
    /* 購物車小按鈕 (保持較大字體以顯示符號) */
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"] p {
         font-size: 20px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"] {
        color: #000000 !important;
        background-color: #ffffff !important; 
        border: none !important;
        box-shadow: none !important;
        padding: 0px !important;
        width: 30px !important;        
        height: 30px !important;  
    }
    div[data-testid="stVerticalBlockBorderWrapper"] button[kind="secondary"]:hover {
        color: #ff5500 !important;
        background-color: #f9f9f9 !important; 
    }

    /* 主要按鈕 (ADD / CHECKOUT) */
    button[kind="primary"] {
        background-color: #ff5500 !important;
        border: none !important;
        color: white !important;
        font-weight: bold;
    }
    button[kind="primary"] p {
        color: white !important; 
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

    /* === 📱 手機版專用優化 (電腦版不會吃到這段設定) === */
    @media only screen and (max-width: 768px) {
        
        /* 1. 只有手機版：縮小留白，讓畫面變寬 */
        .block-container {
            padding-top: 1rem !important;
            padding-bottom: 5rem !important; /* 底部留多一點，防止被手機瀏覽器選單擋住 */
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        
        /* 2. 只有手機版：調整欄位間距，避免文字擠成一團 */
        div[data-testid="column"] {
            padding: 0px 2px !important;
            min-width: 0px !important; /* 允許欄位縮得更小 */
        }
        
        /* 3. 只有手機版：按鈕稍微變高一點，比較好按 */
        div.stButton > button {
            min-height: 45px !important;
            padding-left: 5px !important;
            padding-right: 5px !important;
        }

        /* 4. 只有手機版：字體稍微縮小一點點，避免折行太嚴重 */
        p, .stMarkdown, div[data-testid="stText"] {
            font-size: 14px !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --- 3. 輔助函數 ---

@st.cache_data(ttl=3600)
def get_products_data():
    try:
        return conn.read(spreadsheet=SHEET_URL, worksheet="Products")
    except Exception as e:
        st.error(f"無法讀取產品資料: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def get_brand_rules():
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="BrandRules")
        rules = {}
        for _, row in df.iterrows():
            rules[row['Brand']] = {
                'wholesale_threshold': int(row['Wholesale_Threshold']),
                'shipping_threshold': int(row['Shipping_Threshold']),
                'discount_rate': float(row['Discount'])
            }
        return rules, df
    except Exception as e:
        default_df = pd.DataFrame([{"Brand": "default", "Wholesale_Threshold": 10000, "Shipping_Threshold": 10000, "Discount": 0.7}])
        return {"default": {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}}, default_df

def get_data(worksheet):
    try:
        return conn.read(spreadsheet=SHEET_URL, worksheet=worksheet, ttl=0)
    except:
        return pd.DataFrame()

def update_data(worksheet, df):
    conn.update(spreadsheet=SHEET_URL, worksheet=worksheet, data=df)
    if worksheet == "Products":
        get_products_data.clear()
    if worksheet == "BrandRules":
        get_brand_rules.clear()

def convert_drive_url(url):
    # 1. 基礎防呆
    if pd.isna(url) or not isinstance(url, str): 
        return None
    
    url = url.strip()
    
    # 2. 嘗試解析 Google Drive ID
    file_id = None
    
    try:
        if "drive.google.com" in url:
            if "/file/d/" in url:
                # 格式: .../file/d/{FILE_ID}/view...
                file_id = url.split('/file/d/')[1].split('/')[0]
            elif "id=" in url:
                # 格式: ...?id={FILE_ID}...
                file_id = url.split('id=')[1].split('&')[0]
    except Exception:
        return None

    # 3. 使用 Google Drive Thumbnail API
    if file_id:
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"
    
    # 非 Google Drive 連結 (如 Imgur, GitHub Raw) 則直接回傳
    return url if url.startswith('http') else None

def display_status_badges(status_str):
    if pd.isna(status_str): return ""
    badges_html = ""
    keywords = {
        "已完成": "badge-done", "處理中": "badge-pending", "已出貨": "badge-logistics",
        "已部分出貨": "badge-logistics", "已付款": "badge-payment", "未付款": "badge-unpaid", "待處理": "badge-pending"
    }
    parts = str(status_str).replace("，", ",").split(",")
    for p in parts:
        p = p.strip()
        css_class = keywords.get(p, "badge-pending")
        badges_html += f'<span class="status-badge {css_class}">{p}</span>'
    return badges_html

def calculate_new_status(current_status, new_action_group, new_action_value):
    G1_LOGISTICS = ["處理中", "已出貨", "已部分出貨", "待處理"]
    G2_PAYMENT = ["未付款", "已付款"]
    
    if pd.isna(current_status): current_status = ""
    current_parts = [p.strip() for p in str(current_status).replace("，", ",").split(",") if p.strip()]
    
    if new_action_group == "G3": return "已完成"
    if "已完成" in current_parts: current_parts = [] 

    new_parts = []
    if new_action_group == "G1":
        new_parts.append(new_action_value)
        for p in current_parts:
            if p in G2_PAYMENT: new_parts.append(p)
    elif new_action_group == "G2":
        new_parts.append(new_action_value)
        for p in current_parts:
            if p in G1_LOGISTICS: new_parts.append(p)
    return ", ".join(new_parts)

# 寄送訂單確認信函式
def send_order_email(order_data, cart_items, is_update=False):
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "bluebulous.offcial@gmail.com"
    SENDER_PASSWORD = "vusc ncoh mlma dhcx"

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = order_data['Email']
    
    if is_update:
        subject_status = f"【訂單狀態更新】{order_data['Status']}"
        title_text = "訂單狀態更新通知"
    else:
        subject_status = f"【訂單確認】({order_data['Status']})"
        title_text = "訂單確認通知"

    msg['Subject'] = f"{subject_status} 訂單編號 {order_data['Order_ID']}"

    rows_html = ""
    for item in cart_items.values():
        rows_html += f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 8px;">{item['name']}<br><small style="color:#666;">{item['spec']}</small></td>
            <td style="padding: 8px; text-align: center;">x{item['qty']}</td>
            <td style="padding: 8px; text-align: right;">${item['final_subtotal']}</td>
        </tr>
        """
        
    extra_info = ""
    if order_data.get('Tracking_Number'):
        extra_info += f"<p style='margin: 5px 0;'><strong>📦 物流單號:</strong> {order_data['Tracking_Number']}</p>"
    if order_data.get('Admin_Note'):
        extra_info += f"<p style='margin: 5px 0; color: #ff5500;'><strong>📝 賣家備註:</strong> {order_data['Admin_Note']}</p>"
    if order_data.get('Extra_Discount') and int(order_data['Extra_Discount']) > 0:
        extra_info += f"<p style='margin: 5px 0; color: green;'><strong>🎁 額外折扣:</strong> -${int(order_data['Extra_Discount'])}</p>"

    html_content = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #ff5500;">Bluebulous {title_text}</h2>
                <p>親愛的 <strong>{order_data['Customer_Name']}</strong> 您好，</p>
                <p>您的訂單 <b>{order_data['Order_ID']}</b> 狀態如下。</p>
                
                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin: 20px 0;">
                    <p style="margin: 5px 0;"><strong>訂單狀態:</strong> <span style="font-size: 16px; font-weight: bold;">{order_data['Status']}</span></p>
                    <p style="margin: 5px 0;"><strong>總金額:</strong> <span style="font-size: 18px; color: #ff5500; font-weight: bold;">${order_data['Total']}</span></p>
                    {extra_info}
                </div>

                <h3>訂購明細</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <thead style="background-color: #f0f0f0;">
                        <tr>
                            <th style="padding: 8px; text-align: left;">商品</th>
                            <th style="padding: 8px; text-align: center;">數量</th>
                            <th style="padding: 8px; text-align: right;">小計</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
                <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="font-size: 12px; color: #999;">此信件為系統自動發送，請勿直接回信。<br>如有疑問請聯繫客服。</p>
            </div>
        </body>
    </html>
    """
    
    msg.attach(MIMEText(html_content, 'html'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email Error: {e}") 
        return False

# --- 4. 頁面邏輯 ---

def main_app(user):
    if 'cart' not in st.session_state: st.session_state.cart = {}
    if 'page' not in st.session_state: st.session_state.page = 'shop'
    if 'editing_order_id' not in st.session_state: st.session_state.editing_order_id = None
    if 'editing_customer_info' not in st.session_state: st.session_state.editing_customer_info = None
    
    try:
        df_products = get_products_data().dropna(how="all")
        df_products['Wholesale_Price'] = pd.to_numeric(df_products['Wholesale_Price'], errors='coerce').fillna(0)
        df_products['Retail_Price'] = pd.to_numeric(df_products['Retail_Price'], errors='coerce').fillna(0)
    except:
        st.error("無法讀取產品資料")
        return

    if 'current_product_name' not in st.session_state:
        st.session_state.current_product_name = df_products['Name'].unique()[0]

    with st.sidebar:
        # 請記得把這裡改成您真正的側邊欄 Logo 網址
        logo_url = "https://raw.githubusercontent.com/Bluebulous/product-images/main/LOGO-white-01.png"
        st.image(logo_url, use_container_width=True)
        st.markdown("<h3 style='text-align: center; color: #ffffff; margin-top: -10px;'>B2B採購系統 (Beta版)</h3>", unsafe_allow_html=True)
        st.divider()
        st.markdown(f"### Hello, {user['Contact_Person']}")
        st.caption(f"單位: {user['Dealer_Name']}")
        st.divider()
        
        # [新增] 手機版救星：側邊欄購物車摘要
        if st.session_state.cart:
            total_qty = sum(item['qty'] for item in st.session_state.cart.values())
            st.info(f"🛒 購物車內有 {total_qty} 件商品")
            if st.button("前往結帳 (查看詳情)", type="primary", use_container_width=True):
                 st.toast("請往下滑動查看完整購物車", icon="👇")
        else:
            st.caption("🛒 購物車是空的")
            
        st.divider()
        
        if st.button("🔄 重整產品資料", use_container_width=True):
            st.cache_data.clear()
            st.toast("資料已更新！正在重新載入...", icon="🔄")
            time.sleep(1)
            st.rerun()

        if st.button("開始訂購", use_container_width=True):
            st.session_state.page = 'shop'
            st.session_state.editing_order_id = None
            st.rerun()
        if st.button("歷史訂單", use_container_width=True):
            st.session_state.page = 'history'
            st.rerun()
        if st.button("個人資料", use_container_width=True):
            st.session_state.page = 'profile'
            st.rerun()
        if user['Username'] in ADMIN_USERS:
            st.markdown("---")
            if st.button("🔧 訂單管理 (Admin)", use_container_width=True):
                st.session_state.page = 'admin_orders'
                st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("登出", key="logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

        if st.session_state.page == 'shop':
            st.divider()
            st.markdown('<div class="nav-section-title">FOR DOGS</div>', unsafe_allow_html=True)
            categories = list(df_products['Category'].unique())
            selected_cat = st.radio("Category", categories, label_visibility="collapsed")
            df_filtered = df_products[df_products['Category'] == selected_cat]
            product_list = df_filtered['Name'].unique()
            if st.session_state.current_product_name not in product_list and len(product_list) > 0:
                st.session_state.current_product_name = product_list[0]
    
    # 1. 歷史訂單頁
    if st.session_state.page == 'history':
        st.title("歷史訂單")
        with st.container(border=True):
            try:
                orders = get_data("Orders")
                # [防呆補位]
                if 'Tracking_Number' not in orders.columns: orders['Tracking_Number'] = ""
                if 'Admin_Note' not in orders.columns: orders['Admin_Note'] = ""
                if 'Extra_Discount' not in orders.columns: orders['Extra_Discount'] = 0 
                orders['Extra_Discount'] = orders['Extra_Discount'].fillna(0).astype(int) # NaN轉0

                my_orders = orders[orders['Email'] == user['Username']].sort_values("Order_Time", ascending=False)
                
                if not my_orders.empty:
                    for index, row in my_orders.iterrows():
                        expander_title = f"{row['Order_Time']} | ${row['Total']}"
                        with st.expander(expander_title):
                            st.markdown(f"### 狀態: {display_status_badges(row['Status'])}", unsafe_allow_html=True)
                            st.divider()
                            c1, c2 = st.columns([1, 1])
                            with c1:
                                st.markdown(f"**訂單編號:** {row['Order_ID']}")
                                st.markdown(f"**總金額:** ${row['Total']}")
                                
                                extra_disc = int(row.get('Extra_Discount', 0))
                                if extra_disc > 0:
                                    st.markdown(f"<span style='color:green;'>**🎁 額外折扣:** -${extra_disc}</span>", unsafe_allow_html=True)

                                if pd.notna(row['Tracking_Number']) and str(row['Tracking_Number']).strip() != "":
                                    st.info(f"📦 **物流單號:** {row['Tracking_Number']}")
                                if pd.notna(row['Admin_Note']) and str(row['Admin_Note']).strip() != "":
                                    st.warning(f"📝 **賣家備註:** {row['Admin_Note']}")
                            with c2:
                                st.markdown("**訂購內容:**")
                                try:
                                    items = json.loads(row['Items_Json'])
                                    for item in items.values():
                                        st.text(f"• {item['name']} ({item['spec']}) x{item['qty']}")
                                except:
                                    st.error("內容讀取失敗")
                else:
                    st.info("目前沒有訂單紀錄")
            except Exception as e:
                st.error(f"讀取失敗: {e}")
        return

    # 2. 個人資料頁
    if st.session_state.page == 'profile':
        st.title("個人資料")
        with st.container(border=True):
            st.markdown(f"**單位:** {user['Dealer_Name']}")
            st.markdown(f"**聯絡人:** {user['Contact_Person']}")
            st.markdown(f"**Email:** {user['Username']}")
            st.markdown(f"**電話:** {user['Phone']}")
            st.markdown(f"**地址:** {user['Address']}")
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.subheader("🔒 修改密碼")
            with st.form("change_password_form"):
                current_pwd = st.text_input("目前密碼", type="password")
                new_pwd = st.text_input("新密碼", type="password")
                confirm_pwd = st.text_input("確認新密碼", type="password")
                if st.form_submit_button("更新密碼", type="primary", use_container_width=True):
                    if str(current_pwd) != str(user['Password']):
                        st.error("❌ 目前密碼輸入錯誤")
                    elif new_pwd != confirm_pwd:
                        st.error("❌ 兩次新密碼輸入不一致")
                    elif not new_pwd:
                        st.error("❌ 新密碼不得為空")
                    else:
                        try:
                            users_df = get_data("Users")
                            user_index = users_df[users_df['Username'] == user['Username']].index
                            if not user_index.empty:
                                idx = user_index[0]
                                users_df.at[idx, 'Password'] = new_pwd
                                update_data("Users", users_df)
                                st.session_state['user']['Password'] = new_pwd
                                st.success("✅ 密碼修改成功！")
                            else: st.error("❌ 找不到使用者資料")
                        except Exception as e: st.error(f"❌ 更新失敗: {e}")
        return

    # 4. 管理員後台
    if st.session_state.page == 'admin_orders':
        if user['Username'] not in ADMIN_USERS:
            st.warning("您沒有權限訪問此頁面")
            st.session_state.page = 'shop'
            st.rerun()
            return

        st.title("🔧 管理員後台")
        tab1, tab2 = st.tabs(["📦 訂單管理", "⚙️ 品牌門檻設定"])
        
        with tab1:
            with st.container(border=True):
                try:
                    orders = get_data("Orders")
                    # [防呆補位]
                    if 'Tracking_Number' not in orders.columns: orders['Tracking_Number'] = ""
                    if 'Admin_Note' not in orders.columns: orders['Admin_Note'] = ""
                    if 'Extra_Discount' not in orders.columns: orders['Extra_Discount'] = 0
                    
                    # 強制處理 NaN
                    orders['Extra_Discount'] = orders['Extra_Discount'].fillna(0).astype(int)

                    if not orders.empty:
                        all_orders = orders.sort_values("Order_Time", ascending=False)
                        st.markdown(f"共 {len(all_orders)} 筆訂單")
                        
                        for index, row in all_orders.iterrows():
                            status_badges = display_status_badges(row['Status'])
                            expander_title = f"{row['Order_Time']} - {row['Customer_Name']} (${row['Total']})"
                            
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
                                        items = json.loads(row['Items_Json'])
                                        for item in items.values():
                                            st.text(f"• {item['name']} ({item['spec']}) x{item['qty']}")
                                    except: st.error("JSON 解析失敗")
                                with c3:
                                    st.markdown(f"**小計:** ${row['Subtotal']}")
                                    st.markdown(f"**稅金:** ${row['Tax']}")
                                    st.markdown(f"**運費:** ${row['Shipping']}")
                                    extra_disc_show = int(row.get('Extra_Discount', 0))
                                    if extra_disc_show > 0:
                                        st.markdown(f"<span style='color:green'>**折扣:** -${extra_disc_show}</span>", unsafe_allow_html=True)
                                    st.markdown(f"### Total: ${row['Total']}")
                                    
                                    if st.button("✏️ 修改內容 (進入購物車)", key=f"admin_edit_{row['Order_ID']}", type="primary"):
                                        st.session_state.cart = json.loads(row['Items_Json'])
                                        st.session_state.editing_order_id = row['Order_ID']
                                        st.session_state.editing_customer_info = {
                                            "Customer_Name": row['Customer_Name'], "Email": row['Email'], "Phone": row['Phone']
                                        }
                                        st.session_state.page = 'shop'
                                        st.rerun()

                                st.divider()
                                st.markdown("#### 📝 訂單資訊 (物流/備註/折扣)")
                                track_key = f"track_{row['Order_ID']}"
                                note_key = f"note_{row['Order_ID']}"
                                disc_key = f"disc_{row['Order_ID']}"
                                
                                ic1, ic2, ic3, ic4 = st.columns([2, 3, 1.5, 1], vertical_alignment="bottom")
                                new_track = ic1.text_input("物流單號", value=str(row['Tracking_Number']) if pd.notna(row['Tracking_Number']) else "", key=track_key)
                                new_note = ic2.text_area("備註 (買家可見)", value=str(row['Admin_Note']) if pd.notna(row['Admin_Note']) else "", key=note_key, height=100)
                                new_discount = ic3.number_input("額外折扣 (扣減金額)", min_value=0, value=int(row.get('Extra_Discount', 0)), key=disc_key)
                                
                                if ic4.button("💾 儲存資訊", key=f"save_info_{row['Order_ID']}"):
                                    try:
                                        df_curr = get_data("Orders")
                                        t_idx = df_curr[df_curr['Order_ID'] == row['Order_ID']].index
                                        if not t_idx.empty:
                                            idx = t_idx[0]
                                            df_curr.at[idx, 'Tracking_Number'] = new_track
                                            df_curr.at[idx, 'Admin_Note'] = new_note
                                            df_curr.at[idx, 'Extra_Discount'] = new_discount
                                            
                                            org_sub = df_curr.at[idx, 'Subtotal']
                                            org_tax = df_curr.at[idx, 'Tax']
                                            org_ship = df_curr.at[idx, 'Shipping']
                                            new_total = org_sub + org_tax + org_ship - new_discount
                                            df_curr.at[idx, 'Total'] = new_total
                                            
                                            update_data("Orders", df_curr)
                                            st.toast("✅ 資訊已儲存並重新計算總額！")
                                            time.sleep(1)
                                            st.rerun()
                                    except Exception as e:
                                        st.error(f"儲存失敗: {e}")
                                
                                st.divider()
                                st.markdown("#### 🔄 更新訂單狀態")
                                st.caption("點擊按鈕將會：1. 更新狀態(疊加) 2. 儲存上方資訊 3. 寄信通知客戶")
                                
                                g1_col, g2_col, g3_col = st.columns([3, 2, 1])
                                
                                def handle_status_click(oid, new_group, new_value, track, note, discount, email, cust_name, items_json, current_status):
                                    try:
                                        final_status = calculate_new_status(current_status, new_group, new_value)
                                        df_curr = get_data("Orders")
                                        t_idx = df_curr[df_curr['Order_ID'] == oid].index
                                        if not t_idx.empty:
                                            idx = t_idx[0]
                                            df_curr.at[idx, 'Status'] = final_status
                                            df_curr.at[idx, 'Tracking_Number'] = track
                                            df_curr.at[idx, 'Admin_Note'] = note
                                            df_curr.at[idx, 'Extra_Discount'] = discount
                                            
                                            org_sub = df_curr.at[idx, 'Subtotal']
                                            org_tax = df_curr.at[idx, 'Tax']
                                            org_ship = df_curr.at[idx, 'Shipping']
                                            new_total = org_sub + org_tax + org_ship - discount
                                            df_curr.at[idx, 'Total'] = new_total

                                            update_data("Orders", df_curr)
                                            st.success(f"狀態已更新為：[{final_status}]")
                                            
                                            o_data = {
                                                "Order_ID": oid, "Customer_Name": cust_name,
                                                "Email": email, "Status": final_status,
                                                "Total": new_total, "Tracking_Number": track, "Admin_Note": note,
                                                "Extra_Discount": discount
                                            }
                                            c_items = json.loads(items_json)
                                            send_order_email(o_data, c_items, is_update=True)
                                            time.sleep(1)
                                            st.rerun()
                                    except Exception as e: st.error(f"更新失敗: {e}")

                                with g1_col:
                                    st.markdown("**物流狀態 (藍)**")
                                    c_b1, c_b2, c_b3 = st.columns(3)
                                    if c_b1.button("處理中", key=f"s_proc_{row['Order_ID']}"):
                                        handle_status_click(row['Order_ID'], "G1", "處理中", new_track, new_note, new_discount, row['Email'], row['Customer_Name'], row['Items_Json'], row['Status'])
                                    if c_b2.button("已出貨", key=f"s_ship_{row['Order_ID']}"):
                                        handle_status_click(row['Order_ID'], "G1", "已出貨", new_track, new_note, new_discount, row['Email'], row['Customer_Name'], row['Items_Json'], row['Status'])
                                    if c_b3.button("部分出貨", key=f"s_part_{row['Order_ID']}"):
                                        handle_status_click(row['Order_ID'], "G1", "已部分出貨", new_track, new_note, new_discount, row['Email'], row['Customer_Name'], row['Items_Json'], row['Status'])
                                with g2_col:
                                    st.markdown("**金流狀態 (綠/紅)**")
                                    c_b4, c_b5 = st.columns(2)
                                    if c_b4.button("未付款", key=f"s_unpaid_{row['Order_ID']}"):
                                        handle_status_click(row['Order_ID'], "G2", "未付款", new_track, new_note, new_discount, row['Email'], row['Customer_Name'], row['Items_Json'], row['Status'])
                                    if c_b5.button("已付款", key=f"s_paid_{row['Order_ID']}"):
                                        handle_status_click(row['Order_ID'], "G2", "已付款", new_track, new_note, new_discount, row['Email'], row['Customer_Name'], row['Items_Json'], row['Status'])
                                with g3_col:
                                    st.markdown("**結案 (灰)**")
                                    if st.button("已完成", key=f"s_done_{row['Order_ID']}"):
                                        handle_status_click(row['Order_ID'], "G3", "已完成", new_track, new_note, new_discount, row['Email'], row['Customer_Name'], row['Items_Json'], row['Status'])
                    else: st.info("目前無任何訂單")
                except Exception as e: st.error(f"讀取失敗: {e}")

        with tab2:
            st.subheader("設定各品牌門檻與折扣")
            st.info("💡 Wholesale_Threshold: 批發門檻 | Shipping_Threshold: 免運門檻 | Discount: 零售折扣")
            _, df_rules = get_brand_rules()
            edited_df = st.data_editor(
                df_rules, num_rows="dynamic",
                column_config={
                    "Brand": st.column_config.TextColumn("品牌", required=True),
                    "Wholesale_Threshold": st.column_config.NumberColumn("批發門檻", min_value=0, format="$%d"),
                    "Shipping_Threshold": st.column_config.NumberColumn("免運門檻", min_value=0, format="$%d"),
                    "Discount": st.column_config.NumberColumn("折扣 (0.1~1.0)", min_value=0.1, max_value=1.0, step=0.05)
                }, use_container_width=True, key="brand_rules_editor"
            )
            if st.button("💾 儲存設定", type="primary"):
                try:
                    update_data("BrandRules", edited_df)
                    st.success("設定已更新！")
                    get_brand_rules.clear()
                    time.sleep(1)
                    st.rerun()
                except Exception as e: st.error(f"儲存失敗: {e}")
        return

    # 3. 商店頁
    col_visual, col_select, col_cart = st.columns([1.8, 1.8, 1.4], gap="medium")
    current_name = st.session_state.current_product_name
    current_product_data = df_products[df_products['Name'] == current_name]

    with col_select:
        with st.container(border=True):
            # [修改] 使用 HTML 來設定精確的字體大小 (font-size: 20px)
            st.markdown(f"<div style='font-size: 20px; font-weight: bold; margin-bottom: 10px;'>{current_name}</div>", unsafe_allow_html=True)
            
            st.caption(f"Brand: {current_product_data.iloc[0]['Brand']}")
            st.markdown("---")
            available_colors = current_product_data['Color'].unique()
            selected_color = st.selectbox("顏色", available_colors, key=f"color_sel_{current_name}")
            variants = current_product_data[current_product_data['Color'] == selected_color]
            st.markdown("<br>", unsafe_allow_html=True)
            h1, h2, h3, h4, h5 = st.columns([1.2, 2.2, 1.5, 1.5, 1.5], vertical_alignment="center")
            h1.markdown("**尺寸**")
            h2.markdown("**數量**")
            h3.markdown("**批發價**\n(未稅)")
            h4.markdown("**零售價**\n(含稅)")
            h5.markdown("") 

            def add_to_cart_callback(p_id, p_name, p_spec, p_w, p_r, q_key, p_brand):
                qty = st.session_state[q_key]
                if qty <= 0: return
                if p_id in st.session_state.cart:
                    st.session_state.cart[p_id]['qty'] += qty
                else:
                    st.session_state.cart[p_id] = {
                        "id": p_id, "name": p_name, "spec": p_spec,
                        "wholesale_price": int(p_w), "retail_price": int(p_r),
                        "brand": p_brand, "qty": qty
                    }
                st.toast(f"已加入 {p_name} x {qty}", icon="🛒")
                st.session_state[q_key] = 1

            for i, (_, sku) in enumerate(variants.iterrows()):
                c_row = st.container()
                c1, c2, c3, c4, c5 = c_row.columns([1.2, 2.2, 1.5, 1.5, 1.5], vertical_alignment="center")
                with c1: st.markdown(f"<div style='font-weight:bold;'>{sku['Size']}</div>", unsafe_allow_html=True)
                with c2:
                    qty_key = f"qty_input_{sku['Product_ID']}_{selected_color}_{i}"
                    st.number_input("Qty", min_value=1, value=1, step=1, key=qty_key, label_visibility="collapsed")
                with c3: st.markdown(f"<div style='color:#ff5500; font-weight:bold;'>${int(sku['Wholesale_Price'])}</div>", unsafe_allow_html=True)
                with c4: st.markdown(f"<div style='color:#666;'>${int(sku['Retail_Price'])}</div>", unsafe_allow_html=True)
                with c5:
                    st.button("ADD", key=f"add_{sku['Product_ID']}_{selected_color}_{i}", type="primary", use_container_width=True,
                        on_click=add_to_cart_callback,
                        args=(sku['Product_ID'], current_name, f"{selected_color} / {sku['Size']}", sku['Wholesale_Price'], sku['Retail_Price'], qty_key, current_product_data.iloc[0]['Brand']))

    with col_visual:
        with st.container(border=True):
            img_row = current_product_data[current_product_data['Color'] == selected_color]
            if img_row.empty: img_row = current_product_data.iloc[0]
            else: img_row = img_row.iloc[0]
            main_img = convert_drive_url(img_row['Image_URL'])
            if main_img: st.image(main_img, use_container_width=True)
            else: st.warning("No Image")
            st.markdown("<br><h4>Related Products / 同系列商品</h4>", unsafe_allow_html=True)
            current_category = current_product_data.iloc[0]['Category']
            same_category_products = df_products[df_products['Category'] == current_category]['Name'].unique()
            others = [p for p in same_category_products if p != current_name]
            for i in range(0, len(others), 3):
                cols = st.columns(3)
                batch = others[i:i+3]
                for idx, other_prod in enumerate(batch):
                    row = df_products[df_products['Name'] == other_prod].iloc[0]
                    thumb = convert_drive_url(row['Image_URL'])
                    with cols[idx]:
                        with st.container(border=True):
                            if thumb: 
                                st.image(thumb, use_container_width=True)
                            else:
                                st.markdown("<div style='height: 150px; background-color: #f0f0f0; display: flex; align-items: center; justify-content: center; color: #666;'>No Image</div>", unsafe_allow_html=True)
                            
                            # [修改] 這裡的按鈕字體大小已經透過上方的 CSS 設定變大了 (16px)
                            if st.button(f" {other_prod}", key=f"view_{other_prod}_{i}_{idx}", use_container_width=True):
                                st.session_state.current_product_name = other_prod
                                st.rerun()
            if not others: st.caption("此分類下無其他商品")

    with col_cart:
        with st.container(border=True):
            # [修改] 購物車標題字體大小設定 (20px)
            st.markdown("<h3 style='font-size: 20px; font-weight: bold;'>🛒 購物車</h3>", unsafe_allow_html=True)
            st.divider()
            if st.session_state.cart:
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
                    brand_groups[b_name]['raw_wholesale_total'] += item['wholesale_price'] * item['qty']

                is_order_free_shipping = False 
                grand_total_subtotal = 0
                grand_total_tax = 0
                
                for b_name, data in brand_groups.items():
                    safe_default = {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}
                    rule = BRAND_RULES.get(b_name, BRAND_RULES.get("default", safe_default))
                    w_threshold = rule.get('wholesale_threshold', 10000)
                    s_threshold = rule.get('shipping_threshold', 10000)
                    discount = rule.get('discount_rate', 0.7)
                    
                    if data['raw_wholesale_total'] >= w_threshold:
                        data['is_wholesale_qualified'] = True
                        brand_subtotal = data['raw_wholesale_total']
                        brand_tax = int(brand_subtotal * TAX_RATE)
                    else:
                        data['is_wholesale_qualified'] = False
                        brand_subtotal = 0
                        brand_tax = 0
                        for item in data['items']:
                            brand_subtotal += int(item['retail_price'] * discount) * item['qty']

                    if data['raw_wholesale_total'] >= s_threshold:
                        data['is_shipping_qualified'] = True
                        is_order_free_shipping = True
                    
                    grand_total_subtotal += brand_subtotal
                    grand_total_tax += brand_tax
                    for item in data['items']:
                        if data['is_wholesale_qualified']: item['final_unit_price'] = item['wholesale_price']
                        else: item['final_unit_price'] = int(item['retail_price'] * discount)
                        item['final_subtotal'] = item['final_unit_price'] * item['qty']

                if is_order_free_shipping:
                    shipping = 0
                    shipping_msg = "✅ 符合免運資格"
                else:
                    shipping = SHIPPING_FEE
                    shipping_msg = f"運費 ${SHIPPING_FEE}"

                grand_total = grand_total_subtotal + grand_total_tax + shipping

                for b_name, data in brand_groups.items():
                    safe_default = {"wholesale_threshold": 10000, "shipping_threshold": 10000, "discount_rate": 0.7}
                    rule = BRAND_RULES.get(b_name, BRAND_RULES.get("default", safe_default))
                    w_icon = "🟢" if data['is_wholesale_qualified'] else "🟠"
                    s_icon = "🚚" if data['is_shipping_qualified'] else ""
                    d_rate = rule.get('discount_rate', 0.7)
                    price_text = "批發價" if data['is_wholesale_qualified'] else f"零售{int(d_rate*10)}折"
                    caption_text = f"{w_icon} **{b_name}** | ${data['raw_wholesale_total']} (門檻:${rule.get('wholesale_threshold', 10000)}) -> {price_text} {s_icon}"
                    st.caption(caption_text)
                    for item in data['items']:
                        c_ctrl, c_sub = st.columns([2, 1], vertical_alignment="center")
                        with c_ctrl:
                            st.markdown(f"{item['name']} <span style='color:#888; font-size:12px;'>({item['spec']})</span>", unsafe_allow_html=True)
                            bc1, bc2, bc3, bc4 = st.columns([0.8, 0.8, 0.8, 0.8], gap="small", vertical_alignment="center")
                            if bc1.button("▬▬", key=f"cart_min_{item['id']}", type="secondary"):
                                item['qty'] -= 1
                                if item['qty'] <= 0: del st.session_state.cart[item['id']]
                                st.rerun()
                            bc2.markdown(f"<div style='text-align:center; font-size:14px;'>{item['qty']}</div>", unsafe_allow_html=True)
                            if bc3.button("╋", key=f"cart_plus_{item['id']}", type="secondary"):
                                item['qty'] += 1
                                st.rerun()
                            if bc4.button("✖", key=f"cart_del_{item['id']}", type="secondary", help="移除此商品"):
                                del st.session_state.cart[item['id']]
                                st.rerun()
                        with c_sub: st.markdown(f"<div style='text-align:right; font-weight:bold;'>${item['final_subtotal']}</div>", unsafe_allow_html=True)
                    st.divider()
                
                r1, r2 = st.columns(2)
                r1.text("小計 (Subtotal)")
                r2.text(f"${grand_total_subtotal}")
                r1.text("稅金 (Tax)")
                r2.text(f"${grand_total_tax}")
                r1.text("運費 (Shipping)")
                r2.text(shipping_msg)
                r1.markdown("#### 總計(含稅)")
                r2.markdown(f"#### ${grand_total}")
                
                if is_order_free_shipping: st.info("🎉 訂單已享免運優惠！")
                else: st.warning(f"⚠️ 全單未達免運標準，需付運費 ${SHIPPING_FEE}")
                
                is_editing = st.session_state.get('editing_order_id') is not None
                if is_editing:
                    btn_text = "💾 確認修改並儲存 (Admin Update)"
                    client_name = st.session_state.get('editing_customer_info', {}).get('Customer_Name', 'Unknown')
                    st.warning(f"🔧 正在修改客戶 [{client_name}] 的訂單：{st.session_state.editing_order_id}")
                else: btn_text = "CHECKOUT / 送出訂單"

                if st.button(btn_text, type="primary", use_container_width=True):
                    if is_editing:
                        order_id = st.session_state.editing_order_id
                        saved_info = st.session_state.get('editing_customer_info', {})
                        c_name = saved_info.get('Customer_Name', user['Dealer_Name'])
                        c_email = saved_info.get('Email', user['Username'])
                        c_phone = saved_info.get('Phone', user['Phone'])
                        c_status = "賣方已修改"
                    else:
                        order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                        c_name = user['Dealer_Name']
                        c_email = user['Username']
                        c_phone = user['Phone']
                        c_status = "待處理"

                    final_cart_data = st.session_state.cart.copy()
                    order_data = {
                        "Order_ID": order_id, "Order_Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Customer_Name": c_name, "Email": c_email, "Phone": c_phone,
                        "Items_Json": json.dumps(final_cart_data, ensure_ascii=False),
                        "Subtotal": grand_total_subtotal, "Tax": grand_total_tax, 
                        "Shipping": shipping, "Total": grand_total, "Status": c_status,
                        "Extra_Discount": 0 
                    }
                    if 'Tracking_Number' not in order_data: order_data['Tracking_Number'] = ""
                    if 'Admin_Note' not in order_data: order_data['Admin_Note'] = ""

                    try:
                        old_orders = get_data("Orders")
                        if is_editing:
                            target_idx = old_orders[old_orders['Order_ID'] == order_id].index
                            if not target_idx.empty:
                                idx = target_idx[0]
                                for key, value in order_data.items():
                                    old_orders.at[idx, key] = value
                                update_data("Orders", old_orders)
                                st.success(f"訂單 {order_id} 修改完成！")
                                with st.spinner("正在寄送通知信給客戶..."):
                                    if send_order_email(order_data, final_cart_data, is_update=True):
                                        st.toast("📧 確認信已寄出！", icon="✅")
                                    else: st.error("信件寄送失敗")
                            else: st.error("找不到原始訂單")
                        else:
                            updated = pd.concat([old_orders, pd.DataFrame([order_data])], ignore_index=True)
                            update_data("Orders", updated)
                            st.success(f"訂單 {order_id} 已送出!")
                            with st.spinner("正在寄送確認信..."):
                                if send_order_email(order_data, final_cart_data):
                                    st.toast("📧 確認信已寄出！", icon="✅")
                                else: st.warning("訂單已成立，但信件寄送失敗")

                        st.session_state.cart = {}
                        st.session_state.editing_order_id = None
                        st.session_state.editing_customer_info = None
                        time.sleep(1)
                        if user['Username'] in ADMIN_USERS: st.session_state.page = 'admin_orders'
                        else: st.session_state.page = 'shop'
                        st.rerun()
                    except Exception as e: st.error(f"訂單處理失敗: {e}")
            else: st.info("購物車是空的")

def login_page():
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.markdown("<br><br><br>", unsafe_allow_html=True)
        st.markdown("## Bluebulous B2B 採購系統")
        # [新增] 登入頁面提示
        st.warning("💡 建議使用 筆電 / 桌機 登入以獲得最佳體驗")
        with st.form("login"):
            u = st.text_input("Username / Email")
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Login", use_container_width=True, type="primary"):
                users = get_data("Users")
                match = users[users['Username'] == u]
                if not match.empty and str(match.iloc[0]['Password']) == p:
                    st.session_state['user'] = match.iloc[0]
                    st.rerun()
                else: st.error("帳號或密碼錯誤")

if __name__ == "__main__":
    if 'user' not in st.session_state:
        login_page()
    else:
        main_app(st.session_state['user'])