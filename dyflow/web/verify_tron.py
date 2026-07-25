"""Xac minh giao dich USDT TRC20 tren chain (Tronscan API).

Khi user gui TX hash:
- TX confirmed / success
- To = CRYPTO_WALLET_ADDRESS
- Token = USDT (contract chinh thuc)
- So tien >= so tien goi (cho phep le sai nho)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..utils import log

# USDT chinh thuc tren Tron (TRC20)
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONSCAN_TX_URL = "https://apilist.tronscanapi.com/api/transaction-info"


@dataclass
class VerifyResult:
    ok: bool
    reason: str
    amount_usdt: float = 0.0
    to_address: str = ""
    symbol: str = ""


def _http_get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "dyflow/1.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _norm_addr(addr: str) -> str:
    return (addr or "").strip()


def verify_usdt_trc20_tx(
    tx_hash: str,
    *,
    expected_to: str,
    expected_usd: float,
    amount_tolerance: float = 0.05,
) -> VerifyResult:
    """Kiem tra TX tren Tronscan. Tra ve VerifyResult."""
    tx_hash = (tx_hash or "").strip()
    expected_to = _norm_addr(expected_to)
    if len(tx_hash) < 16:
        return VerifyResult(False, "TX hash khong hop le")
    if not expected_to:
        return VerifyResult(False, "Chua cau hinh vi nhan")

    url = f"{TRONSCAN_TX_URL}?{urllib.parse.urlencode({'hash': tx_hash})}"
    try:
        data = _http_get_json(url)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        log.warning("Tronscan HTTP %s: %s", e.code, detail)
        return VerifyResult(False, f"Khong doc duoc TX tu Tronscan (HTTP {e.code})")
    except Exception as e:  # noqa: BLE001
        log.warning("Tronscan loi: %s", e)
        return VerifyResult(False, f"Loi ket noi Tronscan: {e}")

    if not data or (not data.get("hash") and not data.get("contractRet")):
        # Mot so TX chua index / hash sai -> body rong
        return VerifyResult(False, "Khong tim thay giao dich (TX sai hoac chua xac nhan)")

    # Trang thai
    contract_ret = str(data.get("contractRet") or data.get("result") or "").upper()
    confirmed = bool(data.get("confirmed", True))
    if contract_ret and contract_ret not in {"SUCCESS", "OK", ""}:
        return VerifyResult(False, f"TX that bai tren chain ({contract_ret})")
    if data.get("confirmed") is False:
        return VerifyResult(False, "TX chua confirmed — thu lai sau vai phut")

    transfers = data.get("trc20TransferInfo") or data.get("tokenTransferInfo") or []
    if isinstance(transfers, dict):
        transfers = [transfers]
    if not transfers:
        return VerifyResult(False, "TX khong phai chuyen TRC20 (khong thay USDT transfer)")

    expected_contract = (os.getenv("CRYPTO_USDT_CONTRACT") or USDT_TRC20_CONTRACT).strip()
    matches: list[dict] = []
    for t in transfers:
        to_addr = _norm_addr(t.get("to_address") or t.get("toAddress") or t.get("to") or "")
        contract = _norm_addr(
            t.get("contract_address") or t.get("contractAddress") or t.get("token_address") or ""
        )
        symbol = str(t.get("symbol") or t.get("tokenAbbr") or "").upper()
        if to_addr != expected_to:
            continue
        if contract and contract != expected_contract and symbol not in {"USDT", ""}:
            # cho phep neu symbol=USDT va to dung
            if symbol != "USDT":
                continue
        if symbol and symbol != "USDT":
            continue
        matches.append(t)

    if not matches:
        return VerifyResult(
            False,
            f"Khong thay chuyen USDT toi vi {expected_to[:8]}... (sai dia chi / sai token)",
        )

    # Cong so tien cac transfer USDT toi vi.
    # Tronscan hien tai dung amount_str; ban cu dung quant / amount (integer theo decimals).
    total = 0.0
    for t in matches:
        decimals = int(t.get("decimals") or t.get("tokenDecimal") or 6)
        raw = str(t.get("amount_str") or t.get("quant") or t.get("amount") or "0")
        try:
            quant = float(raw)
        except ValueError:
            continue
        total += quant / (10 ** decimals)

    need = float(expected_usd)
    if total + 1e-9 < need - amount_tolerance:
        return VerifyResult(
            False,
            f"So tien thieu: nhan {total:.4f} USDT, can {need:.2f} USDT",
            amount_usdt=total,
            to_address=expected_to,
            symbol="USDT",
        )

    return VerifyResult(
        True,
        f"OK: nhan {total:.4f} USDT",
        amount_usdt=total,
        to_address=expected_to,
        symbol="USDT",
    )
