import html

import pandas as pd


EXPECTED_COLS = {
    'product_id': 'Product_ID', 'name': 'Name', 'category': 'Category', 'brand': 'Brand', 'color': 'Color', 'size': 'Size',
    'wholesale_price': 'Wholesale_Price', 'retail_price': 'Retail_Price', 'image_url': 'Image_URL',
    'wholesale_threshold': 'Wholesale_Threshold', 'shipping_threshold': 'Shipping_Threshold', 'discount': 'Discount',
    'username': 'Username', 'password': 'Password', 'dealer_name': 'Dealer_Name', 'contact_person': 'Contact_Person',
    'phone': 'Phone', 'address': 'Address', 'contact_email': 'Contact_Email', 'allowed_brands': 'Allowed_Brands',
    'shopline_sku': 'Shopline_SKU', 'shopline_product_id': 'Shopline_Product_ID',
    'shopline_stock': 'Shopline_Stock', 'current_stock': 'Current_Stock', 'stock': 'Stock',
    'inventory': 'Inventory', 'inventory_qty': 'Inventory_Qty', 'available_quantity': 'Available_Quantity',
    'quantity': 'Quantity', 'qty': 'Qty', 'on_hand': 'On_Hand', 'on_hand_qty': 'On_Hand_Qty',
    'stock_updated_at': 'Stock_Updated_At', 'restock_qty': 'Restock_Qty',
    'expected_arrival_date': 'Expected_Arrival_Date', 'restock_date': 'Restock_Date',
    'order_id': 'Order_ID', 'order_time': 'Order_Time', 'customer_name': 'Customer_Name', 'email': 'Email',
    'items_json': 'Items_Json', 'subtotal': 'Subtotal', 'tax': 'Tax', 'shipping': 'Shipping', 'extra_discount': 'Extra_Discount',
    'total': 'Total', 'status': 'Status', 'tracking_number': 'Tracking_Number', 'admin_note': 'Admin_Note',
    'message': 'Message',
    'time': 'Time', 'dealer': 'Dealer', 'action': 'Action', 'details': 'Details'
}


def format_df_cols(df):
    if df.empty:
        return df
    rename_map = {col: EXPECTED_COLS.get(col.lower(), col) for col in df.columns}
    return df.rename(columns=rename_map)


def escape_html(value):
    return html.escape("" if pd.isna(value) else str(value), quote=True)


def safe_number(value, default=0):
    parsed = pd.to_numeric(value, errors='coerce')
    if pd.isna(parsed):
        return default
    return parsed


def convert_drive_url(url):
    if pd.isna(url) or not isinstance(url, str):
        return None
    url = url.strip()
    file_id = None
    try:
        if "drive.google.com" in url:
            if "/file/d/" in url:
                file_id = url.split('/file/d/')[1].split('/')[0]
            elif "id=" in url:
                file_id = url.split('id=')[1].split('&')[0]
    except Exception:
        return None
    if file_id:
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"
    return url if url.startswith('http') else None


def display_status_badges(status_str):
    if pd.isna(status_str):
        return ""
    badges_html = ""
    keywords = {
        "已完成": "badge-done", "處理中": "badge-pending", "已出貨": "badge-logistics",
        "已部分出貨": "badge-logistics", "已付款": "badge-payment", "未付款": "badge-unpaid", "待處理": "badge-pending"
    }
    parts = str(status_str).replace("，", ",").split(",")
    for p in parts:
        p = p.strip()
        css_class = keywords.get(p, "badge-pending")
        badges_html += f'<span class="status-badge {css_class}">{escape_html(p)}</span>'
    return badges_html
