import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import streamlit as st

from utils.formatting import escape_html


SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "bluebulous.official@gmail.com"


def send_order_email(order_data, cart_items, is_update=False):
    try:
        sender_password = os.environ.get("SMTP_PASSWORD") or st.secrets["SMTP_PASSWORD"]
    except KeyError:
        print("Error: SMTP_PASSWORD 尚未設定")
        return False

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = order_data['Email']
    msg['Bcc'] = SENDER_EMAIL

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
            <td style="padding: 8px;">{escape_html(item.get('name', ''))}<br><small style="color:#666;">{escape_html(item.get('spec', ''))}</small></td>
            <td style="padding: 8px; text-align: center;">x{escape_html(item.get('qty', ''))}</td>
            <td style="padding: 8px; text-align: right;">${escape_html(item.get('final_subtotal', ''))}</td>
        </tr>
        """

    extra_info = ""
    if order_data.get('Tracking_Number'):
        extra_info += f"<p style='margin: 5px 0;'><strong>📦 物流單號:</strong> {escape_html(order_data['Tracking_Number'])}</p>"
    if order_data.get('Admin_Note'):
        extra_info += f"<p style='margin: 5px 0; color: #ff5500;'><strong>📝 賣家備註:</strong> {escape_html(order_data['Admin_Note'])}</p>"
    if order_data.get('Extra_Discount') and int(order_data['Extra_Discount']) != 0:
        extra_val = int(order_data['Extra_Discount'])
        sign = "-" if extra_val > 0 else "+"
        color = "green" if extra_val > 0 else "red"
        extra_info += f"<p style='margin: 5px 0; color: {color};'><strong>🎁 額外調整:</strong> {sign}${abs(extra_val)}</p>"

    html_content = f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #ff5500;">Bluebulous {title_text}</h2>
                <p>親愛的 <strong>{escape_html(order_data['Customer_Name'])}</strong> 您好，</p>
                <p>您的訂單 <b>{escape_html(order_data['Order_ID'])}</b> 狀態如下。</p>

                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin: 20px 0;">
                    <p style="margin: 5px 0;"><strong>訂單狀態:</strong> <span style="font-size: 16px; font-weight: bold;">{escape_html(order_data['Status'])}</span></p>
                    <p style="margin: 5px 0;"><strong>總金額:</strong> <span style="font-size: 18px; color: #ff5500; font-weight: bold;">${escape_html(order_data['Total'])}</span></p>
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
        server.login(SENDER_EMAIL, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email Error: {e}")
        return False
