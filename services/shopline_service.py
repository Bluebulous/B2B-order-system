import os
from typing import Any

import pandas as pd
import requests
import streamlit as st


SHOPLINE_API_BASE_URL = (
    os.environ.get("SHOPLINE_API_BASE_URL")
    or st.secrets.get("SHOPLINE_API_BASE_URL", "https://open.shopline.io")
).rstrip("/")
SHOPLINE_API_TOKEN = os.environ.get("SHOPLINE_API_TOKEN") or st.secrets.get("SHOPLINE_API_TOKEN", "")


def is_shopline_configured():
    return bool(SHOPLINE_API_TOKEN)


def _headers():
    return {
        "Authorization": f"Bearer {SHOPLINE_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(path, params=None):
    if not is_shopline_configured():
        raise RuntimeError("尚未設定 SHOPLINE_API_TOKEN")

    url = f"{SHOPLINE_API_BASE_URL}{path}"
    response = requests.get(url, headers=_headers(), params=params or {}, timeout=30)
    response.raise_for_status()
    return response.json()


def _list_from_payload(payload: Any):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "products", "warehouses", "stocks", "variants", "variations"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _list_from_payload(value)
            if nested:
                return nested
    return []


def _text(value):
    if isinstance(value, dict):
        for key in ("zh-hant", "zh-hans", "zh_tw", "zh_cn", "en", "default"):
            if value.get(key):
                return str(value[key]).strip()
        for item in value.values():
            if item:
                return str(item).strip()
        return ""
    if isinstance(value, list):
        return " / ".join(_text(item) for item in value if _text(item))
    if pd.isna(value):
        return ""
    return str(value).strip()


def fetch_products(page=1, per_page=50):
    payload = _request("/v1/products", {"page": page, "per_page": per_page})
    return payload, _list_from_payload(payload)


def fetch_warehouses(page=1, per_page=50):
    payload = _request("/v1/warehouses", {"page": page, "per_page": per_page})
    return payload, _list_from_payload(payload)


def fetch_product_stocks(product_id):
    payload = _request(f"/v1/products/{product_id}/stocks")
    return payload, _list_from_payload(payload)


def normalize_warehouses(warehouses):
    rows = []
    for warehouse in warehouses:
        if not isinstance(warehouse, dict):
            continue
        rows.append({
            "Warehouse_ID": _text(warehouse.get("id") or warehouse.get("_id") or warehouse.get("warehouse_id")),
            "Warehouse": _text(warehouse.get("name") or warehouse.get("title")),
            "Type": _text(warehouse.get("type") or warehouse.get("warehouse_type")),
            "Raw": warehouse,
        })
    return pd.DataFrame(rows)


def normalize_products(products):
    rows = []
    for product in products:
        if not isinstance(product, dict):
            continue

        product_id = _text(product.get("id") or product.get("_id") or product.get("product_id"))
        product_name = _text(product.get("title") or product.get("name") or product.get("seo_title"))
        status = _text(product.get("status") or product.get("state"))
        variants = []
        for key in ("variants", "variations", "skus", "product_variations"):
            value = product.get(key)
            if isinstance(value, list):
                variants = value
                break

        if not variants:
            rows.append({
                "Shopline_Product_ID": product_id,
                "Shopline_Name": product_name,
                "Shopline_SKU": _text(product.get("sku")),
                "Variant": "",
                "Status": status,
                "Raw": product,
            })
            continue

        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_name = _text(
                variant.get("title")
                or variant.get("name")
                or variant.get("option")
                or variant.get("options")
            )
            rows.append({
                "Shopline_Product_ID": product_id,
                "Shopline_Name": product_name,
                "Shopline_SKU": _text(variant.get("sku") or variant.get("barcode") or product.get("sku")),
                "Variant": variant_name,
                "Status": status,
                "Raw": variant,
            })
    return pd.DataFrame(rows)


def _collect_stock_records(value, records):
    if isinstance(value, list):
        for item in value:
            _collect_stock_records(item, records)
        return
    if not isinstance(value, dict):
        return

    keys = set(value.keys())
    stock_keys = {"quantity", "qty", "stock", "stocks", "inventory_quantity", "available_quantity"}
    id_keys = {"sku", "variant_id", "variation_id", "warehouse_id", "product_id"}
    if keys.intersection(stock_keys) or keys.intersection(id_keys):
        records.append(value)

    for item in value.values():
        if isinstance(item, (dict, list)):
            _collect_stock_records(item, records)


def normalize_stocks(product_id, product_name, stocks_payload):
    records = []
    _collect_stock_records(stocks_payload, records)
    rows = []
    seen = set()
    for record in records:
        sku = _text(record.get("sku") or record.get("barcode"))
        warehouse = _text(record.get("warehouse_name") or record.get("warehouse") or record.get("warehouse_id"))
        qty_value = (
            record.get("inventory_quantity")
            if record.get("inventory_quantity") is not None
            else record.get("available_quantity")
            if record.get("available_quantity") is not None
            else record.get("quantity")
            if record.get("quantity") is not None
            else record.get("qty")
            if record.get("qty") is not None
            else record.get("stock")
        )
        row_key = (sku, warehouse, str(qty_value), _text(record.get("variant_id") or record.get("variation_id")))
        if row_key in seen:
            continue
        seen.add(row_key)
        rows.append({
            "Shopline_Product_ID": product_id,
            "Shopline_Name": product_name,
            "Shopline_SKU": sku,
            "Variant_ID": _text(record.get("variant_id") or record.get("variation_id")),
            "Warehouse": warehouse,
            "Stock": qty_value,
        })
    return pd.DataFrame(rows)
