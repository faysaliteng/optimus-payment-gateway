#!/usr/bin/env python
"""
==============================================================================
 Optimus Payment Gateway  —  Telegram bot example  (TEMPLATE, copy & adapt)
==============================================================================

A minimal `python-telegram-bot` (v20+) bot that sells one thing for crypto:

    /buy   -> creates a payment, shows the payer the address + exact amount +
              a "Open checkout" button (hosted QR page), then DMs them the
              moment the on-chain payment is detected.

It talks to the gateway the SIMPLE way — as a LOCAL library, in-process:

    from optimus_gateway import init, create_payment, get_payment

That means this script IS the gateway: it needs the same environment the
gateway needs (at minimum `OPG_GATEWAY_XPUB`, or `OPG_SHARED_RECEIVE_ADDRESS`)
and you must ALSO run the watcher so payments actually get detected. Two ways:

  (a) run the full service separately:  `python run.py serve`
      (its watcher writes "paid" into the SAME OPG_DB_PATH this bot reads), or
  (b) start the background workers from inside this process — see
      `server/workers.py` :: start_background().

If instead your bot lives on a different machine from the gateway, use the REST
variant shown in `rest_create_payment()` below (commented) and receive results
via the webhook receiver sketched at the bottom — no shared DB needed.

Run it:

    pip install "python-telegram-bot>=20"        # plus: pip install -r requirements.txt
    export TELEGRAM_BOT_TOKEN=123456:abcdef...    # from @BotFather
    export OPG_GATEWAY_XPUB=xpub6C...             # your watch-only receiving xpub
    export OPG_ENABLED_METHODS=usdt_bep20
    export OPG_BASE_URL=https://pay.yourdomain.com   # used in the checkout link
    python examples/telegram_bot.py

Everything below is a template: swap in your product/pricing, your copy, your
fulfilment logic (deliver the goods where marked "TODO: fulfil").
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

# --- python-telegram-bot v20 ------------------------------------------------
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# --- the gateway, used as a local library -----------------------------------
from optimus_gateway import create_payment, get_payment, init

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("example.telegram_bot")

# --- what you're selling (edit me) ------------------------------------------
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]         # required
PAY_METHOD = os.getenv("OPG_ENABLED_METHODS", "usdt_bep20").split(",")[0].strip()
PRODUCT_NAME = "1 month of Optimus Pro"
PRICE_USD = 25.00

# How long to keep watching a single order before we give up polling it.
POLL_TIMEOUT_SECONDS = 45 * 60
POLL_EVERY_SECONDS = 8


# ---------------------------------------------------------------------------
#  /buy  — reserve a payment and show it to the user
# ---------------------------------------------------------------------------
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id

    # A stable, unique merchant order id. Passing it makes create_payment
    # idempotent: if the user taps /buy twice quickly you get the SAME order back
    # instead of two. We stash the chat_id in metadata so a webhook receiver (if
    # you use one) can find who to notify.
    merchant_order_id = f"tg-{user.id}-{int(time.time())}"

    # create_payment is synchronous (SQLite under the hood); run it off the event
    # loop so a slow disk never blocks the bot.
    order = await asyncio.to_thread(
        create_payment,
        PAY_METHOD,
        PRICE_USD,
        merchant_order_id=merchant_order_id,
        # notify_url=f"{os.getenv('OPG_BASE_URL','')}/tg-webhook",  # if using webhooks
        metadata={"telegram_chat_id": chat_id, "telegram_user_id": user.id},
    )
    # order is the public dict from gateway._public_order — the fields we use:
    #   trade_id, pay_address, pay_amount, pay_amount_cents, network, pay_memo,
    #   checkout_url, status, expires_at
    log.info("created order %s for @%s (%s)", order["trade_id"], user.username, order["network"])

    lines = [
        f"🧾 <b>{PRODUCT_NAME}</b>",
        f"Send exactly <b>{order['pay_amount']} USDT</b> on <b>{order['network']}</b>:",
        "",
        f"<code>{order['pay_address']}</code>",
    ]
    # TON (and only TON) uses a MEMO/comment instead of a per-order address — the
    # payer MUST include it or the payment can't be matched.
    if order.get("pay_memo"):
        lines += ["", f"⚠️ Include this MEMO / comment:", f"<code>{order['pay_memo']}</code>"]
    lines += ["", "The bot will message you automatically once it's confirmed."]

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("💳 Open checkout (QR)", url=order["checkout_url"])]]
    )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )

    # Kick off a background poll that DMs the user when the order is paid. Using
    # application.create_task ties the task to the bot's lifecycle (clean shutdown).
    context.application.create_task(
        _watch_until_paid(context, chat_id, order["trade_id"])
    )


# ---------------------------------------------------------------------------
#  Poll loop — the simplest way to learn a payment landed (no public URL needed)
# ---------------------------------------------------------------------------
async def _watch_until_paid(context: ContextTypes.DEFAULT_TYPE, chat_id: int, trade_id: str) -> None:
    """Poll get_payment(trade_id) until it is paid/expired or we time out.

    NOTE: this only works because the WATCHER is running against the same DB (see
    the module docstring). Polling get_payment on its own never changes status —
    the watcher is what credits orders. For production at scale prefer the webhook
    receiver (bottom of file); polling is perfect for a single-server bot.
    """
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_EVERY_SECONDS)
        status = await asyncio.to_thread(get_payment, trade_id)
        if not status:
            continue
        if status["status"] == "paid":
            await context.bot.send_message(
                chat_id,
                f"✅ Payment received — thanks!\nYour <b>{PRODUCT_NAME}</b> is now active.",
                parse_mode=ParseMode.HTML,
            )
            # TODO: fulfil the order here (grant access, deliver keys, etc.).
            #   trade_id / metadata let you tie this back to the buyer.
            log.info("order %s paid; fulfilled for chat %s", trade_id, chat_id)
            return
        if status["status"] == "expired":
            await context.bot.send_message(
                chat_id, "⌛ That payment window expired. Send /buy to try again."
            )
            return
    log.info("stopped polling order %s (timeout)", trade_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"Hi! Send /buy to purchase {PRODUCT_NAME} with crypto (USDT)."
    )


def main() -> None:
    # Initialise the gateway DB (idempotent). If you also want THIS process to run
    # the watcher/sweeper/webhook workers, uncomment:
    #     from server import workers; workers.start_background()
    init()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    log.info("bot up; selling %r via %s", PRODUCT_NAME, PAY_METHOD)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


# ============================================================================
#  VARIANT A — talk to a REMOTE gateway over REST instead of the local library
# ----------------------------------------------------------------------------
#  Use this when the bot and the gateway run on different machines. You sign the
#  request body with your shared secret exactly like the gateway verifies it
#  (optimus_gateway.security.sign_params: HMAC-SHA256 over the sorted, &-joined
#  "k=v" of the body, EXCLUDING the signature field and any empty values).
#
#  Gotcha: send numeric fields as STRINGS. The server signs the JSON-*parsed*
#  body, and `25.00` parses to the float 25.0 ("amount=25.0") which would break a
#  signature computed over "amount=25.00". Sending "25.00" keeps both sides equal.
#
#     import hashlib, hmac, httpx
#
#     GATEWAY_URL = "https://pay.yourdomain.com"
#     API_KEY     = os.environ["OPG_MERCHANT_API_KEY"]
#     API_SECRET  = os.environ["OPG_MERCHANT_API_SECRET"]
#
#     def _sign(secret: str, params: dict) -> str:
#         base = "&".join(
#             f"{k}={params[k]}" for k in sorted(params)
#             if k not in ("signature", "sign") and params[k] not in (None, "")
#         )
#         return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
#
#     async def rest_create_payment(amount_usd: float, order_id: str, notify_url: str) -> dict:
#         body = {
#             "api_key": API_KEY,
#             "method": PAY_METHOD,
#             "amount": f"{amount_usd:.2f}",   # STRING — see gotcha above
#             "order_id": order_id,
#             "notify_url": notify_url,
#         }
#         body["signature"] = _sign(API_SECRET, body)
#         async with httpx.AsyncClient(timeout=15) as client:
#             r = await client.post(f"{GATEWAY_URL}/api/v1/order/create", json=body)
#             r.raise_for_status()
#             return r.json()["data"]          # same public order dict as create_payment()
#
#     # Poll status (no signing needed on the read endpoint):
#     #   GET {GATEWAY_URL}/api/v1/order/{trade_id}  ->  {"data": {...}}


# ============================================================================
#  VARIANT B — receive a signed webhook instead of polling (aiohttp receiver)
# ----------------------------------------------------------------------------
#  Pass notify_url=<your public https URL> to create_payment / the REST body, and
#  the gateway POSTs a signed JSON callback the moment an order is paid. Verify
#  it with the SAME scheme, then DM the buyer (recover their chat_id from the
#  order metadata you set, or from your own DB keyed by merchant_order_id).
#
#     from aiohttp import web
#
#     async def webhook(request: web.Request) -> web.Response:
#         payload = await request.json()
#         header  = request.headers.get("X-OPG-Signature", "")
#         # sign_params ignores the 'signature' field inside payload, so recompute
#         # over the whole body and compare (constant-time).
#         expected = _sign(API_SECRET, payload)
#         if not hmac.compare_digest(header or payload.get("signature", ""), expected):
#             return web.Response(status=401, text="bad signature")
#         if payload.get("status") == "paid":
#             # look up the buyer's chat_id (from your store / the order metadata)
#             # and: await bot.send_message(chat_id, "✅ Payment received!")
#             ...
#         return web.Response(text="ok")       # 2xx => the gateway stops retrying
#
#     # app = web.Application(); app.router.add_post("/tg-webhook", webhook)
#     # web.run_app(app, port=8080)
