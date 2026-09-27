"""
Faction vault balance lookups and amount parsing for bank requests.
"""

from __future__ import annotations

import re


AMOUNT_RE = re.compile(r"^(\d+(?:\.\d+)?)([kmb])?$")
AMOUNT_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
ALL_AMOUNT_WORDS = {"all", "max", "everything"}


class BankBalanceError(ValueError):
    pass


def parse_bank_amount(value):
    """Return a positive int, the string "all", or None when the input is invalid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = int(value)
        return amount if amount > 0 else None
    text = re.sub(r"[,$\s_]", "", str(value or "")).lower()
    if text in ALL_AMOUNT_WORDS:
        return "all"
    match = AMOUNT_RE.match(text)
    if not match:
        return None
    amount = round(float(match.group(1)) * AMOUNT_MULTIPLIERS.get(match.group(2), 1))
    return amount if amount > 0 else None


def _member_balances(response):
    """Map member id -> vault money from a v2 faction/balance (or v1 donations) payload."""
    balances = {}
    if not isinstance(response, dict):
        return balances

    balance = response.get("balance")
    members = balance.get("members") if isinstance(balance, dict) else None
    if isinstance(members, list):
        for member in members:
            try:
                balances[int(member.get("id"))] = int(member.get("money") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
        return balances

    donations = response.get("donations")
    if isinstance(donations, dict):
        for member_id, entry in donations.items():
            try:
                balances[int(member_id)] = int((entry or {}).get("money_balance") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
    return balances


def lookup_member_balance(gateway, settings, requester_id: int, preferred_tag: str | None = None):
    """Find the tracked faction holding requester_id's vault balance. Returns (faction, money)."""
    if gateway is None:
        raise BankBalanceError("Faction balance lookup is unavailable (no Torn API gateway).")

    factions = [faction for faction in settings.list_factions() if faction.api_keys]
    if preferred_tag:
        preferred = str(preferred_tag).strip().upper()
        factions.sort(key=lambda faction: faction.tag != preferred)

    errors = []
    for faction in factions:
        try:
            response = gateway.faction_balance(pool=faction.tag)
        except Exception as exc:
            errors.append(f"{faction.tag}: {type(exc).__name__}")
            continue
        if isinstance(response, dict) and response.get("error"):
            error = response["error"]
            errors.append(f"{faction.tag}: {error.get('error') if isinstance(error, dict) else error}")
            continue
        balances = _member_balances(response)
        if int(requester_id) in balances:
            return faction, balances[int(requester_id)]

    if errors:
        raise BankBalanceError(f"Could not read faction balances ({'; '.join(errors)}).")
    raise BankBalanceError("You are not a member of any tracked faction.")


GAVE_RE = re.compile(r"\bgave\s+\$([\d,]+)\s+to\b", re.IGNORECASE)
WAS_GIVEN_RE = re.compile(r"\bwas\s+given\s+\$([\d,]+)\s+by\b", re.IGNORECASE)
XID_RE = re.compile(r"XID=(\d+)")
LINK_NAME_RE = re.compile(r">([^<]+)</a>")


def _parse_funds_entry(text):
    """Parse a give-to-user funds news line into giver/recipient/amount, or None."""
    text = str(text or "")
    match = GAVE_RE.search(text)
    if match:
        giver_part, recipient_part = text[:match.start()], text[match.end():]
    else:
        match = WAS_GIVEN_RE.search(text)
        if not match:
            return None
        recipient_part, giver_part = text[:match.start()], text[match.end():]

    recipient_id = XID_RE.search(recipient_part)
    if not recipient_id:
        return None
    giver_id = XID_RE.search(giver_part)
    giver_name = LINK_NAME_RE.search(giver_part)
    return {
        "amount": int(match.group(1).replace(",", "")),
        "recipient_id": int(recipient_id.group(1)),
        "giver_id": int(giver_id.group(1)) if giver_id else None,
        "giver_name": giver_name.group(1).strip() if giver_name else None,
    }


def find_vault_payment(response, requester_id: int, since: int, expected_amount: int | None = None):
    """Find a vault payout to requester_id at/after `since` in a fundsnews (v1) or news (v2) payload."""
    if not isinstance(response, dict):
        return None
    entries = response.get("fundsnews")
    if isinstance(entries, dict):
        items = [(entry.get("news"), entry.get("timestamp")) for entry in entries.values() if isinstance(entry, dict)]
    elif isinstance(response.get("news"), list):
        items = [(entry.get("text"), entry.get("timestamp")) for entry in response["news"] if isinstance(entry, dict)]
    else:
        return None

    payments = []
    for text, timestamp in items:
        try:
            timestamp = int(timestamp or 0)
        except (TypeError, ValueError):
            continue
        if timestamp < int(since):
            continue
        parsed = _parse_funds_entry(text)
        if parsed and parsed["recipient_id"] == int(requester_id):
            parsed["timestamp"] = timestamp
            payments.append(parsed)

    if not payments:
        return None
    payments.sort(key=lambda payment: (payment["amount"] != expected_amount, payment["timestamp"]))
    return payments[0]


def resolve_withdrawal(gateway, settings, requester_id: int, amount_input, preferred_tag: str | None = None):
    """Resolve the requester's faction and cap the requested amount to their vault balance."""
    parsed = parse_bank_amount(amount_input)
    if parsed is None:
        raise BankBalanceError("Enter a valid amount, e.g. 5000000, 1.5m, 250k or all.")

    faction, balance = lookup_member_balance(gateway, settings, requester_id, preferred_tag=preferred_tag)
    if balance <= 0:
        raise BankBalanceError(f"You have no money in the {faction.name} vault.")

    amount = balance if parsed == "all" else min(parsed, balance)
    return {
        "faction": faction,
        "balance": balance,
        "amount": amount,
        "requested_text": str(amount_input)[:32],
        "capped": parsed != "all" and parsed > balance,
    }
