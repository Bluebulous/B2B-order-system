import os

TAX_RATE = 0.05
SHIPPING_FEE = 125

DEFAULT_ADMIN_USERS = {"admin", "bluebulous", "test@test.com"}
ADMIN_USERS = {
    user.strip()
    for user in os.environ.get("ADMIN_USERS", ",".join(DEFAULT_ADMIN_USERS)).split(",")
    if user.strip()
}

ENABLE_DEFAULT_ADMIN = os.environ.get("ENABLE_DEFAULT_ADMIN", "").lower() in {"1", "true", "yes"}

