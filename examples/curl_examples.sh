#!/usr/bin/env bash
# ============================================================================
#  Optimus Payment Gateway  —  REST API from the shell (curl + openssl)
# ============================================================================
#  Copy-paste friendly. Shows how to:
#     1. sign a create-order request (HMAC-SHA256, exactly like the server checks)
#     2. POST it to create a payment
#     3. GET the order status
#     4. verify an inbound webhook signature
#
#  Only `curl` and `openssl` are required (both ship on macOS/Linux; on Windows
#  use Git Bash / WSL).
#
#  Start the gateway first:  python run.py serve
# ============================================================================
set -euo pipefail

# --- configure (match your .env) --------------------------------------------
BASE_URL="${OPG_BASE_URL:-http://localhost:8000}"
API_KEY="${OPG_MERCHANT_API_KEY:-change-me-public-key}"
API_SECRET="${OPG_MERCHANT_API_SECRET:-change-me-long-random-secret}"

# --- the order fields -------------------------------------------------------
METHOD="usdt_bep20"
AMOUNT="25.00"                       # <-- keep this a STRING (see note below)
ORDER_ID="INV-$(date +%s)"
NOTIFY_URL="https://shop.example/webhook"

# ----------------------------------------------------------------------------
#  Signing  (optimus_gateway.security.sign_params)
#  ---------------------------------------------------------------------------
#  The server signs the JSON-*parsed* request body: it takes every field EXCEPT
#  `signature`/`sign` (and drops empty values), sorts them by key, joins them as
#  "k=v" with "&", and HMAC-SHA256s that with your API secret.
#
#  So we must build the SAME string here. Keys below are already in alphabetical
#  order: amount, api_key, method, notify_url, order_id.
#
#  IMPORTANT — send numbers as STRINGS. The server signs what JSON parses to, and
#  `25.00` parses to the float 25.0 => it would sign "amount=25.0", breaking a
#  signature you computed over "amount=25.00". Sending the JSON string "25.00"
#  keeps both sides byte-identical. (If auth is disabled server-side — no
#  OPG_MERCHANT_API_KEY set — the signature is ignored and you can skip it.)
# ----------------------------------------------------------------------------
SIGN_BASE="amount=${AMOUNT}&api_key=${API_KEY}&method=${METHOD}&notify_url=${NOTIFY_URL}&order_id=${ORDER_ID}"
SIGNATURE="$(printf '%s' "$SIGN_BASE" | openssl dgst -sha256 -hmac "$API_SECRET" | awk '{print $NF}')"

echo "sign base : $SIGN_BASE"
echo "signature : $SIGNATURE"
echo

# ----------------------------------------------------------------------------
#  1) Create a payment
#     -> { "status_code": 200, "data": { pay_address, pay_amount, checkout_url,
#                                        trade_id, pay_amount_cents, ... } }
# ----------------------------------------------------------------------------
echo "== POST /api/v1/order/create =="
CREATE_RESP="$(curl -s "${BASE_URL}/api/v1/order/create" \
  -H 'content-type: application/json' \
  -d @- <<JSON
{
  "api_key":   "${API_KEY}",
  "method":    "${METHOD}",
  "amount":    "${AMOUNT}",
  "order_id":  "${ORDER_ID}",
  "notify_url":"${NOTIFY_URL}",
  "signature": "${SIGNATURE}"
}
JSON
)"
echo "$CREATE_RESP"
echo

# Pull out the trade_id (jq if available, else a tiny sed fallback).
if command -v jq >/dev/null 2>&1; then
  TRADE_ID="$(printf '%s' "$CREATE_RESP" | jq -r '.data.trade_id')"
  echo "pay_address : $(printf '%s' "$CREATE_RESP" | jq -r '.data.pay_address')"
  echo "pay_amount  : $(printf '%s' "$CREATE_RESP" | jq -r '.data.pay_amount') USDT"
  echo "checkout    : $(printf '%s' "$CREATE_RESP" | jq -r '.data.checkout_url')"
else
  TRADE_ID="$(printf '%s' "$CREATE_RESP" | sed -n 's/.*"trade_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
fi
echo "trade_id    : ${TRADE_ID}"
echo

# ----------------------------------------------------------------------------
#  2) Query the order status (no signature needed on the read endpoint)
#     -> { "status_code": 200, "data": { status: "pending"|"paid"|"expired", ...}}
# ----------------------------------------------------------------------------
echo "== GET /api/v1/order/{trade_id} =="
curl -s "${BASE_URL}/api/v1/order/${TRADE_ID}"
echo; echo

# The payer-facing hosted checkout (open in a browser): a QR + live status page:
echo "checkout page: ${BASE_URL}/pay/${TRADE_ID}"
echo "status  JSON : ${BASE_URL}/pay/${TRADE_ID}/status"
echo

# ----------------------------------------------------------------------------
#  3) Verifying an inbound WEBHOOK
#  ---------------------------------------------------------------------------
#  When an order is paid the gateway POSTs JSON to your notify_url with an
#  `X-OPG-Signature` header (and a `signature` field), both HMAC-SHA256 over the
#  payload using the SAME scheme as above. To verify: take the received JSON,
#  drop the `signature` field, rebuild the sorted "k=v&..." string, HMAC it with
#  your secret, and constant-time compare against the header.
#
#  That "drop one field, re-sort, re-hash" step is fiddly in pure bash, so verify
#  in your app (see examples/flask_shop.py :: /webhook, which does exactly this
#  with optimus_gateway.security.sign_params). The one-liner equivalent, given a
#  canonical "k=v&..." string in $WEBHOOK_BASE, is:
#
#     printf '%s' "$WEBHOOK_BASE" | openssl dgst -sha256 -hmac "$API_SECRET" | awk '{print $NF}'
#
#  ...which must equal the request's X-OPG-Signature header.
# ----------------------------------------------------------------------------
echo "done."
