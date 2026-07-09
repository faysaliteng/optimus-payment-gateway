"""
REST API + hosted checkout (FastAPI).

Merchant API (server-to-server, signed):
  POST /api/v1/order/create   -> reserve a payment, get address/amount/checkout_url
  GET  /api/v1/order/{trade}  -> query status

Payer-facing:
  GET  /pay/{trade}           -> hosted checkout page (address, amount, live QR, poll)
  GET  /pay/{trade}/status    -> JSON status (front-end polls this)
  GET  /pay/{trade}/qr.png    -> QR image for the payment

Ops:
  GET  /health                -> config summary + chain reachability

Auth: merchants send api_key + an HMAC-SHA256 `signature` over the request fields
(see optimus_gateway.security.sign_params). Our webhooks are signed the same way.
"""
from __future__ import annotations

import io

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

import optimus_gateway as opg
from optimus_gateway import config, gateway
from optimus_gateway.security import verify_params
from . import workers

app = FastAPI(title="Optimus Payment Gateway", version=opg.__version__)


@app.on_event("startup")
def _startup():
    opg.init()
    workers.start_background()


def _require_merchant(body: dict):
    if not config.MERCHANT_API_KEY:
        return  # auth disabled (single-tenant / trusted network)
    if str(body.get("api_key")) != config.MERCHANT_API_KEY:
        raise HTTPException(401, "bad api_key")
    if not verify_params(config.MERCHANT_API_SECRET, body):
        raise HTTPException(401, "bad signature")


@app.post("/api/v1/order/create")
async def create_order(request: Request):
    body = await request.json()
    _require_merchant(body)
    method = str(body.get("method") or "").strip()
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a number")
    try:
        order = gateway.create_payment(
            method, amount,
            merchant_order_id=body.get("order_id") or body.get("merchant_order_id"),
            notify_url=body.get("notify_url"),
            redirect_url=body.get("redirect_url"),
            metadata=body.get("metadata"),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {"status_code": 200, "data": order}


@app.get("/api/v1/order/{trade_id}")
async def query_order(trade_id: str):
    order = gateway.get_payment(trade_id)
    if not order:
        raise HTTPException(404, "order not found")
    return {"status_code": 200, "data": order}


@app.get("/pay/{trade_id}/status")
async def pay_status(trade_id: str):
    order = gateway.get_payment(trade_id)
    if not order:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"trade_id": trade_id, "status": order["status"], "received_cents": order["received_cents"],
            "expected_cents": order["pay_amount_cents"]}


@app.get("/pay/{trade_id}/qr.png")
async def pay_qr(trade_id: str):
    order = gateway.get_payment(trade_id)
    if not order:
        raise HTTPException(404, "order not found")
    payload = order["pay_address"]
    try:
        import segno
        buf = io.BytesIO()
        segno.make(payload, error="m").save(buf, kind="png", scale=6, border=2)
        return Response(buf.getvalue(), media_type="image/png")
    except Exception:  # noqa: BLE001
        raise HTTPException(500, "qr unavailable (pip install segno)")


@app.get("/pay/{trade_id}", response_class=HTMLResponse)
async def checkout(trade_id: str):
    order = gateway.get_payment(trade_id)
    if not order:
        raise HTTPException(404, "order not found")
    return _CHECKOUT_HTML.replace("__TRADE__", trade_id).replace(
        "__ADDR__", order["pay_address"] or "").replace(
        "__AMOUNT__", order["pay_amount"]).replace(
        "__NETWORK__", order["network"]).replace(
        "__MEMO__", order.get("pay_memo") or "").replace(
        "__STATUS__", order["status"])


@app.get("/health")
async def health():
    return {"ok": True, "version": opg.__version__, "config": config.summary()}


_CHECKOUT_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Pay __AMOUNT__ USDT</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e8eefc;margin:0;
   display:flex;min-height:100vh;align-items:center;justify-content:center}
 .card{background:#131a30;border:1px solid #24304f;border-radius:18px;padding:28px;max-width:420px;width:92%;
   box-shadow:0 20px 60px rgba(0,0,0,.4)}
 h1{font-size:19px;margin:0 0 4px}.sub{color:#8ea2c9;font-size:13px;margin-bottom:18px}
 .amt{font-size:30px;font-weight:800;letter-spacing:.5px}.net{color:#7ee0a1;font-weight:700;font-size:13px}
 img{width:190px;height:190px;background:#fff;border-radius:12px;display:block;margin:16px auto;padding:6px}
 .addr{background:#0b1020;border:1px solid #24304f;border-radius:10px;padding:10px 12px;font-family:ui-monospace,monospace;
   font-size:12.5px;word-break:break-all;cursor:pointer}
 .memo{margin-top:8px}.lbl{color:#8ea2c9;font-size:11px;text-transform:uppercase;letter-spacing:.4px;margin:12px 0 4px}
 .pill{display:inline-block;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:700}
 .wait{background:#3a2f10;color:#f5c451}.paid{background:#123a24;color:#5fe39a}
 .hint{color:#6c7ea6;font-size:11.5px;margin-top:14px;line-height:1.6}
</style></head><body>
<div class=card>
 <h1>Send exactly <span class=amt>__AMOUNT__</span> USDT</h1>
 <div class=sub>Network: <span class=net>__NETWORK__</span></div>
 <img src="/pay/__TRADE__/qr.png" alt="QR">
 <div class=lbl>Pay to address</div>
 <div class=addr id=addr onclick="cp(this.textContent)">__ADDR__</div>
 <div class=memo id=memoBox style="display:none">
   <div class=lbl>MEMO / Comment (required!)</div>
   <div class=addr onclick="cp('__MEMO__')">__MEMO__</div>
 </div>
 <div class=lbl>Status</div>
 <span id=st class="pill wait">Waiting for payment…</span>
 <div class=hint>Send the <b>exact</b> amount on the <b>__NETWORK__</b> network only. This page updates
   automatically when your payment is detected. Do not close it.</div>
</div>
<script>
 function cp(t){navigator.clipboard&&navigator.clipboard.writeText(t)}
 async function poll(){try{const r=await fetch('/pay/__TRADE__/status');const j=await r.json();
   const st=document.getElementById('st');
   if(j.status==='paid'){st.className='pill paid';st.textContent='✔ Payment received';return}
   if(j.status==='expired'){st.textContent='Expired — start a new payment';return}
   st.textContent='Waiting… ('+ (j.received_cents/100).toFixed(2) +' / '+(j.expected_cents/100).toFixed(2)+' received)';
 }catch(e){}setTimeout(poll,4000)}
 document.getElementById('memoBox').style.display='__MEMO__'?'block':'none';
 poll();
</script></body></html>"""
